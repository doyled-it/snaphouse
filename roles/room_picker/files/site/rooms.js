// The room picker page controller: the DOM and network glue around the pure
// model in rooms-model.js.
//
// It loads the room list from rooms.json, then keeps a live picture of the house
// from snapserver over a JSON-RPC WebSocket at /jsonrpc, re-reading the full
// status whenever snapserver notifies a change. If the socket will not stay up
// it falls back to polling Server.GetStatus every 30 seconds. A status reply
// that will not parse never breaks the page: it keeps the last good render and
// shows a quiet notice. Toggles and the volume sliders send Client.SetVolume
// back the same way. A health line polls each machine's status file.

import {
  roomsFromStatus,
  nowPlaying,
  toggleRequest,
  volumeRequest,
  groupUnmuteRequest,
  healthBanner,
  artFromHistory,
  safeJsonParse,
} from "./rooms-model.js";

// The fallback poll is deliberately slow: it only runs when the live WebSocket
// is down, and each Server.GetStatus is a full status read, so a slow cadence
// keeps that cost off snapserver.
const POLL_MS = 30000;
const HEALTH_MS = 30000;
const RECONNECT_MS = 3000;
const SLIDER_DEBOUNCE_MS = 150;

// Shown on the connection line when a status reply will not parse. The page
// keeps its last good render and says so quietly rather than breaking.
const STATUS_UNAVAILABLE_MSG = "Status unavailable; showing the last update.";
// Shown while the live WebSocket is down and the page is polling instead.
const OFFLINE_MSG = "Live updates are offline; refreshing every 30 seconds.";

const els = {
  nowPlaying: document.getElementById("now-playing"),
  health: document.getElementById("health"),
  rooms: document.getElementById("rooms"),
  connection: document.getElementById("connection"),
  art: document.getElementById("art"),
  artCurrent: document.getElementById("art-current"),
  artRecentHeading: document.getElementById("art-recent-heading"),
  artHistory: document.getElementById("art-history"),
};

const state = {
  config: { rooms: [] },
  cards: new Map(), // room id -> { card, toggle, slider, dragging }
  rooms: new Map(), // room id -> latest row from roomsFromStatus
  socket: null,
  pollTimer: null,
  reconnectTimer: null,
  nextId: 1,
};

// The JSON-RPC endpoint and its WebSocket twin, both relative to this page so
// the proxy decides where snapserver actually is.
const rpcUrl = new URL("jsonrpc", window.location.href);
const wsUrl = (() => {
  const url = new URL("jsonrpc", window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
})();

start();

async function start() {
  await loadConfig();
  buildCards();
  connect();
  refreshHealth();
  refreshArt();
  setInterval(refreshHealth, HEALTH_MS);
  setInterval(refreshArt, HEALTH_MS);

  // A phone that was asleep or backgrounded comes back with a dead socket and
  // stale cards. On return to the foreground, reconnect if needed, re-read the
  // full status, and refresh the health line so the page is current at a glance.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") {
      return;
    }
    const socket = state.socket;
    if (!socket || socket.readyState === WebSocket.CLOSED) {
      // The socket died while backgrounded; bring it back now.
      connect();
    } else if (socket.readyState === WebSocket.OPEN) {
      // Still up: just re-read, the picture may be stale.
      requestStatus();
    }
    // CONNECTING or CLOSING: leave the in-flight transition alone.
    refreshHealth();
    refreshArt();
  });
}

async function loadConfig() {
  try {
    const response = await fetch("rooms.json", { cache: "no-store" });
    state.config = await response.json();
  } catch (err) {
    setConnection("Could not load the room list.");
  }
}

// One card per room, built once so a slider the user is dragging is never torn
// out from under them. Later updates only change values and classes.
function buildCards() {
  els.rooms.replaceChildren();
  state.cards.clear();
  for (const room of state.config.rooms) {
    const card = document.createElement("section");
    card.className = "room";

    const label = document.createElement("div");
    label.className = "room-label";
    label.textContent = room.label;

    const toggle = document.createElement("button");
    toggle.className = "toggle";
    toggle.type = "button";
    toggle.setAttribute("role", "switch");
    toggle.setAttribute("aria-label", `${room.label} on or off`);
    toggle.addEventListener("click", () => onToggle(room.id));

    const slider = document.createElement("input");
    slider.className = "volume";
    slider.type = "range";
    slider.min = "0";
    slider.max = "100";
    slider.step = "1";
    slider.setAttribute("aria-label", `${room.label} volume`);

    const entry = { card, toggle, slider, dragging: false, pending: null };

    slider.addEventListener("input", () => {
      entry.dragging = true;
      onVolume(room.id, Number(slider.value), entry);
    });
    const release = () => {
      entry.dragging = false;
    };
    slider.addEventListener("change", release);
    slider.addEventListener("pointerup", release);
    slider.addEventListener("blur", release);

    card.append(label, toggle, slider);
    els.rooms.append(card);
    state.cards.set(room.id, entry);
  }
}

function connect() {
  clearTimeout(state.reconnectTimer);
  let socket;
  try {
    socket = new WebSocket(wsUrl);
  } catch (err) {
    startPolling();
    scheduleReconnect();
    return;
  }
  state.socket = socket;

  socket.addEventListener("open", () => {
    stopPolling();
    setConnection("");
    requestStatus();
  });
  socket.addEventListener("message", (event) => {
    onMessage(event.data);
  });
  socket.addEventListener("close", () => {
    state.socket = null;
    startPolling();
    scheduleReconnect();
  });
  socket.addEventListener("error", () => {
    // close fires after error; the fallback and reconnect are handled there.
    socket.close();
  });
}

function scheduleReconnect() {
  clearTimeout(state.reconnectTimer);
  state.reconnectTimer = setTimeout(connect, RECONNECT_MS);
}

function onMessage(data) {
  const message = safeJsonParse(data);
  if (message === null) {
    // The body would not parse (for example raw control characters snapserver
    // emitted inline). Keep the last good render and say so quietly.
    setConnection(STATUS_UNAVAILABLE_MSG);
    return;
  }
  const messages = Array.isArray(message) ? message : [message];
  for (const one of messages) {
    // A response to our Server.GetStatus carries the whole picture.
    if (one.result && one.result.server) {
      clearStatusMessage();
      render(one.result);
      continue;
    }
    // Any notification means something changed; re-read the full status rather
    // than track every event shape.
    if (one.method && one.id === undefined) {
      requestStatus();
    }
  }
}

// Restore the baseline connection line after a good read: empty when the live
// socket is up, the offline notice while polling.
function clearStatusMessage() {
  setConnection(state.pollTimer ? OFFLINE_MSG : "");
}

// Ask snapserver for the whole status, over the socket when it is open, else by
// a plain POST (also the polling path).
function requestStatus() {
  if (state.socket && state.socket.readyState === WebSocket.OPEN) {
    state.socket.send(envelope("Server.GetStatus", {}));
    return;
  }
  postRpc("Server.GetStatus", {})
    .then((result) => {
      if (result && result.server) {
        clearStatusMessage();
        render(result);
      }
    })
    .catch((err) => {
      // A reply that would not parse is not the same as snapserver being gone:
      // keep the last good render and show the quiet notice. Any other failure
      // (network, HTTP error) is reported as unreachable.
      if (err && err.parseFailed) {
        setConnection(STATUS_UNAVAILABLE_MSG);
      } else {
        setConnection("snapserver is not reachable.");
      }
    });
}

function envelope(method, params) {
  return JSON.stringify({ id: state.nextId++, jsonrpc: "2.0", method, params });
}

async function postRpc(method, params) {
  const response = await fetch(rpcUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: envelope(method, params),
  });
  // Parse the body ourselves (not response.json()) so a malformed payload is a
  // tagged, recoverable failure rather than an opaque throw.
  const body = safeJsonParse(await response.text());
  if (body === null) {
    const err = new Error("unparseable JSON-RPC response");
    err.parseFailed = true;
    throw err;
  }
  if (body.error) {
    throw new Error(body.error.message || "JSON-RPC error");
  }
  return body.result;
}

function sendControl(request) {
  if (state.socket && state.socket.readyState === WebSocket.OPEN) {
    state.socket.send(envelope(request.method, request.params));
    return;
  }
  postRpc(request.method, request.params)
    .then(() => requestStatus())
    .catch(() => setConnection("Could not reach snapserver."));
}

function startPolling() {
  if (state.pollTimer) {
    return;
  }
  setConnection(OFFLINE_MSG);
  requestStatus();
  state.pollTimer = setInterval(requestStatus, POLL_MS);
}

function stopPolling() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

// Paint the whole picture: what is playing and every room's state.
function render(status) {
  const playing = nowPlaying(status);
  renderNowPlaying(playing);

  const rooms = roomsFromStatus(status, state.config.rooms);
  for (const room of rooms) {
    state.rooms.set(room.id, room);
    const entry = state.cards.get(room.id);
    if (!entry) {
      continue;
    }
    entry.card.classList.toggle("off", !room.on);
    entry.card.classList.toggle("disconnected", !room.connected);
    entry.toggle.setAttribute("aria-checked", room.on ? "true" : "false");
    entry.toggle.textContent = room.on ? "On" : "Off";
    entry.toggle.disabled = !room.connected;
    // Do not fight a finger on the slider.
    if (!entry.dragging) {
      entry.slider.value = String(room.volume);
    }
    entry.slider.disabled = !room.connected;
  }
}

function renderNowPlaying(playing) {
  els.nowPlaying.replaceChildren();
  if (!playing) {
    const idle = document.createElement("span");
    idle.className = "np-idle";
    idle.textContent = "Nothing playing";
    els.nowPlaying.append(idle);
    return;
  }
  const title = document.createElement("span");
  title.className = "np-title";
  title.textContent = playing.title;
  els.nowPlaying.append(title);
  if (playing.artist) {
    const artist = document.createElement("span");
    artist.className = "np-artist";
    artist.textContent = playing.artist;
    els.nowPlaying.append(artist);
  }
}

function onToggle(id) {
  const entry = state.cards.get(id);
  if (!entry) {
    return;
  }
  const nowOn = entry.toggle.getAttribute("aria-checked") === "true";
  const next = !nowOn;
  // Optimistic: flip the control now, snapserver's notification confirms it.
  entry.toggle.setAttribute("aria-checked", next ? "true" : "false");
  entry.toggle.textContent = next ? "On" : "Off";
  entry.card.classList.toggle("off", !next);

  const room = state.rooms.get(id);
  // Turning a room on whose group is muted must unmute the group too, or the
  // client unmute alone leaves it silent and the control snaps back to Off.
  if (next && room && room.groupMuted && room.groupId) {
    sendControl(groupUnmuteRequest(room.groupId));
  }
  sendControl(toggleRequest(id, next));
}

function onVolume(id, percent, entry) {
  clearTimeout(entry.pending);
  entry.pending = setTimeout(() => {
    sendControl(volumeRequest(id, percent));
  }, SLIDER_DEBOUNCE_MS);
}

async function refreshHealth() {
  // One same-origin file the hub writes every 60 s. The browser never resolves
  // other machines' .local names (Android Chrome often cannot), so all the
  // cross-machine fetching happens on the hub, not here.
  let entries;
  try {
    const response = await fetch("house-status.json", { cache: "no-store" });
    const doc = await response.json();
    entries = (doc.machines || []).map((machine) => ({
      host: machine.name,
      status: machine.status,
    }));
  } catch (err) {
    // The hub itself is unreachable; leave the banner as it was rather than
    // flashing a false all-clear or a false alarm.
    return;
  }
  const message = healthBanner(entries);
  if (message) {
    els.health.textContent = message;
    els.health.hidden = false;
  } else {
    els.health.hidden = true;
    els.health.textContent = "";
  }
}

// The art on the nightstand frame, refreshed on the health cadence. Like the
// health file, it is one same-origin file the hub writes; the browser never
// resolves the frame's .local name. A missing file (404) hides the section; a
// transient hub error leaves the last render in place rather than flashing the
// section away and back.
async function refreshArt() {
  let art;
  try {
    const response = await fetch("art-history.json", { cache: "no-store" });
    if (!response.ok) {
      renderArt({ current: null, history: [] });
      return;
    }
    art = artFromHistory(await response.json());
  } catch (err) {
    return;
  }
  renderArt(art);
}

function renderArt(art) {
  const current = art.current;
  // The producer keeps current as history[0]; showing it both as the hero and as
  // the first thumbnail would look like a bug, so the "Recently shown" list is
  // the pieces other than the current one.
  const recent = art.history.filter((piece) => !samePiece(piece, current));

  if (!current && recent.length === 0) {
    els.art.hidden = true;
    els.artCurrent.replaceChildren();
    els.artHistory.replaceChildren();
    els.artRecentHeading.hidden = true;
    return;
  }

  els.art.hidden = false;
  renderArtCurrent(current);

  els.artHistory.replaceChildren();
  for (const piece of recent) {
    els.artHistory.append(historyItem(piece));
  }
  els.artRecentHeading.hidden = recent.length === 0;
}

function samePiece(a, b) {
  return Boolean(a && b && a.file === b.file && a.shown_at === b.shown_at);
}

function renderArtCurrent(current) {
  els.artCurrent.replaceChildren();
  if (!current) {
    return;
  }
  const figure = document.createElement("figure");
  figure.className = "art-figure";

  const img = document.createElement("img");
  img.className = "art-image";
  img.src = artUrl(current.file);
  img.alt = current.title;
  figure.append(img);

  const caption = document.createElement("figcaption");
  caption.className = "art-caption";
  caption.append(titleNode(current, "art-title"));
  if (current.artist) {
    const artist = document.createElement("span");
    artist.className = "art-artist";
    artist.textContent = current.artist;
    caption.append(artist);
  }
  figure.append(caption);
  els.artCurrent.append(figure);
}

function historyItem(piece) {
  const li = document.createElement("li");
  li.className = "art-item";

  const thumb = document.createElement("img");
  thumb.className = "art-thumb";
  thumb.src = artUrl(piece.file);
  thumb.alt = piece.title;
  thumb.loading = "lazy";
  thumb.decoding = "async";
  li.append(thumb);

  const meta = document.createElement("div");
  meta.className = "art-meta";
  meta.append(titleNode(piece, "art-item-title"));
  if (piece.artist) {
    const artist = document.createElement("span");
    artist.className = "art-item-artist";
    artist.textContent = piece.artist;
    meta.append(artist);
  }
  const when = relativeTime(piece.shown_at);
  if (when) {
    const time = document.createElement("time");
    time.className = "art-when";
    time.dateTime = piece.shown_at;
    time.textContent = when;
    meta.append(time);
  }
  li.append(meta);
  return li;
}

// The title, as a link to the museum page when the piece carries an https
// source_url, otherwise plain text. The URL is only ever set as an href (never
// interpolated into markup), and the link opens detached from this page.
function titleNode(piece, className) {
  if (piece.source_url) {
    const link = document.createElement("a");
    link.className = className;
    link.textContent = piece.title;
    link.href = piece.source_url;
    link.target = "_blank";
    link.rel = "noopener";
    return link;
  }
  const span = document.createElement("span");
  span.className = className;
  span.textContent = piece.title;
  return span;
}

// The same-origin path to an art-share image. encodeURIComponent keeps a file
// name with spaces or reserved characters a single path segment (the model has
// already rejected any name with a separator).
function artUrl(file) {
  return `art/${encodeURIComponent(file)}`;
}

// A short, human relative time for when a piece was shown. Returns "" for an
// unparseable stamp so the page simply omits it.
function relativeTime(iso) {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) {
    return "";
  }
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 45) {
    return "just now";
  }
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) {
    return `${minutes} min ago`;
  }
  const hours = Math.round(minutes / 60);
  if (hours < 24) {
    return `${hours} hr ago`;
  }
  const days = Math.round(hours / 24);
  if (days < 7) {
    return `${days} day${days === 1 ? "" : "s"} ago`;
  }
  return new Date(then).toLocaleDateString();
}

function setConnection(message) {
  els.connection.textContent = message;
  els.connection.hidden = message === "";
}
