// The room picker page controller: the DOM and network glue around the pure
// model in rooms-model.js.
//
// It loads the room list from rooms.json, then keeps a live picture of the house
// from snapserver over a JSON-RPC WebSocket at /jsonrpc, re-reading the full
// status whenever snapserver notifies a change. If the socket will not stay up
// it falls back to polling Server.GetStatus every five seconds. Toggles and the
// volume sliders send Client.SetVolume back the same way. A health line polls
// each machine's status file.

import {
  roomsFromStatus,
  nowPlaying,
  toggleRequest,
  volumeRequest,
  healthBanner,
} from "./rooms-model.js";

const POLL_MS = 5000;
const HEALTH_MS = 30000;
const RECONNECT_MS = 3000;
const SLIDER_DEBOUNCE_MS = 150;

const els = {
  nowPlaying: document.getElementById("now-playing"),
  health: document.getElementById("health"),
  rooms: document.getElementById("rooms"),
  connection: document.getElementById("connection"),
};

const state = {
  config: { rooms: [], health: [] },
  cards: new Map(), // room id -> { card, toggle, slider, dragging }
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
  setInterval(refreshHealth, HEALTH_MS);
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
  let message;
  try {
    message = JSON.parse(data);
  } catch (err) {
    return;
  }
  const messages = Array.isArray(message) ? message : [message];
  for (const one of messages) {
    // A response to our Server.GetStatus carries the whole picture.
    if (one.result && one.result.server) {
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
        render(result);
      }
    })
    .catch(() => setConnection("snapserver is not reachable."));
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
  const body = await response.json();
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
  setConnection("Live updates are offline; refreshing every few seconds.");
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
  const rooms = state.cards.get(id);
  if (!rooms) {
    return;
  }
  const nowOn = rooms.toggle.getAttribute("aria-checked") === "true";
  const next = !nowOn;
  // Optimistic: flip the control now, snapserver's notification confirms it.
  rooms.toggle.setAttribute("aria-checked", next ? "true" : "false");
  rooms.toggle.textContent = next ? "On" : "Off";
  rooms.card.classList.toggle("off", !next);
  sendControl(toggleRequest(id, next));
}

function onVolume(id, percent, entry) {
  clearTimeout(entry.pending);
  entry.pending = setTimeout(() => {
    sendControl(volumeRequest(id, percent));
  }, SLIDER_DEBOUNCE_MS);
}

async function refreshHealth() {
  const targets = state.config.health || [];
  const entries = await Promise.all(
    targets.map(async (target) => {
      try {
        const response = await fetch(target.url, { cache: "no-store" });
        if (!response.ok) {
          return { host: target.host, status: null };
        }
        return { host: target.host, status: await response.json() };
      } catch (err) {
        return { host: target.host, status: null };
      }
    })
  );
  const message = healthBanner(entries);
  if (message) {
    els.health.textContent = message;
    els.health.hidden = false;
  } else {
    els.health.hidden = true;
    els.health.textContent = "";
  }
}

function setConnection(message) {
  els.connection.textContent = message;
  els.connection.hidden = message === "";
}
