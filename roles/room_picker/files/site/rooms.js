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
  toggleRequest,
  volumeRequest,
  groupUnmuteRequest,
  healthBanner,
  artFromHistory,
  nowPlayingFromBody,
  progressAt,
  formatDuration,
  historyFromBody,
  interpretRpcResponse,
  safeJsonParse,
  parseArtIndex,
  parseArtControl,
  piecesInFolder,
  rotationSummary,
  intervalOptions,
  apiRequest,
} from "./rooms-model.js";

// The fallback poll is deliberately slow: it only runs when the live WebSocket
// is down, and each Server.GetStatus is a full status read, so a slow cadence
// keeps that cost off snapserver.
const POLL_MS = 30000;
const HEALTH_MS = 30000;
const RECONNECT_MS = 3000;
const SLIDER_DEBOUNCE_MS = 150;
// The hub writes now-playing.json continuously and play-history.json only when a
// play lands, so the card polls quickly and the history slowly. The card's
// progress bar ticks client-side every second from the position anchor.
const NOW_PLAYING_MS = 5000;
const HISTORY_MS = 30000;
const TICK_MS = 1000;
// The art library documents the admin service publishes, refreshed on the same
// slow cadence as the frame history and re-read after every manager action.
const LIBRARY_MS = 30000;
// The default library filter: every folder.
const ALL_FOLDERS = "all";
// How long the manager's status line lingers before it clears itself.
const ART_MESSAGE_MS = 6000;

// Shown on the connection line when a status reply will not parse. The page
// keeps its last good render and says so quietly rather than breaking.
const STATUS_UNAVAILABLE_MSG = "Status unavailable; showing the last update.";
// Shown while the live WebSocket is down and the page is polling instead. The
// cadence is derived from POLL_MS so the text cannot drift from the real poll.
const OFFLINE_MSG = `Live updates are offline; refreshing every ${POLL_MS / 1000} seconds.`;

const els = {
  health: document.getElementById("health"),
  rooms: document.getElementById("rooms"),
  connection: document.getElementById("connection"),
  art: document.getElementById("art"),
  artCurrent: document.getElementById("art-current"),
  artRecentHeading: document.getElementById("art-recent-heading"),
  artHistory: document.getElementById("art-history"),
  npCard: document.getElementById("np-card"),
  npArtImg: document.getElementById("np-art-img"),
  npArtPh: document.getElementById("np-art-ph"),
  npTitle: document.getElementById("np-title"),
  npArtist: document.getElementById("np-artist"),
  npAlbum: document.getElementById("np-album"),
  npProgress: document.getElementById("np-progress"),
  npFill: document.getElementById("np-fill"),
  npElapsed: document.getElementById("np-elapsed"),
  npEnds: document.getElementById("np-ends"),
  npStatus: document.getElementById("np-status"),
  npHistory: document.getElementById("np-history"),
  npHistoryList: document.getElementById("np-history-list"),
  artNowshowing: document.getElementById("art-nowshowing"),
  artBack: document.getElementById("art-back"),
  artNext: document.getElementById("art-next"),
  artMessage: document.getElementById("art-message"),
  artManager: document.getElementById("art-manager"),
  artRotationSummary: document.getElementById("art-rotation-summary"),
  artInterval: document.getElementById("art-interval"),
  artFolderChips: document.getElementById("art-folder-chips"),
  artFolderFilter: document.getElementById("art-folder-filter"),
  artGrid: document.getElementById("art-grid"),
  artGridEmpty: document.getElementById("art-grid-empty"),
  artUploadFolder: document.getElementById("art-upload-folder"),
  artUploadInput: document.getElementById("art-upload-input"),
  artUploadProgress: document.getElementById("art-upload-progress"),
  artFolderCreate: document.getElementById("art-folder-create"),
  artFolderName: document.getElementById("art-folder-name"),
  artFolderList: document.getElementById("art-folder-list"),
  artTrash: document.getElementById("art-trash"),
  artTrashList: document.getElementById("art-trash-list"),
  artTrashEmpty: document.getElementById("art-trash-empty"),
};

const state = {
  config: { rooms: [] },
  cards: new Map(), // room id -> { card, toggle, slider, dragging }
  rooms: new Map(), // room id -> latest row from roomsFromStatus
  socket: null,
  pollTimer: null,
  reconnectTimer: null,
  nextId: 1,
  // The last parsed now-playing track (or null), the card's progress bar ticks
  // from it each second, and the cover src last set, so the image is only
  // swapped when it changes rather than reloaded on every poll.
  nowTrack: null,
  npArtSrc: null,
  // The parsed art library and rotation control (null until the admin service's
  // documents have been read at least once), the folder the library grid is
  // filtered to, whether the interval selector has been populated, and the timer
  // that clears the manager's status line.
  artIndex: null,
  artControl: null,
  artHasHistory: false,
  artFilter: ALL_FOLDERS,
  artIntervalBuilt: false,
  artMessageTimer: null,
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
  wireArtManager();
  refreshHealth();
  refreshArt();
  refreshLibrary();
  refreshNowPlaying();
  refreshHistory();
  setInterval(refreshHealth, HEALTH_MS);
  setInterval(refreshArt, HEALTH_MS);
  setInterval(refreshLibrary, LIBRARY_MS);
  setInterval(refreshNowPlaying, NOW_PLAYING_MS);
  setInterval(refreshHistory, HISTORY_MS);
  // The card's elapsed time and bar advance between polls, driven only by the
  // local clock and the last position anchor.
  setInterval(tickNowPlaying, TICK_MS);

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
    refreshLibrary();
    refreshNowPlaying();
    refreshHistory();
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
  // Interpret the reply in the model: a non-ok status (a proxy error page when
  // snapserver is down) is the unreachable path, while an ok reply that will not
  // parse is a tagged parse failure. This keeps "not reachable" and "status
  // unavailable" distinct.
  return interpretRpcResponse(response.ok, await response.text());
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

// Paint every room's state. The now-playing card and history are driven by the
// hub's own files (refreshNowPlaying / refreshHistory), not this /jsonrpc read.
function render(status) {
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

  renderArtCurrent(current);
  els.artHistory.replaceChildren();
  for (const piece of recent) {
    els.artHistory.append(historyItem(piece));
  }
  els.artRecentHeading.hidden = recent.length === 0;
  state.artHasHistory = Boolean(current) || recent.length > 0;
  updateArtSection();
}

// The "On the frame" section is shown when there is a frame piece or history to
// show, or when the admin service's library is available (the manager). The
// now-showing nav (Back and Next) rides with the manager, since it issues
// rotation commands only that service answers.
function updateArtSection() {
  const managerActive = state.artIndex !== null;
  els.art.hidden = !state.artHasHistory && !managerActive;
  els.artNowshowing.hidden = !managerActive;
  els.artManager.hidden = !managerActive;
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

// The now-playing card from the hub's now-playing.json, one same-origin file the
// hub writes continuously. A 404, or a 200 with an empty or malformed body (an
// old hub, the service down, a partial write, Review Focus 3), means nothing is
// playing: the card hides and never shows stale data. Only a network failure
// (the fetch or the body read itself throwing) leaves the last render in place,
// rather than flashing the card away and back. The parse is kept out of that
// try so a bad body hides the card instead of looking like a transient error.
async function refreshNowPlaying() {
  let body;
  try {
    const response = await fetch("now-playing.json", { cache: "no-store" });
    if (!response.ok) {
      updateNowPlayingCard(null);
      return;
    }
    body = await response.text();
  } catch (err) {
    return;
  }
  updateNowPlayingCard(nowPlayingFromBody(body));
}

// Fill the card from a parsed track, or hide it when nothing is playing. The
// cover image is only swapped when its URL changes, so a poll that leaves the
// track unchanged does not reload the picture.
function updateNowPlayingCard(track) {
  state.nowTrack = track;
  if (!track) {
    els.npCard.hidden = true;
    return;
  }
  els.npCard.hidden = false;

  if (track.artUrl) {
    if (state.npArtSrc !== track.artUrl) {
      els.npArtImg.src = track.artUrl;
      state.npArtSrc = track.artUrl;
    }
    els.npArtImg.alt = track.title;
    els.npArtImg.hidden = false;
    els.npArtPh.hidden = true;
  } else {
    els.npArtImg.hidden = true;
    els.npArtImg.removeAttribute("src");
    state.npArtSrc = null;
    els.npArtPh.hidden = false;
  }

  els.npTitle.textContent = track.title;
  setOptionalLine(els.npArtist, track.artist);
  setOptionalLine(els.npAlbum, track.album);
  tickNowPlaying();
}

// Advance the progress bar and times from the position anchor. Runs every second
// and again on each poll. A track with no duration shows no bar; a paused track
// freezes the bar and shows the "Paused" label instead of an end time.
function tickNowPlaying() {
  const track = state.nowTrack;
  if (!track || els.npCard.hidden) {
    return;
  }
  const paused = track.status === "paused";
  const progress = progressAt(track, Date.now());
  if (progress) {
    els.npProgress.hidden = false;
    els.npFill.style.width = `${(progress.fraction * 100).toFixed(1)}%`;
    els.npElapsed.textContent = `${formatDuration(progress.elapsed)} / ${formatDuration(progress.total)}`;
    if (paused) {
      els.npEnds.hidden = true;
    } else {
      els.npEnds.hidden = false;
      els.npEnds.textContent = `ends ${formatClock(progress.endsAtMs)}`;
    }
  } else {
    els.npProgress.hidden = true;
  }
  els.npStatus.hidden = !paused;
}

function setOptionalLine(el, text) {
  if (text) {
    el.textContent = text;
    el.hidden = false;
  } else {
    el.textContent = "";
    el.hidden = true;
  }
}

// The play history from the hub's play-history.json, one same-origin file. Like
// the now-playing card, a 404 or a 200 with an empty or unparseable body clears
// the section; only a network failure leaves the last render in place, so the
// parse is kept out of that try.
async function refreshHistory() {
  let body;
  try {
    const response = await fetch("play-history.json", { cache: "no-store" });
    if (!response.ok) {
      renderHistory([]);
      return;
    }
    body = await response.text();
  } catch (err) {
    return;
  }
  renderHistory(historyFromBody(body));
}

function renderHistory(plays) {
  if (plays.length === 0) {
    els.npHistory.hidden = true;
    els.npHistoryList.replaceChildren();
    return;
  }
  els.npHistory.hidden = false;
  els.npHistoryList.replaceChildren();
  for (const play of plays) {
    els.npHistoryList.append(playItem(play));
  }
}

function playItem(play) {
  const li = document.createElement("li");
  li.className = "art-item";

  if (play.artUrl) {
    const thumb = document.createElement("img");
    thumb.className = "art-thumb";
    thumb.src = play.artUrl;
    thumb.alt = play.title;
    thumb.loading = "lazy";
    thumb.decoding = "async";
    li.append(thumb);
  } else {
    const ph = document.createElement("div");
    ph.className = "art-thumb np-thumb-ph";
    ph.setAttribute("aria-hidden", "true");
    li.append(ph);
  }

  const meta = document.createElement("div");
  meta.className = "art-meta";

  const title = document.createElement("span");
  title.className = "art-item-title";
  title.textContent = play.title;
  meta.append(title);

  const sub = [play.artist, play.album].filter(Boolean).join(" · ");
  if (sub) {
    const artist = document.createElement("span");
    artist.className = "art-item-artist";
    artist.textContent = sub;
    meta.append(artist);
  }

  const when = formatStartTime(play.startedAt);
  if (when) {
    const time = document.createElement("time");
    time.className = "art-when";
    time.dateTime = play.startedAt;
    time.textContent = when;
    meta.append(time);
  }

  if (play.duration !== null) {
    const length = document.createElement("span");
    length.className = "np-item-len";
    length.textContent = formatDuration(play.duration);
    meta.append(length);
  }

  li.append(meta);
  return li;
}

// A clock time like "9:46 PM" for the card's end time.
function formatClock(ms) {
  return new Date(ms).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

// When a play started: the clock time if it was today, otherwise the date and
// the time. Returns "" for an unparseable stamp so the row simply omits it.
function formatStartTime(iso) {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) {
    return "";
  }
  const then = new Date(ms);
  const now = new Date();
  const time = then.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const sameDay =
    then.getFullYear() === now.getFullYear() &&
    then.getMonth() === now.getMonth() &&
    then.getDate() === now.getDate();
  if (sameDay) {
    return time;
  }
  const date = then.toLocaleDateString([], { month: "short", day: "numeric" });
  return `${date}, ${time}`;
}

// --------------------------------------------------------------------------- //
// The art manager: the frame rotation and the library, driven by the admin
// service's two same-origin documents (art-index.json and art-control.json) and
// its /api/art/ write endpoints. The pure reading and request-building live in
// the model; this is the DOM and network glue around it. Every untrusted string
// reaches the DOM through textContent, and every image src is a same-origin
// /art/ or /art-thumbs/ path the model already validated.
// --------------------------------------------------------------------------- //

function wireArtManager() {
  els.artBack.addEventListener("click", () => runAction("/api/art/command", { action: "previous" }));
  els.artNext.addEventListener("click", () => runAction("/api/art/command", { action: "next" }));

  els.artInterval.addEventListener("change", () => {
    runAction("/api/art/settings", { rotate_minutes: Number(els.artInterval.value) });
  });

  els.artFolderFilter.addEventListener("change", () => {
    state.artFilter = els.artFolderFilter.value;
    renderLibraryGrid();
  });

  els.artUploadInput.addEventListener("change", () => {
    const files = Array.from(els.artUploadInput.files || []);
    const folder = els.artUploadFolder.value;
    // Let the same file be chosen again after this batch.
    els.artUploadInput.value = "";
    if (files.length === 0 || !folder) {
      return;
    }
    for (const file of files) {
      uploadOne(file, folder);
    }
  });

  els.artFolderCreate.addEventListener("submit", (event) => {
    event.preventDefault();
    const name = els.artFolderName.value.trim();
    if (name === "") {
      return;
    }
    els.artFolderName.value = "";
    runAction("/api/art/folder/create", { name });
  });
}

// Read both library documents and repaint, or leave the last render alone on a
// network failure. A 404 (an older hub without the admin service) clears the
// manager so the page falls back to the frame-history view. The parse is kept
// out of the fetch try so a bad body degrades to an empty library rather than
// looking like a transient error.
async function refreshLibrary() {
  let indexBody;
  let controlBody;
  try {
    const [indexRes, controlRes] = await Promise.all([
      fetch("art-index.json", { cache: "no-store" }),
      fetch("art-control.json", { cache: "no-store" }),
    ]);
    if (!indexRes.ok) {
      state.artIndex = null;
      state.artControl = null;
      updateArtSection();
      return;
    }
    indexBody = await indexRes.text();
    controlBody = controlRes.ok ? await controlRes.text() : "";
  } catch (err) {
    return;
  }
  state.artIndex = parseArtIndex(safeJsonParse(indexBody));
  state.artControl = parseArtControl(safeJsonParse(controlBody));
  renderLibrary();
}

function renderLibrary() {
  const index = state.artIndex;
  const control = state.artControl;
  if (!index || !control) {
    updateArtSection();
    return;
  }

  // Interval selector: built once, its value tracks the control file so a change
  // made elsewhere shows here too.
  if (!state.artIntervalBuilt) {
    els.artInterval.replaceChildren();
    for (const option of intervalOptions()) {
      const el = document.createElement("option");
      el.value = String(option.minutes);
      el.textContent = option.label;
      els.artInterval.append(el);
    }
    state.artIntervalBuilt = true;
  }
  els.artInterval.value = String(control.rotateMinutes);

  // Rotation summary and the folder chips that toggle rotation.
  const summary = rotationSummary(index, control);
  els.artRotationSummary.textContent = summary.text;
  els.artRotationSummary.classList.toggle("empty", summary.empty);
  renderFolderChips(index, control);

  // The folder filter and the upload target both list every folder; the filter
  // also offers "All". Both keep the current choice when it still exists.
  const folderNames = index.folders.map((f) => f.name);
  if (state.artFilter !== ALL_FOLDERS && !folderNames.includes(state.artFilter)) {
    state.artFilter = ALL_FOLDERS;
  }
  fillSelect(els.artFolderFilter, [{ value: ALL_FOLDERS, label: "All folders" }, ...folderNames.map((n) => ({ value: n, label: n }))], state.artFilter);
  fillSelect(els.artUploadFolder, folderNames.map((n) => ({ value: n, label: n })), els.artUploadFolder.value);

  renderLibraryGrid();
  renderFolderList(index);
  renderTrash(index);
  updateArtSection();
}

function renderFolderChips(index, control) {
  const enabled = new Set(control.foldersEnabled);
  els.artFolderChips.replaceChildren();
  for (const folder of index.folders) {
    const chip = document.createElement("button");
    chip.className = "art-chip";
    chip.type = "button";
    const on = enabled.has(folder.name);
    chip.setAttribute("aria-pressed", on ? "true" : "false");
    chip.classList.toggle("on", on);
    chip.textContent = `${folder.name} (${folder.count})`;
    chip.addEventListener("click", () => {
      runAction("/api/art/folder/enable", { name: folder.name, enabled: !on });
    });
    els.artFolderChips.append(chip);
  }
}

function renderLibraryGrid() {
  const index = state.artIndex;
  if (!index) {
    return;
  }
  const pieces = piecesInFolder(index, state.artFilter);
  els.artGrid.replaceChildren();
  for (const piece of pieces) {
    els.artGrid.append(libraryTile(piece, index));
  }
  els.artGridEmpty.hidden = pieces.length > 0;
}

function libraryTile(piece, index) {
  const li = document.createElement("li");
  li.className = "art-tile";

  const figure = document.createElement("div");
  figure.className = "art-tile-thumb-wrap";
  figure.append(thumbNode(piece.thumb, piece.title, "art-thumb art-tile-thumb"));
  if (!piece.landscape) {
    const badge = document.createElement("span");
    badge.className = "art-badge";
    badge.textContent = "Portrait";
    badge.title = "Portrait pieces do not rotate on the frame.";
    figure.append(badge);
  }
  li.append(figure);

  const title = document.createElement("span");
  title.className = "art-tile-title";
  title.textContent = piece.title;
  li.append(title);

  li.append(tileMenu(piece, index));
  return li;
}

// The per-piece actions, tucked behind a disclosure so the grid stays compact.
// A portrait piece gets no "Show now" (the frame would skip it).
function tileMenu(piece, index) {
  const menu = document.createElement("details");
  menu.className = "art-menu";
  const summary = document.createElement("summary");
  summary.className = "art-menu-summary";
  summary.textContent = "Options";
  menu.append(summary);

  const body = document.createElement("div");
  body.className = "art-menu-body";

  if (piece.landscape) {
    body.append(menuButton("Show now", () => runAction("/api/art/command", { action: "show", piece: piece.id })));
  }

  const others = index.folders.map((f) => f.name).filter((n) => n !== piece.folder);
  if (others.length > 0) {
    const moveLabel = document.createElement("label");
    moveLabel.className = "art-menu-move";
    moveLabel.append(document.createTextNode("Move to "));
    const select = document.createElement("select");
    select.className = "art-select";
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "folder...";
    select.append(placeholder);
    for (const name of others) {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      select.append(option);
    }
    select.addEventListener("change", () => {
      if (select.value) {
        runAction("/api/art/move", { id: piece.id, folder: select.value });
      }
    });
    moveLabel.append(select);
    body.append(moveLabel);
  }

  body.append(menuButton("Delete", () => runAction("/api/art/delete", { id: piece.id }), "art-menu-danger"));
  menu.append(body);
  return menu;
}

function renderFolderList(index) {
  els.artFolderList.replaceChildren();
  for (const folder of index.folders) {
    if (folder.builtin) {
      // The reserved Collection folder is not renamed or deleted.
      continue;
    }
    const li = document.createElement("li");
    li.className = "art-folder-row";

    const form = document.createElement("form");
    form.className = "art-folder-rename";
    const input = document.createElement("input");
    input.className = "art-text";
    input.type = "text";
    input.maxLength = 40;
    input.value = folder.name;
    input.setAttribute("aria-label", `Rename ${folder.name}`);
    form.append(input);

    const rename = document.createElement("button");
    rename.className = "art-btn art-btn-small";
    rename.type = "submit";
    rename.textContent = "Rename";
    form.append(rename);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const to = input.value.trim();
      if (to === "" || to === folder.name) {
        return;
      }
      runAction("/api/art/folder/rename", { from: folder.name, to });
    });
    li.append(form);

    const del = document.createElement("button");
    del.className = "art-btn art-btn-small art-menu-danger";
    del.type = "button";
    del.textContent = "Delete";
    // Delete is only offered for an empty folder; the service also enforces it.
    del.disabled = folder.count > 0;
    if (folder.count > 0) {
      del.title = "Only an empty folder can be deleted.";
    }
    del.addEventListener("click", () => runAction("/api/art/folder/delete", { name: folder.name }));
    li.append(del);

    els.artFolderList.append(li);
  }
}

function renderTrash(index) {
  els.artTrashList.replaceChildren();
  for (const entry of index.trash) {
    const li = document.createElement("li");
    li.className = "art-item art-trash-item";

    li.append(thumbNode(entry.thumb, entry.original, "art-thumb"));

    const meta = document.createElement("div");
    meta.className = "art-meta";
    const name = document.createElement("span");
    name.className = "art-item-title";
    name.textContent = entry.original;
    meta.append(name);
    const when = relativeTime(entry.deletedAt);
    if (when) {
      const time = document.createElement("time");
      time.className = "art-when";
      time.dateTime = entry.deletedAt;
      time.textContent = `deleted ${when}`;
      meta.append(time);
    }
    li.append(meta);

    const restore = document.createElement("button");
    restore.className = "art-btn art-btn-small";
    restore.type = "button";
    restore.textContent = "Restore";
    restore.addEventListener("click", () => runAction("/api/art/restore", { id: entry.id }));
    li.append(restore);

    els.artTrashList.append(li);
  }
  els.artTrashEmpty.hidden = index.trash.length > 0;
}

// Upload one file with a live progress line and the server's error text on
// failure. A separate request per file keeps the progress honest and one bad
// file from sinking the rest. XHR is used for the upload-progress events fetch
// does not surface; the model builds the multipart request so the guard header
// and the browser-set boundary stay correct.
function uploadOne(file, folder) {
  const li = document.createElement("li");
  li.className = "art-upload-item";
  const name = document.createElement("span");
  name.className = "art-upload-name";
  name.textContent = file.name;
  const status = document.createElement("span");
  status.className = "art-upload-status";
  status.textContent = "0%";
  li.append(name, status);
  els.artUploadProgress.append(li);

  const form = new FormData();
  form.append("file", file);
  form.append("folder", folder);
  const { url, init } = apiRequest("/api/art/upload", form);

  const xhr = new XMLHttpRequest();
  xhr.open("POST", url);
  for (const [key, value] of Object.entries(init.headers)) {
    xhr.setRequestHeader(key, value);
  }
  xhr.upload.addEventListener("progress", (event) => {
    if (event.lengthComputable) {
      status.textContent = `${Math.round((event.loaded / event.total) * 100)}%`;
    }
  });
  xhr.addEventListener("load", () => {
    if (xhr.status >= 200 && xhr.status < 300) {
      status.textContent = "Added";
      li.classList.add("ok");
      setTimeout(() => li.remove(), 4000);
    } else {
      const data = safeJsonParse(xhr.responseText);
      status.textContent = (data && data.error) || `Failed (${xhr.status}).`;
      li.classList.add("err");
    }
    refreshArt();
    refreshLibrary();
  });
  xhr.addEventListener("error", () => {
    status.textContent = "Upload failed.";
    li.classList.add("err");
  });
  xhr.send(init.body);
}

// Send a write to the admin service, show the server's error text on failure,
// then re-read both documents so the manager reflects the change (and the frame
// history catches up as it redraws).
async function runAction(path, body) {
  const { url, init } = apiRequest(path, body);
  try {
    const response = await fetch(url, init);
    if (!response.ok) {
      const data = safeJsonParse(await response.text());
      showArtMessage((data && data.error) || `Request failed (${response.status}).`);
    }
  } catch (err) {
    showArtMessage("Could not reach the art service.");
  }
  refreshArt();
  refreshLibrary();
}

// A short-lived status line for a failed action. It clears itself so a stale
// error does not sit under a later success.
function showArtMessage(text) {
  clearTimeout(state.artMessageTimer);
  els.artMessage.textContent = text;
  els.artMessage.hidden = false;
  state.artMessageTimer = setTimeout(() => {
    els.artMessage.hidden = true;
    els.artMessage.textContent = "";
  }, ART_MESSAGE_MS);
}

// Replace a <select>'s options, keeping the given value selected when it still
// exists (otherwise the first option).
function fillSelect(select, options, selected) {
  select.replaceChildren();
  for (const { value, label } of options) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    select.append(option);
  }
  if (options.some((o) => o.value === selected)) {
    select.value = selected;
  }
}

// A thumbnail image, or a placeholder when there is no thumb yet. The hub can
// publish the index before thumbnails are generated, so a fresh piece arrives
// with a null thumb; it still belongs in the grid. When a thumb path is present
// but 404s (a thumbnail still being written), onerror swaps in the same
// placeholder rather than leaving a broken-image icon. `className` carries the
// sizing classes the placeholder reuses so the tile keeps its shape.
function thumbNode(src, label, className) {
  if (!src) {
    return thumbPlaceholder(className);
  }
  const img = document.createElement("img");
  img.className = className;
  img.src = src;
  img.alt = label;
  img.loading = "lazy";
  img.decoding = "async";
  img.addEventListener("error", () => {
    img.replaceWith(thumbPlaceholder(className));
  });
  return img;
}

function thumbPlaceholder(className) {
  const ph = document.createElement("div");
  ph.className = `${className} art-thumb-ph`;
  ph.setAttribute("aria-hidden", "true");
  return ph;
}

function menuButton(label, onClick, extraClass) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = extraClass ? `art-menu-item ${extraClass}` : "art-menu-item";
  button.textContent = label;
  button.addEventListener("click", onClick);
  return button;
}

function setConnection(message) {
  els.connection.textContent = message;
  els.connection.hidden = message === "";
}
