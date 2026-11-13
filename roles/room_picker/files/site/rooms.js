// The room picker page controller: the DOM and network glue around the pure
// model in rooms-model.js.
//
// It loads the room list from rooms.json, then keeps a live picture of the house
// from snapserver over a JSON-RPC WebSocket at /jsonrpc, re-reading the full
// status whenever snapserver notifies a change. If the socket will not stay up
// it falls back to polling Server.GetStatus every 30 seconds. A status reply
// that will not parse never breaks the page: it keeps the last good render and
// shows a quiet notice. Toggles and the volume sliders send Client.SetVolume
// back the same way. A health line polls the hub's aggregate status file.
//
// Beyond the rooms it mirrors the bedside e-ink frame (/frame.png and
// /frame.json), shows what is playing on the House stream and the recent plays,
// and manages the art library (rotation, a collapsed paged grid, uploads,
// folders, and a restorable trash) through the hub's /api/art/ service. Every
// untrusted string reaches the DOM through textContent, and every image src is a
// same-origin path the model already validated.

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
  libraryRefreshMayRebuild,
  mirrorState,
  mirrorCaption,
  historyDeleteTarget,
  trashRestoreId,
  openInLinks,
  pageCount,
  pageSlice,
  FRAME_KINDS,
  frameControlAfterFetch,
  pinSummary,
  validateFrameSettings,
  framePinBody,
  frameUnpinBody,
  weatherLocationLabel,
  weatherSaveBody,
  weatherClearBody,
  selectedFrameFrom,
  frameIdsFrom,
  frameList,
  frameUrls,
  artCommandBody,
  frameQuietHoursBody,
  pairCountdown,
  formatCountdown,
  pairPhase,
  pairInstruction,
  pairProgress,
  pairOutcomeView,
  shouldPollPair,
  pairControlsDisabled,
  pairCountdownCue,
  deviceStatus,
  lastConnectedText,
  deviceButtons,
  forgetPrompt,
  forgetReminder,
} from "./rooms-model.js";
import { CLIMATE_ENABLED, frameTabVisible, resolveScreen, visibleTabs } from "./nav-model.js";
import { createPairController } from "./pair-controller.js";
import { createDevicesController } from "./devices-controller.js";
import { createVolumeSlider } from "./volume-slider.js";
import { createHaptics } from "./haptics.js";
import { patchKeyed } from "./keyed-list.js";
import { createPageTimers } from "./page-timers.js";
import { createToaster, snapshotRooms, undoPlan } from "./undo.js";
import { everythingOffPlan, howToText, loadBluetoothName, onlyHerePlan, roomState } from "./listen-model.js";
import { DEFAULT_HUES, applyCoverColor, loadCoverHues, swapImage } from "./cover-color.js";

// The fallback poll is deliberately slow: it only runs when the live WebSocket
// is down, and each Server.GetStatus is a full status read, so a slow cadence
// keeps that cost off snapserver.
const POLL_MS = 30000;
const HEALTH_MS = 30000;
const RECONNECT_MS = 3000;
const haptics = createHaptics();
// The hub writes now-playing.json continuously and play-history.json only when a
// play lands, so the card polls quickly and the history slowly. The card's
// progress bar ticks client-side every second from the position anchor.
const NOW_PLAYING_MS = 5000;
const HISTORY_MS = 30000;
const TICK_MS = 1000;

// Every polling interval registers here so they all sleep while the tab is hidden.
const timers = createPageTimers({
  setInterval: (fn, ms) => window.setInterval(fn, ms),
  clearInterval: (id) => window.clearInterval(id),
  isHidden: () => document.hidden,
});
// The frame mirror and the art library documents the hub publishes, refreshed on
// the same slow cadence as the frame history and re-read after every action.
const FRAME_MS = 30000;
const FRAME_CONTROL_MS = 30000;
const LIBRARY_MS = 30000;
// The default library filter: every folder. Matches the model's "all" sentinel.
const ALL_FOLDERS = "all";
// The pairing window moves fast (a 120 s window, a 60 s confirm), so its state is
// polled about once a second, but only while the tab is visible and either the
// Settings screen is showing or a window is live (shouldPollPair). The same tick
// re-renders the countdown so it counts down smoothly between fetches.
const PAIR_MS = 1000;
// The Bluetooth devices list changes at a human pace (a phone connects or
// leaves), so it is polled every few seconds under the same visibility rule.
const DEVICES_MS = 5000;

// Shown on the connection line when a status reply will not parse. The page
// keeps its last good render and says so quietly rather than breaking.
const STATUS_UNAVAILABLE_MSG = "Status unavailable; showing the last update.";
// Shown while the live WebSocket is down and the page is polling instead.
const OFFLINE_MSG = `Live updates are offline; refreshing every ${POLL_MS / 1000} seconds.`;

const els = {
  tabs: document.getElementById("tabs"),

  screens: {
    listen: document.getElementById("screen-listen"),
    frame: document.getElementById("screen-frame"),
    gallery: document.getElementById("screen-gallery"),
    settings: document.getElementById("screen-settings"),
  },

  health: document.getElementById("health"),
  connection: document.getElementById("connection"),

  frame: document.getElementById("frame"),
  frameSwitcher: document.getElementById("frame-switcher"),
  mirror: document.getElementById("mirror"),
  mirrorImg: document.getElementById("mirror-img"),
  mirrorUpdated: document.getElementById("mirror-updated"),
  mirrorCaption: document.getElementById("mirror-caption"),
  frameCaption: document.getElementById("frame-caption"),
  frameControls: document.getElementById("frame-controls"),
  frameBack: document.getElementById("frame-back"),
  frameNext: document.getElementById("frame-next"),
  frameMenu: document.getElementById("frame-menu"),
  frameShow: document.getElementById("frame-show"),
  frameDelete: document.getElementById("frame-delete"),
  frameHint: document.getElementById("frame-hint"),

  pin: document.getElementById("pin"),
  pinStatus: document.getElementById("pin-status"),
  pinCurrent: document.getElementById("pin-current"),
  pinUnpin: document.getElementById("pin-unpin"),
  pinForm: document.getElementById("pin-form"),
  pinWhat: document.getElementById("pin-what"),
  pinDuration: document.getElementById("pin-duration"),
  pinSet: document.getElementById("pin-set"),
  pinUnavailable: document.getElementById("pin-unavailable"),

  frameSettings: document.getElementById("frame-settings"),
  fsUnavailable: document.getElementById("fs-unavailable"),
  fsBody: document.getElementById("fs-body"),
  fsKinds: document.getElementById("fs-kinds"),
  fsQuietStart: document.getElementById("fs-quiet-start"),
  fsQuietEnd: document.getElementById("fs-quiet-end"),
  fsMorningEnabled: document.getElementById("fs-morning-enabled"),
  fsMorningTimes: document.getElementById("fs-morning-times"),
  fsWeekdayStart: document.getElementById("fs-weekday-start"),
  fsWeekdayEnd: document.getElementById("fs-weekday-end"),
  fsWeekendStart: document.getElementById("fs-weekend-start"),
  fsWeekendEnd: document.getElementById("fs-weekend-end"),
  fsSave: document.getElementById("fs-save"),
  fsError: document.getElementById("fs-error"),
  fsWeatherCurrent: document.getElementById("fs-weather-current"),
  fsWeatherInput: document.getElementById("fs-weather-input"),
  fsWeatherSave: document.getElementById("fs-weather-save"),
  fsWeatherClear: document.getElementById("fs-weather-clear"),
  fsWeatherError: document.getElementById("fs-weather-error"),

  pairing: document.getElementById("pairing"),
  pairIdle: document.getElementById("pair-idle"),
  pairStart: document.getElementById("pair-start"),
  pairOpen: document.getElementById("pair-open"),
  pairInstruction: document.getElementById("pair-instruction"),
  pairProgress: document.getElementById("pair-progress"),
  pairOpenCount: document.getElementById("pair-open-count"),
  pairClock: document.getElementById("pair-clock"),
  pairStop: document.getElementById("pair-stop"),
  pairLost: document.getElementById("pair-lost"),
  pairRetry: document.getElementById("pair-retry"),
  pairClose: document.getElementById("pair-close"),
  pairOutcome: document.getElementById("pair-outcome"),
  pairOutcomeText: document.getElementById("pair-outcome-text"),
  pairOutcomeDetail: document.getElementById("pair-outcome-detail"),
  pairAgain: document.getElementById("pair-again"),
  pairError: document.getElementById("pair-error"),
  pairAnnounce: document.getElementById("pair-announce"),
  devList: document.getElementById("dev-list"),
  devLoading: document.getElementById("dev-loading"),
  devEmpty: document.getElementById("dev-empty"),
  devStale: document.getElementById("dev-stale"),
  devError: document.getElementById("dev-error"),
  devReminder: document.getElementById("dev-reminder"),
  devReminderText: document.getElementById("dev-reminder-text"),
  devReminderDismiss: document.getElementById("dev-reminder-dismiss"),

  frameQuiet: document.getElementById("frame-quiet"),
  fqFrameField: document.getElementById("fq-frame-field"),
  fqFrame: document.getElementById("fq-frame"),
  fqMode: document.getElementById("fq-mode"),
  fqCustom: document.getElementById("fq-custom"),
  fqStart: document.getElementById("fq-start"),
  fqEnd: document.getElementById("fq-end"),
  fqSave: document.getElementById("fq-save"),
  fqError: document.getElementById("fq-error"),

  npArtImg: document.getElementById("np-art-img"),
  npArtPh: document.getElementById("np-art-ph"),
  npTitle: document.getElementById("np-title"),
  np: document.getElementById("np"),
  npSource: document.getElementById("np-source"),
  npHowto: document.getElementById("np-howto"),
  everythingOff: document.getElementById("everything-off"),
  npProgress: document.getElementById("np-progress"),
  npFill: document.getElementById("np-fill"),
  npElapsed: document.getElementById("np-elapsed"),
  npEnds: document.getElementById("np-ends"),
  npStatus: document.getElementById("np-status"),

  rooms: document.getElementById("rooms"),

  library: document.getElementById("library"),
  libraryDetails: document.getElementById("library-details"),
  libraryCount: document.getElementById("library-count"),
  artFolderFilter: document.getElementById("art-folder-filter"),
  artGrid: document.getElementById("art-grid"),
  artGridEmpty: document.getElementById("art-grid-empty"),
  libraryPager: document.getElementById("library-pager"),
  libPrev: document.getElementById("lib-prev"),
  libNext: document.getElementById("lib-next"),
  libPage: document.getElementById("lib-page"),

  artHistorySection: document.getElementById("art-history-section"),
  artHistory: document.getElementById("art-history"),

  playHistorySection: document.getElementById("play-history-section"),
  npHistoryList: document.getElementById("np-history-list"),

  rotation: document.getElementById("rotation"),
  artRotationSummary: document.getElementById("art-rotation-summary"),
  artInterval: document.getElementById("art-interval"),
  artFolderChips: document.getElementById("art-folder-chips"),

  upload: document.getElementById("upload"),
  artUploadFolder: document.getElementById("art-upload-folder"),
  artUploadInput: document.getElementById("art-upload-input"),
  artUploadProgress: document.getElementById("art-upload-progress"),
  artFolderCreate: document.getElementById("art-folder-create"),
  artFolderName: document.getElementById("art-folder-name"),
  artFolderList: document.getElementById("art-folder-list"),

  trash: document.getElementById("trash"),
  artTrashList: document.getElementById("art-trash-list"),
  artTrashEmpty: document.getElementById("art-trash-empty"),

  openSheet: document.getElementById("open-sheet"),
  sheetTitle: document.getElementById("sheet-title"),
  sheetClose: document.getElementById("sheet-close"),
  sheetLinks: document.getElementById("sheet-links"),

  toast: document.getElementById("toast"),
};

const state = {
  config: { rooms: [] },
  cards: new Map(), // room id -> { card, toggle, slider }
  rooms: new Map(), // room id -> latest row from roomsFromStatus
  socket: null,
  pollTimer: null,
  reconnectTimer: null,
  nextId: 1,

  // The last parsed now-playing track (or null); the progress bar ticks from it.
  // npArtSrc is the cover src last set, so the image is swapped only on change.
  nowTrack: null,
  npArtSrc: null,
  btName: null,

  // The frame mirror state (mirrorState result) for the chosen frame and the
  // cover-busting src set.
  mirror: { visible: false, updatedLabel: "" },
  mirrorSrc: null,
  // The chosen frame (the "#frame/<id>" hash; the first frame by default) and
  // each frame's parsed frame.json, so the switcher can mark a frame offline when
  // its mirror is missing (its Inky not yet installed, Review Focus 1).
  selectedFrame: "nightstand",
  mirrors: {},
  // What the frame is actually showing (mirrorCaption result): art, or a screen
  // that has taken the panel over. When it is not art, the mirror carries a plain
  // caption and the art piece below is labelled as the next piece in rotation.
  frameShowing: { isArt: true, caption: "", nextLabel: null },

  // The frame's current piece and its shown history (from art-history.json).
  artCurrent: null,
  artHistory: [],

  // The parsed art library and rotation control (null until read once), the
  // library filter and zero-indexed page, whether the interval selector is
  // built, a held-refresh flag, and the toast timer.
  artIndex: null,
  artControl: null,
  artFilter: ALL_FOLDERS,
  libPage: 0,
  artIntervalBuilt: false,
  artRefreshPending: false,

  // The current screen (hash-routed), and the hub's parsed /frame-control.json
  // (shared settings plus each frame's quiet hours and pin), null until read
  // once. `available` is false when the document is missing or malformed, so the
  // page shows the frame settings as unknown rather than wrong.
  screen: "listen",
  frameControl: null,
  // True once the frame mirrors have been fetched at least once.
  framesProbed: false,
  fsKindsBuilt: false,

  // The pairing window lives in the pair controller (pair-controller.js): the
  // last service answer, whether this page opened a window, whether a POST is in
  // flight, and lost contact. `lastCue` is the coarse countdown cue last spoken,
  // so each is announced only once.
  pair: { lastCue: null, lastParsed: "idle" },
  // A fingerprint of the frame context the mirror and history were last fetched
  // for (the legacy flag, the known frame ids, and the chosen frame). When it
  // changes, the per-frame URLs changed, so those must be re-fetched even if the
  // chosen frame id did not: on first load the document arrives after the first
  // fetch, flipping legacy off and switching to the /frames/<id>/ paths.
  frameContextKey: null,
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
  els.npHowto.textContent = howToText(null);
  connect();
  wireChrome();
  els.everythingOff.addEventListener("click", onEverythingOff);
  wireArtManager();
  wireFrameControl();
  wirePairing();
  wireDevices();
  renderDevices();
  resolveSelectedFrame();
  applyHash();
  renderPairing();
  refreshHealth();
  // Read the frame-control document first (it sets the legacy flag and the frame
  // list), then fetch the mirror and history with the right per-frame URLs. The
  // control load kicks its own frame/art refresh when the context first resolves,
  // so this is the single initial fetch, with no legacy-then-per-frame flip.
  await refreshFrameControl();
  refreshLibrary();
  refreshNowPlaying();
  loadBluetoothName((url) =>
    fetch(url, { cache: "no-store" }).then((r) => {
      if (!r.ok) throw new Error("no name");
      return r.json();
    }),
  ).then((name) => {
    state.btName = name;
    if (!state.nowTrack) els.npHowto.textContent = howToText(name);
  });
  refreshHistory();
  timers.add(refreshHealth, HEALTH_MS);
  timers.add(refreshFrame, FRAME_MS);
  timers.add(refreshFrameControl, FRAME_CONTROL_MS);
  timers.add(refreshArt, HEALTH_MS);
  timers.add(() => refreshLibrary({ periodic: true }), LIBRARY_MS);
  timers.add(refreshNowPlaying, NOW_PLAYING_MS);
  timers.add(refreshHistory, HISTORY_MS);
  timers.add(tickNowPlaying, TICK_MS);
  // Poll the pairing state and tick its countdown once a second; pairTick itself
  // polls only when shouldPollPair allows it, so a hidden tab never polls.
  timers.add(pairTick, PAIR_MS);
  timers.add(devicesTick, DEVICES_MS);
  timers.start();

  // The URL hash is the screen and the chosen frame: Back, forward, and a shared
  // link all land on the right one.
  window.addEventListener("hashchange", onHashChange);

  // A phone that was asleep comes back with a dead socket and stale cards. On
  // return to the foreground, reconnect if needed, re-read the status, and
  // refresh everything so the page is current at a glance.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") {
      timers.pause();
      stopProgressLoop();
      return;
    }
    timers.resume();
    const socket = state.socket;
    if (!socket || socket.readyState === WebSocket.CLOSED) {
      connect();
    } else if (socket.readyState === WebSocket.OPEN) {
      requestStatus();
    }
    refreshHealth();
    refreshFrame();
    refreshFrameControl();
    refreshArt();
    refreshLibrary();
    refreshNowPlaying();
    refreshHistory();
    maybePollPairing();
    maybePollDevices();
    tickNowPlaying();
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

function reducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

// --------------------------------------------------------------------------- //
// Page chrome: the tab bar and the "Open in" sheet (a real dialog: focus
// trapped, Escape closes). The tabs switch between screens, each addressed by
// the URL hash so Back and refresh keep the screen.
// --------------------------------------------------------------------------- //

function wireChrome() {
  // Setting the hash routes through hashchange, which calls showScreen, so the
  // history entry and the screen stay in step.
  for (const tab of els.tabs.querySelectorAll(".tab")) {
    tab.addEventListener("click", () => {
      window.location.hash = `#${tab.dataset.screen}`;
    });
  }

  els.sheetClose.addEventListener("click", () => els.openSheet.close());
  els.openSheet.addEventListener("click", (event) => {
    if (event.target === els.openSheet) {
      els.openSheet.close();
    }
  });
}

// Which optional tabs show. Frame needs at least one frame in the hub's list
// (the same signals the Frame screen already uses); Climate waits on the flag in
// nav-model.js.
function navVisible() {
  return {
    frame: frameTabVisible(state.frameControl, state.mirrors, state.framesProbed),
    climate: CLIMATE_ENABLED,
  };
}

// Show one screen and hide the others, mark the current tab, and scroll to the
// top so switching screens starts at the beginning, not wherever the last one
// was scrolled.
function showScreen(name) {
  const screen = name in els.screens ? name : "listen";
  state.screen = screen;
  for (const [key, el] of Object.entries(els.screens)) {
    el.hidden = key !== screen;
  }
  syncTabs();
  window.scrollTo({ top: 0, behavior: "auto" });
  // Entering Settings shows the pairing state, so fetch it now rather than
  // waiting up to a second for the next tick. The devices list too.
  maybePollPairing();
  maybePollDevices();
}

// Show the tabs that are visible, in bar order, and mark the current one.
function syncTabs() {
  const visible = visibleTabs(navVisible());
  for (const tab of els.tabs.querySelectorAll(".tab")) {
    const name = tab.dataset.screen;
    tab.hidden = !visible.includes(name);
    if (name === state.screen) {
      tab.setAttribute("aria-current", "page");
    } else {
      tab.removeAttribute("aria-current");
    }
  }
}

// The frame signals may have just changed: show or hide the Frame tab, and
// leave the Frame screen if it is no longer there.
function syncFrameTab() {
  if (state.screen === "frame" && !navVisible().frame) {
    applyHash();
  } else {
    syncTabs();
  }
}

// Resolve the URL hash to a screen. An alias (#art) or a hidden or unknown
// screen has its hash rewritten in place, so Back never lands on it.
function applyHash() {
  const { screen, hash } = resolveScreen(window.location.hash, navVisible());
  if (window.location.hash !== hash) {
    history.replaceState(null, "", hash);
  }
  showScreen(screen);
}

// The known frame ids (from the parsed frame-control document), and whether the
// page is on the old single-frame contract (legacy): both default to a
// nightstand-only view until the document is read once, so the page works before
// the first fetch and if the hub has not shipped the feature.
function frameIds() {
  return frameIdsFrom(state.frameControl);
}

function controlLegacy() {
  return state.frameControl ? state.frameControl.legacy : true;
}

// Resolve the chosen frame from the hash, the known frames, and the current
// selection, and keep it on state. Returns true when the selection changed, so
// a caller can re-render and re-fetch for the new frame. An unknown id in the
// hash never selects an arbitrary frame (Review Focus 5).
function resolveSelectedFrame() {
  const next = selectedFrameFrom(window.location.hash, frameIds(), state.selectedFrame);
  const resolved = next || frameIds()[0];
  const changed = resolved !== state.selectedFrame;
  state.selectedFrame = resolved;
  return changed;
}

// Pick a frame in place (not through the hash), for a selector on a screen other
// than the Frame screen. The id comes from a control the page built from the
// known frames, but it is checked anyway so an unexpected value never selects an
// arbitrary frame. The choice persists and every per-frame view follows it.
function selectFrame(id) {
  if (!frameIds().includes(id) || id === state.selectedFrame) {
    return;
  }
  state.selectedFrame = id;
  renderFrameSwitcher();
  renderFrameControl();
  if (frameContextChanged()) {
    refreshFrame();
    refreshArt();
  }
}

// The hash drives both the screen and the chosen frame. When the frame changes,
// re-render the switcher and the frame-scoped controls and re-fetch that frame's
// mirror and history, so every per-frame view follows the chosen frame.
function onHashChange() {
  applyHash();
  resolveSelectedFrame();
  renderFrameSwitcher();
  renderFrameControl();
  if (frameContextChanged()) {
    refreshFrame();
    refreshArt();
  }
}

// --------------------------------------------------------------------------- //
// Rooms: the live snapserver picture.
// --------------------------------------------------------------------------- //

// One row per room, built once so a slider the user is dragging is never torn
// out from under them. Later updates only change values and classes.
function buildCards() {
  els.rooms.replaceChildren();
  state.cards.clear();
  for (const room of state.config.rooms) {
    const card = document.createElement("li");
    card.className = "room";

    const name = document.createElement("div");
    name.className = "room-name";
    const label = document.createElement("span");
    label.className = "room-label";
    label.textContent = room.label;
    const stateEl = document.createElement("span");
    stateEl.className = "room-state";
    name.append(label, stateEl);

    const only = document.createElement("button");
    only.className = "only-here";
    only.type = "button";
    only.textContent = "Only here";
    only.setAttribute("aria-label", `Play only in ${room.label}`);
    only.addEventListener("click", () => onOnlyHere(room.id));

    const toggle = document.createElement("button");
    toggle.className = "toggle";
    toggle.type = "button";
    toggle.setAttribute("role", "switch");
    toggle.setAttribute("aria-label", `${room.label} on or off`);
    toggle.addEventListener("click", () => onToggle(room.id));

    const entry = { card, toggle, only, stateEl, slider: null };
    entry.slider = createVolumeSlider({
      label: `${room.label} volume`,
      onSend: (p) => {
        // Dragging an off room wakes it: unmute first, then the volume.
        if (entry.toggle.getAttribute("aria-checked") !== "true") {
          setRoomPower(room.id, true);
        }
        sendControl(volumeRequest(room.id, p));
      },
      onRender: (p) => {
        if (stateEl.dataset.mode === "volume") {
          stateEl.textContent = `${p}%`;
        }
      },
      haptics,
    });

    card.append(name, only, toggle, entry.slider.el);
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
    setConnection(STATUS_UNAVAILABLE_MSG);
    return;
  }
  const messages = Array.isArray(message) ? message : [message];
  for (const one of messages) {
    if (one.result && one.result.server) {
      clearStatusMessage();
      render(one.result);
      continue;
    }
    if (one.method && one.id === undefined) {
      requestStatus();
    }
  }
}

function clearStatusMessage() {
  setConnection(state.pollTimer ? OFFLINE_MSG : "");
}

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
  return interpretRpcResponse(response.ok, await response.text());
}

function sendControl(request) {
  if (state.socket && state.socket.readyState === WebSocket.OPEN) {
    state.socket.send(envelope(request.method, request.params));
    return undefined;
  }
  return postRpc(request.method, request.params)
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

// The rows in the shape listen-model.js reads: `reachable` is snapserver's
// `connected`.
function listenRooms() {
  return state.config.rooms
    .map((r) => state.rooms.get(r.id))
    .filter(Boolean)
    .map((r) => ({ id: r.id, on: r.on, reachable: r.connected, volume: r.volume }));
}

function render(status) {
  const rooms = roomsFromStatus(status, state.config.rooms);
  for (const room of rooms) {
    state.rooms.set(room.id, room);
  }
  renderListen();
}

// Paint every row and the header button from the latest rooms.
function renderListen() {
  const all = listenRooms();
  for (const room of all) {
    const entry = state.cards.get(room.id);
    if (!entry) {
      continue;
    }
    const st = roomState(room, all);
    entry.card.classList.toggle("disconnected", !room.reachable);
    entry.toggle.setAttribute("aria-checked", room.on ? "true" : "false");
    entry.toggle.textContent = room.on ? "On" : "Off";
    entry.toggle.disabled = st.disabled;
    entry.only.hidden = !st.showOnlyHere;
    entry.only.disabled = st.disabled;
    entry.stateEl.dataset.mode = room.reachable && room.on ? "volume" : "text";
    entry.stateEl.textContent = st.label;
    entry.slider.applyStatus(room.volume);
    entry.slider.setEnabled(!st.disabled);
  }
  els.everythingOff.disabled = everythingOffPlan(all).length === 0;
}

// Turn one room on or off: the row flips at once, a muted group is unmuted
// first, then the client is switched.
function setRoomPower(id, next) {
  const entry = state.cards.get(id);
  if (entry) {
    entry.toggle.setAttribute("aria-checked", next ? "true" : "false");
    entry.toggle.textContent = next ? "On" : "Off";
    const vol = entry.stateEl.dataset.mode;
    if (next && vol !== "volume") {
      entry.stateEl.dataset.mode = "volume";
    }
    if (!next) {
      entry.stateEl.dataset.mode = "text";
      entry.stateEl.textContent = "Off";
    }
  }
  const room = state.rooms.get(id);
  if (next && room && room.groupMuted && room.groupId) {
    sendControl(groupUnmuteRequest(room.groupId));
  }
  sendControl(toggleRequest(id, next));
}

function onToggle(id) {
  const entry = state.cards.get(id);
  if (!entry) {
    return;
  }
  const next = entry.toggle.getAttribute("aria-checked") !== "true";
  if (next) haptics.roomOn();
  else haptics.roomOff();
  setRoomPower(id, next);
}

// Send a plan, then offer Undo. The snapshot is taken before any write, and Undo
// replays on/off only, so volumes are never touched.
function runPlan(plan, message) {
  if (plan.length === 0) {
    return;
  }
  const snapshot = snapshotRooms(listenRooms());
  for (const step of plan) {
    setRoomPower(step.id, step.on);
  }
  showToast(message, () => {
    for (const step of undoPlan(snapshot, listenRooms())) {
      setRoomPower(step.id, step.on);
    }
  });
}

function onOnlyHere(id) {
  const room = state.config.rooms.find((r) => r.id === id);
  haptics.roomOn();
  runPlan(onlyHerePlan(listenRooms(), id), `Playing only in the ${room ? room.label : "room"}.`);
}

function onEverythingOff() {
  haptics.roomOff();
  runPlan(everythingOffPlan(listenRooms()), "Everything is off.");
}

async function refreshHealth() {
  let entries;
  try {
    const response = await fetch("house-status.json", { cache: "no-store" });
    const doc = await response.json();
    entries = (doc.machines || []).map((machine) => ({
      host: machine.name,
      status: machine.status,
    }));
  } catch (err) {
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

// --------------------------------------------------------------------------- //
// The frame mirror: a live copy of a frame, from the hub's same-origin
// /frames/<id>/frame.json (timestamp) and frame.png (the image it sent). Each
// frame's mirror is fetched so the switcher can mark a frame offline when its
// mirror is missing (its Inky not yet installed, Review Focus 1); the chosen
// frame's mirror is shown. A missing or malformed document (a 404 until the hub
// republishes, or a bad body) hides that frame's mirror and marks it offline; a
// transient hub error leaves the last reading in place. On the old single-frame
// contract (legacy) the nightstand's mirror is read from the old single paths.
// --------------------------------------------------------------------------- //

async function refreshFrame() {
  const ids = frameIds();
  const legacy = controlLegacy();
  const mirrors = { ...state.mirrors };
  await Promise.all(
    ids.map(async (id) => {
      const urls = frameUrls(id, legacy);
      if (!urls) {
        mirrors[id] = null;
        return;
      }
      try {
        const response = await fetch(urls.meta, { cache: "no-store" });
        // A 404 (the frame is offline or not yet installed) reads as no mirror;
        // a transient network throw keeps the last-good reading below.
        mirrors[id] = response.ok ? safeJsonParse(await response.text()) : null;
      } catch (err) {
        // Keep the last-good mirror for this frame on a transient error.
      }
    })
  );
  // Keep only the current frames, so a removed frame does not linger offline.
  state.mirrors = Object.fromEntries(ids.map((id) => [id, mirrors[id] ?? null]));
  renderFrameSwitcher();

  const selMeta = state.mirrors[state.selectedFrame] ?? null;
  state.mirror = mirrorState(selMeta, Date.now());
  state.frameShowing = mirrorCaption(selMeta);
  renderMirror(selMeta, legacy, state.selectedFrame);
  // The art piece and its label depend on what the frame is showing, so re-render
  // the caption with the latest reading (the piece itself comes from refreshArt).
  renderFrameCaption(state.artCurrent);
  updateSections();
  state.framesProbed = true;
  syncFrameTab();
}

// The frame switcher: one button per known frame, in published order, the chosen
// one marked, an offline one flagged. Hidden while there is only one frame, so a
// single-frame house (or the old contract) shows no needless control. A tap sets
// the hash, which routes through onHashChange so the history entry and the
// chosen frame stay in step.
function renderFrameSwitcher() {
  const rows = frameList(state.frameControl, state.mirrors);
  if (rows.length <= 1) {
    els.frameSwitcher.hidden = true;
    els.frameSwitcher.replaceChildren();
    return;
  }
  els.frameSwitcher.replaceChildren();
  for (const row of rows) {
    const button = document.createElement("button");
    button.className = "frame-tab";
    button.type = "button";
    const chosen = row.id === state.selectedFrame;
    button.classList.toggle("current", chosen);
    if (chosen) {
      button.setAttribute("aria-current", "true");
    }
    const name = document.createElement("span");
    name.className = "frame-tab-name";
    name.textContent = row.label;
    button.append(name);
    if (row.offline) {
      button.classList.add("offline");
      const flag = document.createElement("span");
      flag.className = "frame-tab-flag";
      flag.textContent = "Offline";
      button.append(flag);
    }
    button.addEventListener("click", () => {
      window.location.hash = `#frame/${row.id}`;
    });
    els.frameSwitcher.append(button);
  }
  els.frameSwitcher.hidden = false;
}

function renderMirror(json, legacy, frameId) {
  if (!state.mirror.visible) {
    els.mirror.hidden = true;
    els.mirrorCaption.hidden = true;
    els.mirrorCaption.textContent = "";
    state.mirrorSrc = null;
    return;
  }
  // Bust the cache only when the frame reports a new timestamp, so the mirror
  // follows the frame without reloading the image on every poll.
  const stamp = json && typeof json.updated_at === "string" ? json.updated_at : state.mirror.updatedLabel;
  const urls = frameUrls(frameId, legacy);
  const imageBase = urls ? urls.image : "frame.png";
  const src = `${imageBase}?v=${encodeURIComponent(stamp)}`;
  if (state.mirrorSrc !== src) {
    swapImage(els.mirrorImg, src);
    state.mirrorSrc = src;
  }
  // If the image itself 404s (the json landed before the png), hide the mirror
  // and mark this frame offline in the switcher.
  els.mirrorImg.onerror = () => {
    els.mirror.hidden = true;
    els.mirrorCaption.hidden = true;
    els.mirrorCaption.textContent = "";
    state.mirror = { visible: false, updatedLabel: "" };
    state.frameShowing = { isArt: true, caption: "", nextLabel: null };
    state.mirrorSrc = null;
    state.mirrors = { ...state.mirrors, [frameId]: null };
    renderFrameSwitcher();
    renderFrameCaption(state.artCurrent);
    updateSections();
  };
  els.mirror.hidden = false;
  if (state.mirror.updatedLabel) {
    els.mirrorUpdated.textContent = state.mirror.updatedLabel;
    els.mirrorUpdated.hidden = false;
  } else {
    els.mirrorUpdated.hidden = true;
    els.mirrorUpdated.textContent = "";
  }
  // When a screen other than art holds the panel, name it plainly under the
  // image so the mirror does not read as if the art piece were showing.
  if (state.frameShowing.caption) {
    els.mirrorCaption.textContent = state.frameShowing.caption;
    els.mirrorCaption.hidden = false;
  } else {
    els.mirrorCaption.hidden = true;
    els.mirrorCaption.textContent = "";
  }
}

// --------------------------------------------------------------------------- //
// Frame control: the hub's /frame-control.json drives the Pin control on the
// Frame screen and the frame settings on the Settings screen. A missing or
// malformed document (it 404s until the hub ships the feature, or a bad body)
// reads as unavailable: the page shows the settings as unknown and leaves the
// frame running on its own, rather than inventing values (Review Focus 1).
// --------------------------------------------------------------------------- //

const KIND_LABELS = { music: "Music", podcast: "Podcasts", audiobook: "Audiobooks" };

function wireFrameControl() {
  els.pinSet.addEventListener("click", pinCurrent);
  els.pinUnpin.addEventListener("click", unpinFrame);
  els.fsSave.addEventListener("click", saveFrameTimes);
  els.fsWeatherSave.addEventListener("click", saveWeatherCity);
  els.fsWeatherClear.addEventListener("click", clearWeatherCity);
  els.fsWeatherInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      saveWeatherCity();
    }
  });
  els.fsMorningEnabled.addEventListener("change", () => {
    els.fsMorningTimes.classList.toggle("disabled", !els.fsMorningEnabled.checked);
  });

  // Per-frame quiet hours: the mode selector shows the custom times only when
  // "custom" is chosen, and the frame selector routes through the hash so the
  // chosen frame stays in step with the Frame screen switcher.
  els.fqMode.addEventListener("change", () => {
    els.fqCustom.hidden = els.fqMode.value !== "custom";
  });
  // The Settings frame selector changes which frame's quiet hours are edited
  // without leaving the Settings screen (setting the "#frame/<id>" hash would
  // route to the Frame screen). It picks the frame in place; the choice persists
  // and the Frame screen switcher follows it.
  els.fqFrame.addEventListener("change", () => selectFrame(els.fqFrame.value));
  els.fqSave.addEventListener("click", saveFrameQuietHours);
}

async function refreshFrameControl() {
  let json = null;
  let threw = false;
  try {
    const response = await fetch("frame-control.json", { cache: "no-store" });
    if (response.ok) {
      json = safeJsonParse(await response.text());
    }
  } catch (err) {
    threw = true;
  }
  // A 404, a malformed body, and a network throw all land on the unavailable
  // state rather than the HTML form defaults; a throw over a good render keeps
  // the last-good document. See frameControlAfterFetch.
  state.frameControl = frameControlAfterFetch(state.frameControl, { threw, json });
  // The frame list and the legacy flag come from this document, so re-resolve the
  // chosen frame once it is read (the hash may have named a frame the page did not
  // know yet) and re-fetch the mirror and history when the frame context changed:
  // the first document flips legacy off and switches the per-frame URLs, even when
  // the chosen frame id is unchanged (so a stale offline flag does not stick).
  resolveSelectedFrame();
  renderFrameControl();
  renderFrameSwitcher();
  syncFrameTab();
  if (frameContextChanged()) {
    refreshFrame();
    refreshArt();
  }
}

// Whether the frame context (the legacy flag, the known frame ids, and the chosen
// frame) changed since the mirror and history were last fetched, updating the
// stored fingerprint. A change means the per-frame URLs changed and must be
// re-fetched.
function frameContextChanged() {
  const key = `${controlLegacy()}|${frameIds().join(",")}|${state.selectedFrame}`;
  if (key === state.frameContextKey) {
    return false;
  }
  state.frameContextKey = key;
  return true;
}

// The per-frame pin for the chosen frame, or null when the document is
// unavailable or the frame is not in it (the selection is always resolved
// against the known frames, so this guards only a race).
function selectedFramePin() {
  const fc = state.frameControl;
  if (!fc || !fc.frames) {
    return null;
  }
  const entry = fc.frames[state.selectedFrame];
  return entry ? entry.pin : null;
}

function renderFrameControl() {
  const fc = state.frameControl;
  const available = Boolean(fc && fc.available);

  // Pin control (Frame screen): the chosen frame's own pin.
  els.pinForm.hidden = !available;
  els.pinUnavailable.hidden = available;
  if (available) {
    const summary = pinSummary(selectedFramePin(), Date.now());
    if (summary) {
      els.pinStatus.textContent = summary;
      els.pinStatus.hidden = false;
      els.pinCurrent.hidden = false;
    } else {
      els.pinStatus.hidden = true;
      els.pinStatus.textContent = "";
      els.pinCurrent.hidden = true;
    }
  } else {
    els.pinStatus.hidden = true;
    els.pinStatus.textContent = "";
    els.pinCurrent.hidden = true;
  }

  // Frame settings (Settings screen).
  els.fsUnavailable.hidden = available;
  els.fsBody.hidden = !available;
  if (available) {
    renderFrameSettings(fc.settings);
  }

  renderFrameQuiet();
}

// The per-frame quiet-hours section (Settings). It only applies on the new
// multi-frame contract, where each frame carries its own quiet-hours override;
// on the old single-frame contract (legacy) or while the document is
// unavailable, the shared quiet hours above still work and this section is
// hidden. The frame selector appears only when there is more than one frame.
function renderFrameQuiet() {
  const fc = state.frameControl;
  const show = Boolean(fc && fc.available && !fc.legacy);
  els.frameQuiet.hidden = !show;
  if (!show) {
    return;
  }

  const rows = frameList(fc, state.mirrors);
  if (rows.length > 1) {
    fillSelect(
      els.fqFrame,
      rows.map((row) => ({ value: row.id, label: row.label })),
      state.selectedFrame
    );
    els.fqFrameField.hidden = false;
  } else {
    els.fqFrameField.hidden = true;
  }

  const entry = fc.frames[state.selectedFrame];
  const override = entry ? entry.quietHours : null;
  if (override && override.start && override.end) {
    els.fqMode.value = "custom";
    els.fqStart.value = override.start;
    els.fqEnd.value = override.end;
  } else if (override && override.enabled === false) {
    els.fqMode.value = "off";
  } else {
    els.fqMode.value = "shared";
  }
  els.fqCustom.hidden = els.fqMode.value !== "custom";
}

function renderFrameSettings(settings) {
  const kinds = new Set(settings.now_playing_kinds);
  if (!state.fsKindsBuilt) {
    els.fsKinds.replaceChildren();
    for (const kind of FRAME_KINDS) {
      const chip = document.createElement("button");
      chip.className = "chip";
      chip.type = "button";
      chip.dataset.kind = kind;
      chip.textContent = KIND_LABELS[kind];
      chip.addEventListener("click", () => toggleKind(kind));
      els.fsKinds.append(chip);
    }
    state.fsKindsBuilt = true;
  }
  for (const chip of els.fsKinds.querySelectorAll(".chip")) {
    const on = kinds.has(chip.dataset.kind);
    chip.classList.toggle("on", on);
    chip.setAttribute("aria-pressed", on ? "true" : "false");
  }

  els.fsQuietStart.value = settings.quiet_hours.start;
  els.fsQuietEnd.value = settings.quiet_hours.end;
  els.fsMorningEnabled.checked = settings.morning_weather.enabled;
  els.fsMorningTimes.classList.toggle("disabled", !settings.morning_weather.enabled);
  els.fsWeekdayStart.value = settings.morning_weather.weekday[0];
  els.fsWeekdayEnd.value = settings.morning_weather.weekday[1];
  els.fsWeekendStart.value = settings.morning_weather.weekend[0];
  els.fsWeekendEnd.value = settings.morning_weather.weekend[1];

  // The weather city shows the hub's resolved display name (or "Not set"). The
  // input is left as the owner typed it, so a refresh after an unrelated change
  // never clears a half-typed city.
  els.fsWeatherCurrent.textContent = weatherLocationLabel(settings.weather_location);
}

// Toggle one now-playing kind and save, like the rotation folder chips: the
// change is immediate and always valid, so there is no form to submit.
async function toggleKind(kind) {
  const fc = state.frameControl;
  if (!fc || !fc.available) {
    return;
  }
  const current = new Set(fc.settings.now_playing_kinds);
  if (current.has(kind)) {
    current.delete(kind);
  } else {
    current.add(kind);
  }
  const next = FRAME_KINDS.filter((k) => current.has(k));
  await postFrameSettings({ now_playing_kinds: next });
}

// Save quiet hours and morning weather together, validated the way the hub
// validates them, so a bad time is caught here and explained in place rather
// than posted.
async function saveFrameTimes() {
  clearFsError();
  const subset = {
    quiet_hours: { start: els.fsQuietStart.value, end: els.fsQuietEnd.value },
    morning_weather: {
      enabled: els.fsMorningEnabled.checked,
      weekday: [els.fsWeekdayStart.value, els.fsWeekdayEnd.value],
      weekend: [els.fsWeekendStart.value, els.fsWeekendEnd.value],
    },
  };
  const check = validateFrameSettings(subset);
  if (!check.ok) {
    showFsError(check.error);
    return;
  }
  const result = await postFrameSettings(check.value);
  if (result.ok) {
    showToast("Frame settings saved.");
  }
}

// Validate a settings subset and post it, showing the server's error on failure.
// Returns { ok } so a caller can confirm. Always refreshes afterwards so the
// form reflects what the hub actually stored.
async function postFrameSettings(subset) {
  const check = validateFrameSettings(subset);
  if (!check.ok) {
    showFsError(check.error);
    return { ok: false };
  }
  const result = await postApi("/api/frame/settings", check.value);
  if (!result.ok) {
    showToast(result.error);
  }
  await refreshFrameControl();
  return result;
}

async function pinCurrent() {
  // The pin is scoped to the chosen frame, so pinning one never moves another
  // (Review Focus 2).
  const body = framePinBody(state.selectedFrame, els.pinWhat.value, els.pinDuration.value);
  if (!body) {
    return;
  }
  const result = await postApi("/api/frame/pin", body);
  if (!result.ok) {
    showToast(result.error);
  }
  await refreshFrameControl();
  refreshFrame();
}

async function unpinFrame() {
  const body = frameUnpinBody(state.selectedFrame);
  if (!body) {
    return;
  }
  const result = await postApi("/api/frame/unpin", body);
  if (!result.ok) {
    showToast(result.error);
  }
  await refreshFrameControl();
  refreshFrame();
}

// Save the chosen frame's quiet-hours override: follow the shared hours
// (null), keep none ({enabled:false}), or a custom window. A bad custom time is
// caught by frameQuietHoursBody and shown in place rather than posted.
async function saveFrameQuietHours() {
  clearFqError();
  const override = frameQuietOverrideFromForm();
  const built = frameQuietHoursBody(state.selectedFrame, override);
  if (!built.ok) {
    showFqError(built.error);
    return;
  }
  const result = await postApi("/api/frame/settings", built.value);
  if (!result.ok) {
    showFqError(result.error);
    return;
  }
  showToast("Quiet hours saved.");
  await refreshFrameControl();
}

// The quiet-hours override the per-frame form describes: null for "follow the
// shared hours", {enabled:false} for "no quiet hours", or the custom window.
function frameQuietOverrideFromForm() {
  if (els.fqMode.value === "off") {
    return { enabled: false };
  }
  if (els.fqMode.value === "custom") {
    return { start: els.fqStart.value, end: els.fqEnd.value };
  }
  return null;
}

function showFqError(message) {
  els.fqError.textContent = message;
  els.fqError.hidden = false;
}

function clearFqError() {
  els.fqError.hidden = true;
  els.fqError.textContent = "";
}

function showFsError(message) {
  els.fsError.textContent = message;
  els.fsError.hidden = false;
}

function clearFsError() {
  els.fsError.hidden = true;
  els.fsError.textContent = "";
}

// Geocode and save the typed weather city. The body goes straight to
// /api/frame/settings (not through the times validator, which only knows the
// other keys): the hub geocodes the city, and on a bad city or an unreachable
// geocoder its own error text is shown in place. Only the typed city name leaves
// the network; the hub sends just the city part to the geocoder.
async function saveWeatherCity() {
  clearWeatherError();
  const body = weatherSaveBody(els.fsWeatherInput.value);
  if (!body) {
    showWeatherError('Enter a city, like "San Diego, CA".');
    return;
  }
  const result = await postApi("/api/frame/settings", body);
  if (!result.ok) {
    showWeatherError(result.error);
    return;
  }
  els.fsWeatherInput.value = "";
  showToast("Weather city saved.");
  await refreshFrameControl();
}

// Clear the weather city, so the frame falls back to its own local config.
async function clearWeatherCity() {
  clearWeatherError();
  const result = await postApi("/api/frame/settings", weatherClearBody());
  if (!result.ok) {
    showWeatherError(result.error);
    return;
  }
  els.fsWeatherInput.value = "";
  showToast("Weather city cleared.");
  await refreshFrameControl();
}

function showWeatherError(message) {
  els.fsWeatherError.textContent = message;
  els.fsWeatherError.hidden = false;
}

function clearWeatherError() {
  els.fsWeatherError.hidden = true;
  els.fsWeatherError.textContent = "";
}

// --------------------------------------------------------------------------- //
// Pair a phone: start a Bluetooth window, tell the person what to do on the
// phone, and report the outcome when they come back. The hub accepts the first
// phone that asks, so there is no code here. The state comes from GET
// /api/pair/state, polled about once a second while a window is live or the
// Settings screen is visible (never in the background). The requests and the
// state live in the pair controller; this is the thin view over it.
// --------------------------------------------------------------------------- //

const PAIR_OUTCOME_PHASES = ["paired", "failed", "closed"];

const pair = createPairController({
  fetch: (url, init) => fetch(url, init),
  onChange: () => {
    renderPairing();
    // A phone that just paired belongs in the devices list now, not in 5 s.
    if (pair.state.parsed.state === "paired" && state.pair.lastParsed !== "paired") {
      maybePollDevices();
    }
    state.pair.lastParsed = pair.state.parsed.state;
  },
});

function wirePairing() {
  els.pairStart.addEventListener("click", () => pair.start());
  els.pairAgain.addEventListener("click", () => pair.start());
  els.pairStop.addEventListener("click", () => pair.stop());
  els.pairRetry.addEventListener("click", () => pair.retry());
  els.pairClose.addEventListener("click", () => pair.close());
}

// Whether a window is live. A live window is polled even off the Settings
// screen, so returning to Settings shows its true state rather than a stale one.
function pairWindowActive() {
  return pair.state.parsed.state === "open";
}

function pairPollAllowed() {
  // Once contact is lost (a bounded run of failed polls), stop polling: the
  // lost-contact view owns the section until the owner retries or closes.
  if (pair.state.lost) {
    return false;
  }
  return shouldPollPair({
    documentVisible: document.visibilityState === "visible",
    settingsVisible: state.screen === "settings",
    windowActive: pairWindowActive(),
  });
}

// The once-a-second tick: re-render so the countdown counts down smoothly, and
// poll the state when allowed. A poll never waits for a POST in flight.
function pairTick() {
  renderPairing();
  maybePollPairing();
}

function maybePollPairing() {
  if (pairPollAllowed()) {
    pair.poll();
  }
}

// Paint the one view the current state calls for and hide the rest. Re-run every
// second, so it only sets text and toggles `hidden`. When contact is lost the
// lost-contact view takes over and every live view is hidden, so the page never
// leaves a dead window on screen.
function renderPairing() {
  const parsed = pair.state.parsed;
  const lost = pair.state.lost;
  const phase = lost ? null : pairPhase(parsed, { started: pair.state.started });

  els.pairLost.hidden = !lost;
  els.pairIdle.hidden = lost || phase !== "idle";
  els.pairOpen.hidden = lost || phase !== "open";
  els.pairOutcome.hidden = lost || !PAIR_OUTCOME_PHASES.includes(phase);

  // A coarse, once-only spoken cue for the active countdown; null elsewhere.
  let cue = null;
  if (phase === "open") {
    els.pairInstruction.textContent = pairInstruction();
    const progress = pairProgress(parsed);
    els.pairProgress.hidden = progress === null;
    els.pairProgress.textContent = progress || "";
    cue = pairCountdownCue(setPairCount(parsed.until, Date.now()));
  } else if (!els.pairOutcome.hidden) {
    renderPairOutcome(phase, parsed);
  }
  announcePair(cue);

  if (pair.state.error) {
    els.pairError.textContent = pair.state.error;
    els.pairError.hidden = false;
  } else {
    els.pairError.hidden = true;
    els.pairError.textContent = "";
  }

  // A request in flight disables every action button, so a double tap cannot
  // reach the service as a duplicate.
  const disabled = pairControlsDisabled(pair.state.pending);
  els.pairStart.disabled = disabled;
  els.pairAgain.disabled = disabled;
  els.pairStop.disabled = disabled;
  els.pairRetry.disabled = disabled;
  els.pairClose.disabled = disabled;
}

// Set the countdown figure and return the whole seconds left (or null), so the
// caller can derive the spoken cue. The line is hidden when there is no usable
// deadline so the UI never shows a stuck 0:00.
function setPairCount(until, now) {
  const seconds = pairCountdown(until, now);
  els.pairOpenCount.hidden = seconds === null;
  els.pairClock.textContent = seconds === null ? "" : formatCountdown(seconds);
  return seconds;
}

// Speak a coarse countdown cue through the dedicated polite region, once each:
// the cue only changes at the marks, so a reader hears "30 seconds left." once
// and is otherwise left alone, not read a per-second tick.
function announcePair(cue) {
  if (cue === state.pair.lastCue) {
    return;
  }
  state.pair.lastCue = cue;
  els.pairAnnounce.textContent = cue || "";
}

function renderPairOutcome(phase, parsed) {
  const view = pairOutcomeView(phase, parsed);
  els.pairOutcomeText.classList.remove("ok", "bad");
  if (view.tone) {
    els.pairOutcomeText.classList.add(view.tone);
  }
  els.pairOutcomeText.textContent = view.text;
  els.pairOutcomeDetail.hidden = view.detail === null;
  els.pairOutcomeDetail.textContent = view.detail || "";
}

// --------------------------------------------------------------------------- //
// The frame's art history: the current piece (the mirror's caption) and the log
// of what the frame has shown, each entry deletable.
// --------------------------------------------------------------------------- //

async function refreshArt() {
  // The chosen frame's own history (its /frames/<id>/art-history.json, or the old
  // single path on the legacy contract). A missing file reads as no history,
  // which hides the section rather than showing another frame's pieces.
  const urls = frameUrls(state.selectedFrame, controlLegacy());
  const historyUrl = urls ? urls.history : "art-history.json";
  let art;
  try {
    const response = await fetch(historyUrl, { cache: "no-store" });
    if (!response.ok) {
      art = { current: null, history: [] };
    } else {
      art = artFromHistory(await response.json());
    }
  } catch (err) {
    return;
  }
  state.artCurrent = art.current;
  state.artHistory = art.history;
  renderFrameCaption(art.current);
  renderArtHistory(art.history);
  updateSections();
}

function renderFrameCaption(current) {
  els.frameCaption.replaceChildren();
  if (!current) {
    return;
  }
  // When another screen holds the panel, this piece is the next one in rotation,
  // not what is on the glass; label it so the title and its Back / Next controls
  // read as changing what comes next rather than what is showing now.
  if (state.frameShowing.nextLabel) {
    const label = document.createElement("span");
    label.className = "frame-next-label";
    label.textContent = state.frameShowing.nextLabel;
    els.frameCaption.append(label);
  }
  els.frameCaption.append(titleNode(current, "frame-title"));
  if (current.artist) {
    const artist = document.createElement("span");
    artist.className = "frame-artist";
    artist.textContent = current.artist;
    els.frameCaption.append(artist);
  }
}

function renderArtHistory(history) {
  els.artHistory.replaceChildren();
  for (const piece of history) {
    els.artHistory.append(artHistoryRow(piece));
  }
}

function artHistoryRow(piece) {
  const li = document.createElement("li");
  li.className = "row";

  const thumb = document.createElement("img");
  thumb.className = "row-thumb";
  thumb.src = artUrl(piece.file);
  thumb.alt = "";
  thumb.loading = "lazy";
  thumb.decoding = "async";
  // A frame piece that lives in a folder is not reachable at the share root, so
  // swap a broken thumbnail for a placeholder rather than show a broken image.
  thumb.addEventListener("error", () => {
    thumb.replaceWith(thumbPlaceholder("row-thumb"));
  });
  li.append(thumb);

  const meta = document.createElement("div");
  meta.className = "row-meta";
  meta.append(titleNode(piece, "row-title"));
  if (piece.artist) {
    const artist = document.createElement("span");
    artist.className = "row-sub";
    artist.textContent = piece.artist;
    meta.append(artist);
  }
  const when = relativeTime(piece.shown_at);
  if (when) {
    const time = document.createElement("time");
    time.className = "row-when";
    time.dateTime = piece.shown_at;
    time.textContent = when;
    meta.append(time);
  }
  li.append(meta);

  // Delete is offered only when the entry can be targeted safely (an id, or a
  // bare file name at the library root); otherwise the button is left out.
  const target = historyDeleteTarget(piece);
  if (target) {
    const del = document.createElement("button");
    del.className = "btn btn-small danger row-action";
    del.type = "button";
    del.textContent = "Delete";
    del.addEventListener("click", () => moveToTrash(target));
    li.append(del);
  }
  return li;
}

// The title, as a link to the museum page when the piece carries an https
// source_url, otherwise plain text. The URL is only ever set as an href.
function titleNode(piece, className) {
  if (piece.source_url) {
    const link = document.createElement("a");
    link.className = className;
    link.textContent = piece.title;
    link.href = piece.source_url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    return link;
  }
  const span = document.createElement("span");
  span.className = className;
  span.textContent = piece.title;
  return span;
}

// The same-origin path to an art-share image. encodeURIComponent keeps a file
// name with spaces or reserved characters a single path segment.
function artUrl(file) {
  return `art/${encodeURIComponent(file)}`;
}

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

// --------------------------------------------------------------------------- //
// Now playing on the House stream.
// --------------------------------------------------------------------------- //

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

// The page color follows the now-playing cover. A token drops a slow older
// extraction so it can never overwrite a newer cover's color.
let coverColorToken = 0;
function followCover(url) {
  coverColorToken += 1;
  const token = coverColorToken;
  if (!url) {
    applyCoverColor(document.documentElement, DEFAULT_HUES);
    return;
  }
  loadCoverHues(url).then((hues) => {
    if (token === coverColorToken) {
      applyCoverColor(document.documentElement, hues);
    }
  });
}

function updateNowPlayingCard(track) {
  state.nowTrack = track;
  els.np.hidden = !track;
  els.npHowto.hidden = Boolean(track);
  if (!track) {
    els.npHowto.textContent = howToText(state.btName);
    if (state.npArtSrc !== null) {
      state.npArtSrc = null;
      followCover(null);
    }
    stopProgressLoop();
    return;
  }

  if (track.artUrl) {
    if (state.npArtSrc !== track.artUrl) {
      swapImage(els.npArtImg, track.artUrl);
      state.npArtSrc = track.artUrl;
      followCover(track.artUrl);
    }
    els.npArtImg.alt = track.title;
    els.npArtImg.hidden = false;
    els.npArtPh.hidden = true;
  } else {
    els.npArtImg.hidden = true;
    els.npArtImg.removeAttribute("src");
    state.npArtSrc = null;
    els.npArtPh.hidden = false;
    followCover(null);
  }

  els.npTitle.textContent = track.title;
  setOptionalLine(els.npSource, [track.artist, track.album].filter(Boolean).join(" \u00b7 "));
  tickNowPlaying();
}

function setProgressFill(fraction) {
  els.npFill.style.transform = `scaleX(${Math.max(0, Math.min(1, fraction)).toFixed(4)})`;
}

function writeProgressText(progress, paused) {
  els.npElapsed.textContent = `${formatDuration(progress.elapsed)} / ${formatDuration(progress.total)}`;
  if (paused) {
    els.npEnds.hidden = true;
  } else {
    els.npEnds.hidden = false;
    els.npEnds.textContent = `ends ${formatClock(progress.endsAtMs)}`;
  }
}

function tickNowPlaying() {
  const track = state.nowTrack;
  if (!track) {
    stopProgressLoop();
    return;
  }
  const paused = track.status === "paused";
  const progress = progressAt(track, Date.now());
  if (progress) {
    els.npProgress.hidden = false;
    setProgressFill(progress.fraction);
    writeProgressText(progress, paused);
    progressSecond = Math.floor(progress.elapsed);
  } else {
    els.npProgress.hidden = true;
  }
  els.npStatus.hidden = !paused;
  if (progress && !paused && !document.hidden) {
    startProgressLoop();
  } else {
    stopProgressLoop();
  }
}

// One requestAnimationFrame loop moves the fill by transform. It runs only while
// a track is playing with its progress shown and the tab visible; the text is
// written only when the whole second changes.
let progressFrame = 0;
let progressSecond = -1;

function progressLoopWanted() {
  const track = state.nowTrack;
  return Boolean(
    track && track.status !== "paused" && !els.npProgress.hidden && !document.hidden,
  );
}

function progressStep() {
  progressFrame = 0;
  if (!progressLoopWanted()) {
    return;
  }
  const progress = progressAt(state.nowTrack, Date.now());
  if (!progress) {
    return;
  }
  setProgressFill(progress.fraction);
  const second = Math.floor(progress.elapsed);
  if (second !== progressSecond) {
    progressSecond = second;
    writeProgressText(progress, false);
  }
  progressFrame = requestAnimationFrame(progressStep);
}

function startProgressLoop() {
  if (progressFrame === 0 && progressLoopWanted()) {
    progressFrame = requestAnimationFrame(progressStep);
  }
}

function stopProgressLoop() {
  if (progressFrame !== 0) {
    cancelAnimationFrame(progressFrame);
    progressFrame = 0;
  }
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

// --------------------------------------------------------------------------- //
// Play history: each row opens the "Open in" sheet.
// --------------------------------------------------------------------------- //

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
  els.npHistoryList.replaceChildren();
  if (plays.length === 0) {
    els.playHistorySection.hidden = true;
    return;
  }
  els.playHistorySection.hidden = false;
  for (const play of plays) {
    els.npHistoryList.append(playRow(play));
  }
}

function playRow(play) {
  const li = document.createElement("li");

  const button = document.createElement("button");
  button.className = "row row-tappable";
  button.type = "button";
  button.setAttribute("aria-haspopup", "dialog");
  button.addEventListener("click", () => openInSheet(play));

  if (play.artUrl) {
    const thumb = document.createElement("img");
    thumb.className = "row-thumb";
    thumb.src = play.artUrl;
    thumb.alt = "";
    thumb.loading = "lazy";
    thumb.decoding = "async";
    button.append(thumb);
  } else {
    const ph = document.createElement("div");
    ph.className = "row-thumb row-thumb-ph";
    ph.setAttribute("aria-hidden", "true");
    button.append(ph);
  }

  const meta = document.createElement("div");
  meta.className = "row-meta";

  const title = document.createElement("span");
  title.className = "row-title";
  title.textContent = play.title;
  meta.append(title);

  const subParts = dedupe([play.artist, play.album].filter(Boolean));
  if (subParts.length > 0) {
    const artist = document.createElement("span");
    artist.className = "row-sub";
    artist.textContent = subParts.join(", ");
    meta.append(artist);
  }

  const when = formatStartTime(play.startedAt);
  if (when) {
    const time = document.createElement("time");
    time.className = "row-when";
    time.dateTime = play.startedAt;
    time.textContent = play.duration !== null ? `${when}, ${formatDuration(play.duration)}` : when;
    meta.append(time);
  }

  button.append(meta);
  li.append(button);
  return li;
}

function dedupe(values) {
  return values.filter((v, i) => values.indexOf(v) === i);
}

function openInSheet(play) {
  const links = openInLinks(play);
  els.sheetTitle.textContent = play.title;
  els.sheetLinks.replaceChildren();
  for (const link of links) {
    const anchor = document.createElement("a");
    anchor.className = "sheet-link";
    anchor.textContent = link.label;
    anchor.href = link.url;
    anchor.target = "_blank";
    anchor.rel = "noopener noreferrer";
    els.sheetLinks.append(anchor);
  }
  els.openSheet.showModal();
}

function formatClock(ms) {
  return new Date(ms).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

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
// The art library and rotation, driven by the hub's /api/art/ service and its
// two same-origin documents (art-index.json and art-control.json).
// --------------------------------------------------------------------------- //

function wireArtManager() {
  els.frameBack.addEventListener("click", () => runArtCommand("previous"));
  els.frameNext.addEventListener("click", () => runArtCommand("next"));
  els.frameShow.addEventListener("click", () => {
    els.frameMenu.open = false;
    showInLibrary(state.artCurrent);
  });
  els.frameDelete.addEventListener("click", () => {
    els.frameMenu.open = false;
    frameDeleteCurrent();
  });

  els.artInterval.addEventListener("change", () => {
    runAction("/api/art/settings", { rotate_minutes: Number(els.artInterval.value) });
  });

  els.artFolderFilter.addEventListener("change", () => {
    state.artFilter = els.artFolderFilter.value;
    state.libPage = 0;
    renderLibraryGrid();
  });

  els.libPrev.addEventListener("click", () => {
    state.libPage -= 1;
    renderLibraryGrid();
  });
  els.libNext.addEventListener("click", () => {
    state.libPage += 1;
    renderLibraryGrid();
  });

  els.artUploadInput.addEventListener("change", () => {
    const files = Array.from(els.artUploadInput.files || []);
    const folder = els.artUploadFolder.value;
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

  // When a held refresh is pending, resume it once the grid goes idle: a menu
  // closing (toggle does not bubble, so capture) or a select losing focus.
  els.artGrid.addEventListener("toggle", maybeResumeLibraryRefresh, true);
  els.artGrid.addEventListener("focusout", maybeResumeLibraryRefresh);
}

function libraryMenuBusy() {
  const menuOpen = els.artGrid.querySelector("details.menu[open]") !== null;
  const active = document.activeElement;
  const selectFocused = active instanceof HTMLSelectElement && els.artGrid.contains(active);
  return { menuOpen, selectFocused };
}

function maybeResumeLibraryRefresh() {
  if (!state.artRefreshPending) {
    return;
  }
  setTimeout(() => {
    if (!state.artRefreshPending) {
      return;
    }
    const { menuOpen, selectFocused } = libraryMenuBusy();
    if (menuOpen || selectFocused) {
      return;
    }
    refreshLibrary();
  }, 0);
}

async function refreshLibrary({ periodic = false } = {}) {
  const { menuOpen, selectFocused } = libraryMenuBusy();
  if (!libraryRefreshMayRebuild({ periodic, menuOpen, selectFocused })) {
    state.artRefreshPending = true;
    return;
  }
  state.artRefreshPending = false;

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
      updateSections();
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
    updateSections();
    return;
  }

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

  const summary = rotationSummary(index, control);
  els.artRotationSummary.textContent = summary.text;
  els.artRotationSummary.classList.toggle("empty", summary.empty);
  renderFolderChips(index, control);

  // The library count, in plain words.
  const total = index.pieces.length;
  if (total === 0) {
    els.libraryCount.textContent = "Library is empty";
  } else {
    const noun = total === 1 ? "piece" : "pieces";
    els.libraryCount.textContent = `Library: ${total} ${noun}, ${summary.count} in rotation`;
  }

  const folderNames = index.folders.map((f) => f.name);
  if (state.artFilter !== ALL_FOLDERS && !folderNames.includes(state.artFilter)) {
    state.artFilter = ALL_FOLDERS;
    state.libPage = 0;
  }
  fillSelect(
    els.artFolderFilter,
    [{ value: ALL_FOLDERS, label: "All folders" }, ...folderNames.map((n) => ({ value: n, label: n }))],
    state.artFilter
  );
  fillSelect(els.artUploadFolder, folderNames.map((n) => ({ value: n, label: n })), els.artUploadFolder.value);

  renderLibraryGrid();
  renderFolderList(index);
  renderTrash(index);
  updateSections();
}

function renderFolderChips(index, control) {
  const enabled = new Set(control.foldersEnabled);
  els.artFolderChips.replaceChildren();
  for (const folder of index.folders) {
    const chip = document.createElement("button");
    chip.className = "chip";
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
  const count = pageCount(pieces.length);
  state.libPage = Math.max(0, Math.min(count - 1, state.libPage));
  const slice = pageSlice(pieces, state.libPage);

  const folderNames = index.folders.map((f) => f.name).join("\u0001");
  patchKeyed(els.artGrid, slice, {
    key: (piece) => String(piece.id),
    sig: (piece) =>
      JSON.stringify([piece.title, piece.artist, piece.thumb, piece.landscape, piece.folder, folderNames]),
    create: (piece) => libraryTile(piece, index),
  });
  els.artGridEmpty.hidden = pieces.length > 0;

  els.libraryPager.hidden = count <= 1;
  els.libPage.textContent = `Page ${state.libPage + 1} of ${count}`;
  els.libPrev.disabled = state.libPage === 0;
  els.libNext.disabled = state.libPage >= count - 1;
}

function libraryTile(piece, index) {
  const li = document.createElement("li");
  li.className = "tile";
  li.dataset.pieceId = piece.id;

  const wrap = document.createElement("div");
  wrap.className = "tile-thumb-wrap";
  wrap.append(thumbNode(piece.thumb, piece.title, "row-thumb tile-thumb"));
  if (!piece.landscape) {
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = "Too tall";
    badge.title = "Too tall to rotate on the frame.";
    wrap.append(badge);
  }
  li.append(wrap);

  const title = document.createElement("span");
  title.className = "tile-title";
  title.textContent = piece.title;
  li.append(title);

  if (piece.artist) {
    const artist = document.createElement("span");
    artist.className = "tile-artist";
    artist.textContent = piece.artist;
    li.append(artist);
  }

  li.append(tileMenu(piece, index));
  return li;
}

function tileMenu(piece, index) {
  const menu = document.createElement("details");
  menu.className = "menu";
  const summary = document.createElement("summary");
  summary.className = "btn menu-summary";
  summary.textContent = "Options";
  menu.append(summary);

  const body = document.createElement("div");
  body.className = "menu-body";

  if (piece.landscape) {
    body.append(menuButton("Show now", () => runArtCommand("show", piece.id)));
  }

  const others = index.folders.map((f) => f.name).filter((n) => n !== piece.folder);
  if (others.length > 0) {
    const moveLabel = document.createElement("label");
    moveLabel.className = "menu-move";
    moveLabel.append(document.createTextNode("Move to "));
    const select = document.createElement("select");
    select.className = "select";
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

  body.append(menuButton("Delete", () => moveToTrash(piece.id), "danger"));
  menu.append(body);
  return menu;
}

function renderFolderList(index) {
  els.artFolderList.replaceChildren();
  for (const folder of index.folders) {
    if (folder.builtin) {
      continue;
    }
    const li = document.createElement("li");
    li.className = "folder-row";

    const form = document.createElement("form");
    form.className = "folder-rename";
    const input = document.createElement("input");
    input.className = "text";
    input.type = "text";
    input.maxLength = 40;
    input.value = folder.name;
    input.setAttribute("aria-label", `Rename ${folder.name}`);
    form.append(input);

    const rename = document.createElement("button");
    rename.className = "btn btn-small";
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
    del.className = "btn btn-small danger";
    del.type = "button";
    del.textContent = "Delete";
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
    li.className = "row";

    li.append(thumbNode(entry.thumb, entry.original, "row-thumb"));

    const meta = document.createElement("div");
    meta.className = "row-meta";
    const name = document.createElement("span");
    name.className = "row-title";
    name.textContent = entry.original;
    meta.append(name);
    const when = relativeTime(entry.deletedAt);
    if (when) {
      const time = document.createElement("time");
      time.className = "row-when";
      time.dateTime = entry.deletedAt;
      time.textContent = `Deleted ${when}`;
      meta.append(time);
    }
    li.append(meta);

    const restore = document.createElement("button");
    restore.className = "btn btn-small row-action";
    restore.type = "button";
    restore.textContent = "Restore";
    restore.addEventListener("click", () => runAction("/api/art/restore", { id: entry.id }));
    li.append(restore);

    els.artTrashList.append(li);
  }
  els.artTrashEmpty.hidden = index.trash.length > 0;
}

// Move a piece to the trash, then offer an Undo that restores it. The frame's
// "Delete this piece" advances the frame first (next) so it moves off the piece
// within one redraw, then deletes; the library and history deletes just delete.
async function frameDeleteCurrent() {
  const current = state.artCurrent;
  if (!current) {
    return;
  }
  const target = historyDeleteTarget(current);
  if (!target) {
    showToast("This piece can't be deleted from here.");
    return;
  }
  // Advance the chosen frame first so the deleted piece is gone from its panel in
  // one redraw and cannot come back from a stale state (Review Focus 2).
  const command = artCommandBody(state.selectedFrame, "next");
  if (command) {
    await postApi("/api/art/command", command);
  }
  await moveToTrash(target);
}

async function moveToTrash(target) {
  if (!target) {
    showToast("This piece can't be deleted.");
    return;
  }
  const result = await postApi("/api/art/delete", { id: target });
  refreshArt();
  refreshFrame();
  await refreshLibrary();
  if (!result.ok) {
    showToast(result.error);
    return;
  }
  // Undo is keyed on the delete target (the piece id), which is what the hub
  // records as the trash entry's `original`. Keying on the basename instead
  // silently failed to restore any piece that lived in a user folder.
  showToast("Moved to trash.", () => undoTrash(target));
}

async function undoTrash(target) {
  await refreshLibrary();
  const index = state.artIndex;
  const restoreId = index ? trashRestoreId(index.trash, target) : null;
  if (!restoreId) {
    showToast("Couldn't undo. Restore it from the Trash section.");
    return;
  }
  runAction("/api/art/restore", { id: restoreId });
}

// Open the library, filter to the current piece's folder, page to it, and
// scroll there, so "Show in library" lands on the piece.
function showInLibrary(current) {
  const index = state.artIndex;
  if (!index || !current) {
    return;
  }
  const piece = index.pieces.find((p) => (current.id && p.id === current.id) || p.file === current.file);
  els.libraryDetails.open = true;
  if (piece) {
    state.artFilter = piece.folder;
    const list = piecesInFolder(index, piece.folder);
    const idx = list.findIndex((p) => p.id === piece.id);
    state.libPage = idx >= 0 ? Math.floor(idx / 24) : 0;
    renderLibrary();
  }

  // The library lives on the Gallery screen. Routing through the hash calls
  // showScreen, which scrolls the body to the top, so the scroll to the piece
  // has to run after that lands or it is immediately overridden. When the hash
  // actually changes, scroll on the next hashchange (after showScreen); when we
  // are already on #gallery, no hashchange fires, so scroll now.
  const target = piece ? piece.id : null;
  if (window.location.hash === "#gallery") {
    scrollToPiece(target);
  } else {
    window.addEventListener(
      "hashchange",
      () => scrollToPiece(target),
      { once: true },
    );
    window.location.hash = "#gallery";
  }
}

// Scroll the piece's tile into view and flash it so "Show in library" lands on
// the piece, not just the top of the library. Falls back to the library heading
// when the tile is not on the page (no match, or the piece has no id).
function scrollToPiece(pieceId) {
  const behavior = reducedMotion() ? "auto" : "smooth";
  const tile = pieceId
    ? els.artGrid.querySelector(`[data-piece-id="${CSS.escape(pieceId)}"]`)
    : null;
  if (tile) {
    tile.scrollIntoView({ behavior, block: "center" });
    tile.classList.remove("tile-flash");
    // Reading offsetWidth restarts the animation if the same tile is targeted
    // again before the class has been cleared.
    void tile.offsetWidth;
    tile.classList.add("tile-flash");
    tile.addEventListener(
      "animationend",
      () => tile.classList.remove("tile-flash"),
      { once: true },
    );
    return;
  }
  els.library.scrollIntoView({ behavior, block: "start" });
}

function uploadOne(file, folder) {
  const li = document.createElement("li");
  li.className = "upload-item";
  const name = document.createElement("span");
  name.className = "upload-name";
  name.textContent = file.name;
  const status = document.createElement("span");
  status.className = "upload-status";
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

// Post one write to the admin service, returning { ok } or { ok:false, error }.
async function postApi(path, body) {
  const { url, init } = apiRequest(path, body);
  try {
    const response = await fetch(url, init);
    if (!response.ok) {
      const data = safeJsonParse(await response.text());
      return { ok: false, error: (data && data.error) || `Request failed (${response.status}).` };
    }
    return { ok: true };
  } catch (err) {
    return { ok: false, error: "Could not reach the art service." };
  }
}

// Send a rotation command for the chosen frame. The command always carries the
// frame id (the design: the page always sends a frame id), so Back, Next, and
// Show act only on the chosen frame and never move another (Review Focus 2).
function runArtCommand(action, piece) {
  const body = artCommandBody(state.selectedFrame, action, piece);
  if (!body) {
    return;
  }
  runAction("/api/art/command", body);
}

// Send a write and refresh, showing the server's error text as a toast on
// failure, so the manager reflects the change (and the frame catches up).
async function runAction(path, body) {
  const result = await postApi(path, body);
  if (!result.ok) {
    showToast(result.error);
  }
  refreshArt();
  refreshFrame();
  refreshLibrary();
}

// A short-lived toast above the tab bar, optionally carrying an Undo action.
// A new toast replaces the previous one. It never takes focus.
const toaster = createToaster({
  now: () => Date.now(),
  setTimer: (fn, ms) => setTimeout(fn, ms),
  clearTimer: (id) => clearTimeout(id),
  render: ({ visible, text, undo }) => {
    els.toast.replaceChildren();
    els.toast.hidden = !visible;
    if (!visible) return;
    const span = document.createElement("span");
    span.className = "toast-text";
    span.textContent = text;
    els.toast.append(span);
    if (undo) {
      const button = document.createElement("button");
      button.className = "toast-undo";
      button.type = "button";
      button.textContent = "Undo";
      button.addEventListener("click", undo);
      els.toast.append(button);
    }
  },
});

function showToast(text, onUndo) {
  toaster.show(text, onUndo);
}

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
  ph.className = `${className} thumb-ph`;
  ph.setAttribute("aria-hidden", "true");
  return ph;
}

function menuButton(label, onClick, extraClass) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = extraClass ? `menu-item ${extraClass}` : "menu-item";
  button.textContent = label;
  button.addEventListener("click", onClick);
  return button;
}

// Section visibility: the frame shows with a mirror, a current piece, or the
// manager; the library, rotation, upload, and trash need the admin service; the
// art-history section needs at least one shown piece. Now playing and play
// history toggle themselves in their own renders.
function updateSections() {
  const managerActive = state.artIndex !== null;
  const frameVisible = state.mirror.visible || Boolean(state.artCurrent) || managerActive;
  els.frame.hidden = !frameVisible;
  els.frameControls.hidden = !managerActive;
  els.frameHint.hidden = !managerActive;
  els.frameCaption.hidden = !state.artCurrent;

  // The Pin control shows whenever there is a frame to pin; its own body shows
  // the pin form or the "unavailable" note from the frame-control document.
  els.pin.hidden = !frameVisible;

  els.library.hidden = !managerActive;
  els.rotation.hidden = !managerActive;
  els.upload.hidden = !managerActive;
  els.trash.hidden = !managerActive;

  els.artHistorySection.hidden = state.artHistory.length === 0;
}

function setConnection(message) {
  els.connection.textContent = message;
  els.connection.hidden = message === "";
}


// --------------------------------------------------------------------------- //
// Bluetooth devices: the phones paired to the hub. The rows are drawn from the
// devices controller (devices-controller.js), which owns the requests, the
// in-flight flag and the forget confirm; this is the thin view over it. The list
// is polled every DEVICES_MS while the tab is visible and Settings is showing,
// the same rule as the pairing section.
// --------------------------------------------------------------------------- //

const devices = createDevicesController({
  fetch: (url, init) => fetch(url, init),
  onChange: () => renderDevices(),
});

// What the list last drew, so a poll that changes nothing leaves the rows (and
// whatever has keyboard focus in them) alone.

function wireDevices() {
  els.devList.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) {
      return;
    }
    const id = button.dataset.id;
    switch (button.dataset.action) {
      case "disconnect":
        devices.disconnect(id);
        break;
      case "forget":
        devices.askForget(id);
        break;
      case "forget-yes":
        devices.confirmForget();
        break;
      case "forget-no":
        devices.cancelForget();
        break;
      default:
        break;
    }
  });
  els.devReminderDismiss.addEventListener("click", () => devices.dismissReminder());
}

function devicesPollAllowed() {
  return shouldPollPair({
    documentVisible: document.visibilityState === "visible",
    settingsVisible: state.screen === "settings",
    windowActive: false,
  });
}

function maybePollDevices() {
  if (devicesPollAllowed()) {
    devices.poll();
  }
}

function devicesTick() {
  // Re-render too, so "5 minutes ago" keeps counting between fetches.
  renderDevices();
  maybePollDevices();
}

function deviceButton(label, action, id, { disabled, ghost = true, danger = false }) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `btn btn-small dev-btn${ghost ? " btn-ghost" : ""}${danger ? " danger" : ""}`;
  button.textContent = label;
  button.dataset.action = action;
  button.dataset.id = id;
  button.disabled = disabled;
  return button;
}

function deviceRow(device) {
  const buttons = deviceButtons(device, devices.state);
  const row = document.createElement("li");
  row.className = "dev-row";

  const text = document.createElement("div");
  const name = document.createElement("p");
  name.className = "dev-name";
  name.textContent = device.name;
  const stateLine = document.createElement("p");
  stateLine.className = "dev-state";
  const status = document.createElement("span");
  status.className = `dev-status${device.connected ? " on" : ""}`;
  status.textContent = deviceStatus(device);
  stateLine.append(status);
  const last = lastConnectedText(device, Date.now());
  if (last) {
    const lastEl = document.createElement("span");
    lastEl.textContent = last;
    stateLine.append(lastEl);
  }
  text.append(name, stateLine);
  row.append(text);

  if (buttons.confirm) {
    const prompt = forgetPrompt(device);
    const box = document.createElement("div");
    box.className = "dev-confirm";
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", prompt.question);
    const question = document.createElement("p");
    question.className = "dev-confirm-q";
    question.textContent = prompt.question;
    const detail = document.createElement("p");
    detail.className = "dev-confirm-detail";
    detail.textContent = prompt.detail;
    const actions = document.createElement("div");
    actions.className = "dev-actions";
    actions.append(
      deviceButton(prompt.cancel, "forget-no", device.id, { disabled: devices.state.pending, ghost: false }),
      deviceButton(prompt.confirm, "forget-yes", device.id, { disabled: devices.state.pending, danger: true }),
    );
    box.append(question, detail, actions);
    row.append(box);
    return row;
  }

  const actions = document.createElement("div");
  actions.className = "dev-actions";
  if (buttons.disconnect.visible) {
    actions.append(
      deviceButton("Disconnect", "disconnect", device.id, { disabled: buttons.disconnect.disabled }),
    );
  }
  if (buttons.forget.visible) {
    actions.append(
      deviceButton("Forget", "forget", device.id, { disabled: buttons.forget.disabled, danger: true }),
    );
  }
  row.append(actions);
  return row;
}

// The confirm that last got the safe-answer focus, so a poll never steals it again.
let devicesConfirmFocused = null;

function renderDevices() {
  const s = devices.state;
  els.devLoading.hidden = s.loaded;
  els.devEmpty.hidden = !s.loaded || s.devices.length > 0;
  els.devStale.hidden = !s.stale;
  els.devError.hidden = !s.error;
  els.devError.textContent = s.error || "";
  els.devReminder.hidden = s.forgotten === null;
  els.devReminderText.textContent = s.forgotten === null ? "" : forgetReminder(s.forgotten);

  // Keep keyboard focus on the same control across a redraw, and move it to the
  // safe answer when a confirm opens.
  const active = document.activeElement;
  const focusKey =
    active && active.dataset && active.dataset.action && els.devList.contains(active)
      ? `${active.dataset.action}:${active.dataset.id}`
      : null;
  const now = Date.now();
  patchKeyed(els.devList, s.devices, {
    key: (d) => String(d.id),
    sig: (d) =>
      JSON.stringify([
        d.name,
        d.connected,
        lastConnectedText(d, now),
        s.pending,
        s.confirmingId === d.id,
        deviceButtons(d, s),
      ]),
    create: deviceRow,
  });
  if (focusKey && els.devList.contains(document.activeElement)) {
    devicesConfirmFocused = s.confirmingId;
    return;
  }
  let target = null;
  if (focusKey) {
    const [action, id] = focusKey.split(":");
    target = els.devList.querySelector(`[data-action="${action}"][data-id="${id}"]:not(:disabled)`);
  }
  if (!target && s.confirmingId !== null && s.confirmingId !== devicesConfirmFocused) {
    target = els.devList.querySelector(`[data-action="forget-no"][data-id="${s.confirmingId}"]`);
  }
  devicesConfirmFocused = s.confirmingId;
  if (target) {
    target.focus();
  }
}
