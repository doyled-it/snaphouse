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
  artFromHistory,
  nowPlayingFromBody,
  progressAt,
  formatDuration,
  historyWhenText,
  progressLabel,
  historyFromBody,
  interpretRpcResponse,
  interpretControlResponse,
  safeJsonParse,
  parseArtIndex,
  parseArtControl,
  piecesInFolder,
  rotationSummary,
  intervalOptions,
  apiRequest,
  libraryRefreshMayRebuild,
  movedPieceId,
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
  keepShowingBody,
  pinControlState,
  repinBody,
  weatherSaveBody,
  weatherClearBody,
  selectedFrameFrom,
  frameIdsFrom,
  frameList,
  frameUrls,
  artCommandBody,
  frameQuietHoursBody,
  effectiveQuietHours,
  quietOverrideFromFields,
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
import { createAutosave, changedKeys, saveStatus } from "./autosave-queue.js";
import { climateTabVisible, frameTabVisible, resolveScreen, visibleTabs } from "./nav-model.js";
import {
  EMPTY_PEOPLE_TEXT,
  SETTING_LIMITS,
  checkSettings,
  lastChangeText,
  overrideText,
  editSetting,
  parseClimateText,
  parseThisDevice,
  addBody,
  checkName,
  deviceLine,
  removeBody,
  parseWhole,
  peopleLines,
  staleness,
  settingsBody,
  thermostatView,
} from "./climate-model.js";
import { createPairController } from "./pair-controller.js";
import { createDevicesController } from "./devices-controller.js";
import { createVolumeSlider } from "./volume-slider.js";
import { createHaptics } from "./haptics.js";
import { patchKeyed } from "./keyed-list.js";
import { createPageTimers } from "./page-timers.js";
import { galleryHash, idAfterRemoval, pieceIdFromHash, stepIndex, swipeDirection, viewerSource, viewerState } from "./gallery-viewer.js";
import { createToaster, snapshotRooms, undoPlan } from "./undo.js";
import { classifyMachines, healthMessage, reasonLabel, speakerBanner } from "./health-model.js";
import { applyPlan, everythingOffPlan, heldStatus, howToText, loadBluetoothName, onlyHerePlan, playbackControls, playbackRequest, roomState } from "./listen-model.js";
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
// How long a play/pause tap holds its state against now-playing reads.
const PLAY_PAUSE_HOLD_MS = 3000;
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
// The climate document: read every 15 s while the Climate tab shows, and once a
// minute from the other tabs, so the tab appears and disappears with the file.
const CLIMATE_MS = 15000;
const CLIMATE_IDLE_EVERY = 4;

// Shown on the connection line when a status reply will not parse. The page
// keeps its last good render and says so quietly rather than breaking.
const STATUS_UNAVAILABLE_MSG = "Can't read the latest. Showing the last update.";
// Shown while the live WebSocket is down and the page is polling instead.
const OFFLINE_MSG = `Updates are slower than usual. Refreshing every ${POLL_MS / 1000} seconds.`;

const els = {
  tabs: document.getElementById("tabs"),

  screens: {
    listen: document.getElementById("screen-listen"),
    frame: document.getElementById("screen-frame"),
    gallery: document.getElementById("screen-gallery"),
    climate: document.getElementById("screen-climate"),
    settings: document.getElementById("screen-settings"),
  },

  health: document.getElementById("health"),
  connection: document.getElementById("connection"),
  speakers: document.getElementById("speakers"),
  needsAttention: document.getElementById("needs-attention"),
  needsAttentionList: document.getElementById("needs-attention-list"),
  notSetUp: document.getElementById("not-set-up"),
  notSetUpList: document.getElementById("not-set-up-list"),

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
  keepToggle: document.getElementById("keep-toggle"),
  keepMore: document.getElementById("keep-more"),
  pinForm: document.getElementById("pin-form"),
  pinWhat: document.getElementById("pin-what"),
  pinDuration: document.getElementById("pin-duration"),
  pinSet: document.getElementById("pin-set"),

  frameSettings: document.getElementById("frame-settings"),
  fsUnavailable: document.getElementById("fs-unavailable"),
  fsBody: document.getElementById("fs-body"),
  fsKinds: document.getElementById("fs-kinds"),
  fsQuietStart: document.getElementById("fs-quiet-start"),
  fsQuietEnd: document.getElementById("fs-quiet-end"),
  fsQuietReset: document.getElementById("fs-quiet-reset"),
  fsQuietEnabled: document.getElementById("fs-quiet-enabled"),
  fsQuietNote: document.getElementById("fs-quiet-note"),
  fsQuietError: document.getElementById("fs-quiet-error"),
  fsMorningEnabled: document.getElementById("fs-morning-enabled"),
  fsMorningTimes: document.getElementById("fs-morning-times"),
  fsMorningError: document.getElementById("fs-morning-error"),
  fsWeekdayStart: document.getElementById("fs-weekday-start"),
  fsWeekdayEnd: document.getElementById("fs-weekday-end"),
  fsWeekendStart: document.getElementById("fs-weekend-start"),
  fsWeekendEnd: document.getElementById("fs-weekend-end"),
  fsWeatherInput: document.getElementById("fs-weather-input"),
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
  npControls: document.getElementById("np-controls"),
  npPrev: document.getElementById("np-prev"),
  npPlayPause: document.getElementById("np-playpause"),
  npNext: document.getElementById("np-next"),

  rooms: document.getElementById("rooms"),

  library: document.getElementById("library"),
  galleryGridView: document.getElementById("gallery-grid-view"),
  galleryViewer: document.getElementById("gallery-viewer"),
  viewerClose: document.getElementById("viewer-close"),
  viewerPosition: document.getElementById("viewer-position"),
  viewerStage: document.getElementById("viewer-stage"),
  viewerPrev: document.getElementById("viewer-prev"),
  viewerNext: document.getElementById("viewer-next"),
  viewerImage: document.getElementById("viewer-image"),
  viewerTitle: document.getElementById("viewer-title"),
  viewerSub: document.getElementById("viewer-sub"),
  viewerShow: document.getElementById("viewer-show"),
  viewerPin: document.getElementById("viewer-pin"),
  viewerMove: document.getElementById("viewer-move"),
  viewerTrash: document.getElementById("viewer-trash"),
  viewerNote: document.getElementById("viewer-note"),
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
  // The parsed climate document (null: none or unreadable) and whether the
  // first read has finished, which the Climate tab's visibility waits on.
  climate: null,
  climateProbed: false,
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
  playHold: null, // { status, untilMs } set by a play/pause tap
  npArtSrc: null,
  btName: null,
  speakers: "up",
  connectionMessage: "",

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

  // The Gallery viewer: the piece id it shows (null when closed), whether this
  // page session pushed its hash entry (so Back closes it), the grid's scroll
  // position to return to, the folder row asking to confirm a delete, and the
  // folder list's last rendered signature.
  viewerId: null,
  // "grid" or "history": which list the open viewer steps through.
  viewerKind: "grid",
  // History entries removed this page session, so a log that still lists a
  // trashed piece does not bring it back until Undo.
  historyHidden: new Set(),
  // History ids that moved to another folder this session (old id to new id),
  // so the viewer and the rows follow the piece.
  historyMoved: new Map(),
  viewerPushed: false,
  gridScroll: 0,
  folderConfirm: null,
  folderSig: "",

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
  els.npPrev.addEventListener("click", () => onPlayback("previous"));
  els.npPlayPause.addEventListener("click", () => onPlayback("playPause"));
  els.npNext.addEventListener("click", () => onPlayback("next"));
  wireArtManager();
  wireFrameControl();
  wirePairing();
  wireDevices();
  wireClimate();
  renderDevices();
  resolveSelectedFrame();
  applyHash();
  renderPairing();
  refreshHealth();
  refreshClimate();
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
  timers.add(climateTick, CLIMATE_MS);
  timers.start();

  // The URL hash is the screen and the chosen frame: Back, forward, and a shared
  // link all land on the right one.
  window.addEventListener("hashchange", onHashChange);
  window.addEventListener("pagehide", flushSettings);

  // A phone that was asleep comes back with a dead socket and stale cards. On
  // return to the foreground, reconnect if needed, re-read the status, and
  // refresh everything so the page is current at a glance.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") {
      flushSettings();
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
    setConnection("Couldn't load the rooms. Reload the page.");
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
// (the same signals the Frame screen already uses); Climate shows while the hub's
// climate document answers.
function navVisible() {
  return {
    frame: frameTabVisible(state.frameControl, state.mirrors, state.framesProbed),
    climate: climateTabVisible(state.climate, state.climateProbed),
  };
}

// Show one screen and hide the others, mark the current tab, and scroll to the
// top so switching screens starts at the beginning, not wherever the last one
// was scrolled.
function showScreen(name) {
  const screen = name in els.screens ? name : "listen";
  const changed = screen !== state.screen;
  state.screen = screen;
  for (const [key, el] of Object.entries(els.screens)) {
    el.hidden = key !== screen;
  }
  syncTabs();
  els.everythingOff.hidden = screen !== "listen";
  if (changed) {
    window.scrollTo({ top: 0, behavior: "auto" });
  }
  // Entering Settings shows the pairing state, so fetch it now rather than
  // waiting up to a second for the next tick. The devices list too.
  maybePollPairing();
  maybePollDevices();
  if (screen === "climate" && changed) {
    refreshClimate();
  }
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
  syncViewer();
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
        setSpeakers("down");
      }
    });
}

function envelope(method, params) {
  return JSON.stringify({ id: state.nextId++, jsonrpc: "2.0", method, params });
}

async function postRpc(method, params, interpret = interpretRpcResponse) {
  const response = await fetch(rpcUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: envelope(method, params),
  });
  return interpret(response.ok, await response.text());
}

function sendControl(request) {
  if (state.socket && state.socket.readyState === WebSocket.OPEN) {
    state.socket.send(envelope(request.method, request.params));
    return undefined;
  }
  return postRpc(request.method, request.params)
    .then(() => requestStatus())
    .catch(() => setSpeakers("down"));
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
  setSpeakers("up");
  const rooms = roomsFromStatus(status, state.config.rooms);
  for (const room of rooms) {
    state.rooms.set(room.id, room);
  }
  renderListen();
}

// Paint every row and the header button from the latest rooms.
function renderListen() {
  const all = listenRooms();
  const speakersDown = state.speakers === "down";
  for (const room of all) {
    const entry = state.cards.get(room.id);
    if (!entry) {
      continue;
    }
    const st = roomState(room, all);
    entry.card.classList.toggle("disconnected", !room.reachable);
    entry.toggle.setAttribute("aria-checked", room.on ? "true" : "false");
    entry.toggle.textContent = room.on ? "On" : "Off";
    const off = st.disabled || speakersDown;
    entry.toggle.disabled = off;
    entry.only.hidden = !st.showOnlyHere;
    entry.only.disabled = off;
    entry.stateEl.dataset.mode = room.reachable && room.on ? "volume" : "text";
    entry.stateEl.textContent = st.label;
    entry.slider.applyStatus(room.volume);
    entry.slider.setEnabled(!off);
  }
  els.everythingOff.disabled = speakersDown || everythingOffPlan(all).length === 0;
}

// Turn one room on or off: the row flips at once, a muted group is unmuted
// first, then the client is switched.
function setRoomPower(id, next) {
  const entry = state.cards.get(id);
  if (entry) {
    entry.toggle.setAttribute("aria-checked", next ? "true" : "false");
    entry.toggle.textContent = next ? "On" : "Off";
    if (next) {
      entry.stateEl.dataset.mode = "volume";
      const known = state.rooms.get(id);
      if (known) entry.stateEl.textContent = `${Math.round(known.volume)}%`;
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
    for (const step of undoPlan(snapshot, applyPlan(listenRooms(), plan))) {
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
      installed: machine.installed !== false,
    }));
  } catch (err) {
    return;
  }
  const { troubled, notSetUp } = classifyMachines(entries);
  renderNotSetUp(notSetUp);
  renderNeedsAttention(troubled);
  const message = healthMessage(troubled);
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
      flag.textContent = "Not responding";
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
  els.keepToggle.addEventListener("click", toggleKeepShowing);

  // Every field saves itself. Switches write at once; time and text fields
  // write after a short pause (the queue's delay) and at once on blur.
  els.fsQuietEnabled.addEventListener("change", () => editQuietHours(true));
  for (const input of [els.fsQuietStart, els.fsQuietEnd]) {
    input.addEventListener("input", () => editQuietHours(false));
    input.addEventListener("blur", flushQuietHours);
  }
  els.fsQuietReset.addEventListener("click", resetQuietHours);
  els.fsMorningEnabled.addEventListener("change", () => editMorning(true));
  for (const input of [els.fsWeekdayStart, els.fsWeekdayEnd, els.fsWeekendStart, els.fsWeekendEnd]) {
    input.addEventListener("input", () => editMorning(false));
    input.addEventListener("blur", () => sharedQueue.flush());
  }
  els.fsWeatherInput.addEventListener("input", editWeatherCity);
  els.fsWeatherInput.addEventListener("blur", () => sharedQueue.flush());
  els.fsWeatherInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      sharedQueue.flush();
    }
  });
  els.artInterval.addEventListener("change", () => {
    rotationQueue.edit({ rotate_minutes: Number(els.artInterval.value) }, { delayMs: 0 });
  });
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

let frameControlVisible = false;

function renderFrameControl() {
  const fc = state.frameControl;
  const available = Boolean(fc && fc.available);

  // Keep showing (Frame screen): the chosen frame's own pin. One button that
  // becomes Unpin; gone when the hub has no frame-control document.
  const pin = selectedFramePin();
  const control = pinControlState(fc, pinSummary(pin, Date.now()) ? pin : null, state.frameShowing.isArt);
  frameControlVisible = control.visible;
  els.keepToggle.textContent = control.label;
  els.keepToggle.classList.toggle("danger", control.pinned);
  els.keepToggle.dataset.pinned = control.pinned ? "1" : "0";
  const summary = control.pinned ? pinSummary(pin, Date.now()) : null;
  els.pinStatus.hidden = !summary;
  els.pinStatus.textContent = summary || "";
  updateSections();

  // Frame settings (Frame screen).
  els.fsUnavailable.hidden = available;
  els.fsBody.hidden = !available;
  if (available) {
    renderFrameSettings(fc);
  }
}

// --------------------------------------------------------------------------- //
// Frame settings. Every field saves itself through an autosave queue
// (autosave-queue.js): one queue per settings object, so the shared settings
// (kinds, morning weather, weather city), each frame's quiet hours, and the art
// rotation each write one request at a time, latest edit wins. Switches and
// chips write at once; time and text fields after a short pause or on blur.
// "Saved" shows next to the setting for a moment; a failure shows "Couldn't
// save." with Retry. A bad time is explained in place and never written.
// --------------------------------------------------------------------------- //

// The editable shared settings the hub last stored, so a write sends only what
// changed (the city is geocoded each time it is sent).
let sharedWritten = {};
let sharedState = "idle";
// The groups the last shared write carried, so "Saved" or Retry shows next to
// the fields actually written, not the field edited last.
let sharedWriteGroups = new Set();
const SHARED_KEY_GROUPS = {
  now_playing_kinds: "kinds",
  quiet_hours: "quiet",
  morning_weather: "morning",
  weather_city: "city",
};
const frameQuietQueues = new Map();
const frameQuietStates = new Map();
let rotationWritten = {};
let rotationState = "idle";

const sharedQueue = createAutosave({
  write: writeSharedSettings,
  onState: (next) => {
    sharedState = next;
    showSharedStatus(next);
  },
});

const rotationQueue = createAutosave({
  write: writeRotation,
  onState: (next) => {
    rotationState = next;
    showSaveStatus("rotation", next, () => rotationQueue.retry());
  },
});

// Send any edit still waiting out its pause (a tab hidden or closed mid-edit).
function flushSettings() {
  sharedQueue.flush();
  rotationQueue.flush();
  climateQueue.flush();
  for (const queue of frameQuietQueues.values()) {
    queue.flush();
  }
}

function showSharedStatus(queueState) {
  const legacy = Boolean(state.frameControl && state.frameControl.legacy);
  const groups = ["kinds", "morning", "city", ...(legacy ? ["quiet"] : [])];
  const writing = queueState === "saved" || queueState === "failed" || queueState === "idle";
  for (const group of groups) {
    const here = writing && sharedWriteGroups.has(group);
    showSaveStatus(group, here ? queueState : "idle", () => sharedQueue.retry());
  }
}

function frameQuietQueue(frame) {
  let queue = frameQuietQueues.get(frame);
  if (!queue) {
    queue = createAutosave({
      write: (obj) => writeFrameQuiet(frame, obj),
      onState: (next) => {
        frameQuietStates.set(frame, next);
        if (frame === state.selectedFrame) {
          showSaveStatus("quiet", next, () => queue.retry());
        }
      },
    });
    frameQuietQueues.set(frame, queue);
  }
  return queue;
}

// A queue's values are safe to overwrite from the hub's document only when
// nothing of the owner's is waiting, in flight, or failed.
function settled(queueState) {
  return queueState === "idle" || queueState === "saved";
}

function showSaveStatus(group, queueState, onRetry) {
  const el = document.querySelector(`[data-status="${group}"]`);
  if (!el) {
    return;
  }
  const info = saveStatus(queueState);
  el.replaceChildren();
  el.classList.toggle("failed", info.retry);
  if (info.text) {
    el.append(document.createTextNode(info.text));
  }
  if (info.retry) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "save-retry";
    button.textContent = "Retry";
    button.addEventListener("click", onRetry);
    el.append(button);
  }
}

function showFieldError(el, message) {
  el.textContent = message;
  el.hidden = false;
}

function clearFieldError(el) {
  el.hidden = true;
  el.textContent = "";
}

// Do not overwrite the field the owner is typing in.
function setFieldValue(input, value) {
  if (document.activeElement !== input) {
    input.value = value;
  }
}

function renderFrameSettings(fc) {
  const settings = fc.settings;
  if (!state.fsKindsBuilt) {
    els.fsKinds.replaceChildren();
    for (const kind of FRAME_KINDS) {
      const chip = document.createElement("button");
      chip.className = "chip";
      chip.type = "button";
      chip.dataset.kind = kind;
      chip.textContent = KIND_LABELS[kind];
      chip.addEventListener("click", () => toggleKind(chip));
      els.fsKinds.append(chip);
    }
    state.fsKindsBuilt = true;
  }

  // Per-frame quiet hours exist on the multi-frame contract; on the old
  // single-frame one the shared window is the only quiet-hours setting.
  const perFrame = !fc.legacy;
  const entry = perFrame ? fc.frames[state.selectedFrame] : null;
  const override = entry ? entry.quietHours : null;
  const quiet = effectiveQuietHours(settings.quiet_hours, override);
  const quietQueue = perFrame ? frameQuietQueue(state.selectedFrame) : null;
  const quietState = perFrame ? frameQuietStates.get(state.selectedFrame) || "idle" : sharedState;
  els.fsQuietEnabled.closest(".switch-field").hidden = !perFrame;
  const rows = frameList(fc, state.mirrors);
  const frameName = rows.find((row) => row.id === state.selectedFrame);
  els.fsQuietNote.textContent =
    perFrame && rows.length > 1 && frameName
      ? `Quiet hours for ${frameName.label}. The frame dims and stops changing.`
      : "During quiet hours the frame dims and stops changing.";
  if (perFrame) {
    showSaveStatus("quiet", quietState, () => quietQueue.retry());
  }

  if (settled(sharedState)) {
    const seed = {
      now_playing_kinds: settings.now_playing_kinds,
      quiet_hours: settings.quiet_hours,
      morning_weather: settings.morning_weather,
      weather_city: settings.weather_location ? settings.weather_location.name : "",
    };
    sharedWritten = JSON.parse(JSON.stringify(seed));
    sharedQueue.seed(seed);

    const kinds = new Set(settings.now_playing_kinds);
    for (const chip of els.fsKinds.querySelectorAll(".chip")) {
      const on = kinds.has(chip.dataset.kind);
      chip.classList.toggle("on", on);
      chip.setAttribute("aria-pressed", on ? "true" : "false");
    }
    els.fsMorningEnabled.checked = settings.morning_weather.enabled;
    els.fsMorningTimes.classList.toggle("disabled", !settings.morning_weather.enabled);
    setFieldValue(els.fsWeekdayStart, settings.morning_weather.weekday[0]);
    setFieldValue(els.fsWeekdayEnd, settings.morning_weather.weekday[1]);
    setFieldValue(els.fsWeekendStart, settings.morning_weather.weekend[0]);
    setFieldValue(els.fsWeekendEnd, settings.morning_weather.weekend[1]);
    setFieldValue(els.fsWeatherInput, seed.weather_city);
  }

  if (settled(quietState)) {
    if (perFrame) {
      quietQueue.seed({ quiet_hours: override });
    }
    els.fsQuietEnabled.checked = quiet.enabled;
    els.fsQuietReset.hidden = !(perFrame && override !== null);
    els.fsQuietStart.disabled = !quiet.enabled;
    els.fsQuietEnd.disabled = !quiet.enabled;
    setFieldValue(els.fsQuietStart, quiet.start);
    setFieldValue(els.fsQuietEnd, quiet.end);
  }
}

// Toggle one now-playing kind. The chip flips at once and the write follows.
function toggleKind(chip) {
  const on = !chip.classList.contains("on");
  chip.classList.toggle("on", on);
  chip.setAttribute("aria-pressed", on ? "true" : "false");
  const chosen = new Set(
    [...els.fsKinds.querySelectorAll(".chip.on")].map((c) => c.dataset.kind)
  );
  sharedQueue.edit({ now_playing_kinds: FRAME_KINDS.filter((k) => chosen.has(k)) }, { delayMs: 0 });
}

function flushQuietHours() {
  const fc = state.frameControl;
  if (fc && !fc.legacy) {
    frameQuietQueue(state.selectedFrame).flush();
  } else {
    sharedQueue.flush();
  }
}

// The quiet-hours switch and times. A switch writes at once, a time after a
// pause. An invalid time stays in the field with an explanation and no write.
function editQuietHours(immediate) {
  const fc = state.frameControl;
  if (!fc || !fc.available) {
    return;
  }
  const perFrame = !fc.legacy;
  const enabled = perFrame ? els.fsQuietEnabled.checked : true;
  els.fsQuietStart.disabled = !enabled;
  els.fsQuietEnd.disabled = !enabled;
  const override = quietOverrideFromFields({
    enabled,
    start: els.fsQuietStart.value,
    end: els.fsQuietEnd.value,
  });
  if (override === undefined) {
    showFieldError(els.fsQuietError, "Use 24-hour times like 07:30 for quiet hours.");
    return;
  }
  clearFieldError(els.fsQuietError);
  const options = immediate ? { delayMs: 0 } : undefined;
  if (perFrame) {
    frameQuietQueue(state.selectedFrame).edit({ quiet_hours: override }, options);
  } else {
      sharedQueue.edit({ quiet_hours: override }, options);
  }
}

// Give the chosen frame back to the default hours: writes quiet_hours null
// through the same queue, so it saves, fails and retries like any other field.
function resetQuietHours() {
  const fc = state.frameControl;
  if (!fc || !fc.available || fc.legacy) {
    return;
  }
  clearFieldError(els.fsQuietError);
  els.fsQuietReset.hidden = true;
  frameQuietQueue(state.selectedFrame).edit(
    { quiet_hours: quietOverrideFromFields({ reset: true }) },
    { delayMs: 0 }
  );
}

function editMorning(immediate) {
  const morning = {
    enabled: els.fsMorningEnabled.checked,
    weekday: [els.fsWeekdayStart.value, els.fsWeekdayEnd.value],
    weekend: [els.fsWeekendStart.value, els.fsWeekendEnd.value],
  };
  els.fsMorningTimes.classList.toggle("disabled", !morning.enabled);
  const check = validateFrameSettings({ morning_weather: morning });
  if (!check.ok) {
    showFieldError(els.fsMorningError, check.error);
    return;
  }
  clearFieldError(els.fsMorningError);
  sharedQueue.edit({ morning_weather: check.value.morning_weather }, immediate ? { delayMs: 0 } : undefined);
}

function editWeatherCity() {
  clearFieldError(els.fsWeatherError);
  sharedQueue.edit({ weather_city: els.fsWeatherInput.value.trim() });
}

// The write behind the shared queue: only the settings that differ from what
// the hub holds, so an unchanged city is not geocoded again. A failure throws
// so the queue shows Retry; a city the hub could not find is explained in place.
async function writeSharedSettings(obj) {
  const changed = changedKeys(sharedWritten, obj);
  sharedWriteGroups = new Set(changed.map((key) => SHARED_KEY_GROUPS[key]).filter(Boolean));
  const subset = {};
  for (const key of ["now_playing_kinds", "quiet_hours", "morning_weather"]) {
    if (changed.includes(key)) {
      subset[key] = obj[key];
    }
  }
  const check = validateFrameSettings(subset);
  if (!check.ok) {
    throw new Error(check.error);
  }
  const body = { ...check.value };
  const cityChanged = changed.includes("weather_city");
  if (cityChanged) {
    Object.assign(body, obj.weather_city ? weatherSaveBody(obj.weather_city) : weatherClearBody());
  }
  if (Object.keys(body).length === 0) {
    return;
  }
  const result = await postApi("/api/frame/settings", body);
  if (!result.ok) {
    if (cityChanged) {
      showFieldError(els.fsWeatherError, result.error);
    }
    throw new Error(result.error);
  }
  clearFieldError(els.fsWeatherError);
  sharedWritten = JSON.parse(JSON.stringify({ ...sharedWritten, ...obj }));
  refreshFrameControl();
}

async function writeFrameQuiet(frame, obj) {
  const built = frameQuietHoursBody(frame, obj.quiet_hours);
  if (!built.ok) {
    showFieldError(els.fsQuietError, built.error);
    throw new Error(built.error);
  }
  const result = await postApi("/api/frame/settings", built.value);
  if (!result.ok) {
    showFieldError(els.fsQuietError, result.error);
    throw new Error(result.error);
  }
  clearFieldError(els.fsQuietError);
  refreshFrameControl();
}

// The rotation write: the change-every interval and each folder switch, one
// request per changed key, in order.
async function writeRotation(obj) {
  for (const key of changedKeys(rotationWritten, obj)) {
    let result;
    if (key === "rotate_minutes") {
      result = await postApi("/api/art/settings", { rotate_minutes: obj[key] });
    } else if (key.startsWith("folder:")) {
      result = await postApi("/api/art/folder/enable", { name: key.slice(7), enabled: obj[key] });
    } else {
      continue;
    }
    if (!result.ok) {
      throw new Error(result.error);
    }
    rotationWritten = { ...rotationWritten, [key]: obj[key] };
  }
  refreshArt();
  refreshFrame();
  refreshLibrary();
}

async function toggleKeepShowing() {
  if (els.keepToggle.dataset.pinned === "1") {
    await unpinFrame();
    return;
  }
  const body = keepShowingBody(state.selectedFrame);
  if (!body) {
    return;
  }
  const result = await postApi("/api/frame/pin", body);
  showToast(result.ok ? "Keeping this on the frame until you unpin it." : result.error);
  await refreshFrameControl();
  refreshFrame();
}

// The full choice behind "For how long...": what to keep, and for how long.
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
  } else {
    els.keepMore.open = false;
  }
  await refreshFrameControl();
  refreshFrame();
}

async function unpinFrame() {
  const body = frameUnpinBody(state.selectedFrame);
  if (!body) {
    return;
  }
  const before = selectedFramePin();
  const undoBody = repinBody(state.selectedFrame, before, Date.now());
  const result = await postApi("/api/frame/unpin", body);
  if (result.ok) {
    showToast(
      "Unpinned.",
      undoBody
        ? async () => {
            const back = await postApi("/api/frame/pin", undoBody);
            if (!back.ok) {
              showToast(back.error);
            }
            await refreshFrameControl();
            refreshFrame();
          }
        : null,
    );
  } else {
    showToast(result.error);
  }
  await refreshFrameControl();
  refreshFrame();
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
    els.pairInstruction.textContent = pairInstruction(state.btName);
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
    const id = historyPieceId(piece);
    if (id && state.historyHidden.has(id)) {
      continue;
    }
    els.artHistory.append(artHistoryRow(piece));
  }
}

// A history row is one button that opens the picture in the viewer. The title
// is plain text here (a link inside a button is not valid); the viewer is where
// the actions live.
function historyPieceId(piece) {
  const id = historyDeleteTarget(piece);
  return id ? (state.historyMoved.get(id) ?? id) : null;
}

function artHistoryRow(piece) {
  const li = document.createElement("li");
  const id = historyPieceId(piece);

  const button = document.createElement("button");
  button.className = "row row-tappable";
  button.type = "button";
  button.setAttribute("aria-label", `Open ${piece.title}`);
  if (id) {
    li.dataset.pieceId = id;
    button.addEventListener("click", () => openViewer(id, "history"));
  } else {
    button.disabled = true;
  }

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
  button.append(thumb);

  const meta = document.createElement("div");
  meta.className = "row-meta";
  const title = document.createElement("span");
  title.className = "row-title";
  title.textContent = piece.title;
  meta.append(title);
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
  button.append(meta);

  li.append(button);
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
  updateNowPlayingCard(withPlayHold(nowPlayingFromBody(body)));
}

// Keep the status a play/pause tap set while its hold runs, so a read that has
// not caught up yet does not flip the icon back.
function withPlayHold(track) {
  if (!track || !state.playHold) return track;
  track.status = heldStatus(track.status, state.playHold, Date.now());
  return track;
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
  renderPlaybackControls();
  setOptionalLine(els.npSource, [track.artist, track.album].filter(Boolean).join(" \u00b7 "));
  tickNowPlaying();
}

// Back, play/pause and forward for the phone's Bluetooth playback. Hidden for
// any other source. The play/pause icon follows the track's status.
function renderPlaybackControls() {
  const controls = playbackControls(state.nowTrack);
  els.npControls.hidden = !controls.visible;
  if (!controls.visible) return;
  els.npPlayPause.dataset.state = controls.playing ? "playing" : "paused";
  els.npPlayPause.setAttribute("aria-label", controls.playing ? "Pause" : "Play");
}

// Send one command. The request goes over HTTP so a refusal (the phone is gone)
// comes back as an error to show. Play/pause flips its icon at once; the next
// now-playing refresh settles the truth.
function onPlayback(command) {
  const track = state.nowTrack;
  if (!track || track.source !== "bluetooth") return;
  haptics.playPause();
  if (command === "playPause") {
    track.status = track.status === "paused" ? "playing" : "paused";
    state.playHold = { status: track.status, untilMs: Date.now() + PLAY_PAUSE_HOLD_MS };
    renderPlaybackControls();
    tickNowPlaying();
  } else {
    // Next and previous change the track, so an earlier play/pause hold no longer applies.
    state.playHold = null;
  }
  const request = playbackRequest(command);
  postRpc(request.method, request.params, interpretControlResponse)
    .catch(() => showToast("Couldn't reach your phone"))
    .then(() => setTimeout(refreshNowPlaying, 800));
}

function setProgressFill(fraction) {
  els.npFill.style.transform = `scaleX(${Math.max(0, Math.min(1, fraction)).toFixed(4)})`;
}

function writeProgressText(progress, paused) {
  els.npElapsed.textContent = progressLabel(
    state.nowTrack,
    `${formatDuration(progress.elapsed)} / ${formatDuration(progress.total)}`,
  );
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

  const when = formatStartTime(play.lastPlayedAt);
  if (when) {
    const time = document.createElement("time");
    time.className = "row-when";
    time.dateTime = play.lastPlayedAt;
    time.textContent = historyWhenText(play, when);
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

  // When a held refresh is pending, resume it once the viewer's select loses focus.
  els.galleryViewer.addEventListener("focusout", maybeResumeLibraryRefresh);

  wireViewer();
}

function libraryMenuBusy() {
  // No menus remain; a focused select in the viewer still holds a periodic
  // rebuild so its list is not replaced while it is open.
  const active = document.activeElement;
  const selectFocused = active instanceof HTMLSelectElement && els.galleryViewer.contains(active);
  return { menuOpen: false, selectFocused };
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
  if (settled(rotationState)) {
    els.artInterval.value = String(control.rotateMinutes);
    const seed = { rotate_minutes: control.rotateMinutes };
    for (const folder of index.folders) {
      seed[`folder:${folder.name}`] = control.foldersEnabled.includes(folder.name);
    }
    rotationWritten = seed;
    rotationQueue.seed(seed);
  }

  const summary = rotationSummary(index, control);
  els.artRotationSummary.textContent = summary.text;
  els.artRotationSummary.classList.toggle("empty", summary.empty);
  renderFolderChips(index, control);

  // The library count, in plain words.
  const total = index.pieces.length;
  if (total === 0) {
    els.libraryCount.textContent = "No pictures yet";
  } else {
    const noun = total === 1 ? "picture" : "pictures";
    els.libraryCount.textContent = `${total} ${noun}, ${summary.count} in rotation`;
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
  syncViewer();
}

function renderFolderChips(index, control) {
  if (!settled(rotationState)) {
    return;
  }
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
      const next = chip.getAttribute("aria-pressed") !== "true";
      chip.setAttribute("aria-pressed", next ? "true" : "false");
      chip.classList.toggle("on", next);
      rotationQueue.edit({ [`folder:${folder.name}`]: next }, { delayMs: 0 });
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
    create: (piece) => libraryTile(piece),
  });
  els.artGridEmpty.hidden = pieces.length > 0;

  els.libraryPager.hidden = count <= 1;
  els.libPage.textContent = `Page ${state.libPage + 1} of ${count}`;
  els.libPrev.disabled = state.libPage === 0;
  els.libNext.disabled = state.libPage >= count - 1;
}

function libraryTile(piece) {
  const li = document.createElement("li");
  li.className = "tile";
  li.dataset.pieceId = piece.id;

  const open = document.createElement("button");
  open.className = "tile-open";
  open.type = "button";
  open.setAttribute("aria-label", `Open ${piece.title}`);
  open.addEventListener("click", () => openViewer(piece.id));

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
  open.append(wrap);

  const title = document.createElement("span");
  title.className = "tile-title";
  title.textContent = piece.title;
  open.append(title);

  if (piece.artist) {
    const artist = document.createElement("span");
    artist.className = "tile-artist";
    artist.textContent = piece.artist;
    open.append(artist);
  }

  li.append(open);
  return li;
}

function renderFolderList(index) {
  // Rebuild only when something visible changed, so a name being typed into a
  // rename field survives a refresh that changes nothing.
  const folders = index.folders.filter((f) => !f.builtin);
  const sig = JSON.stringify([folders.map((f) => [f.name, f.count]), state.folderConfirm]);
  if (sig === state.folderSig) {
    return;
  }
  state.folderSig = sig;
  els.artFolderList.replaceChildren();
  for (const folder of folders) {
    const li = document.createElement("li");
    li.className = "folder-row";

    if (state.folderConfirm === folder.name) {
      const ask = document.createElement("p");
      ask.className = "folder-ask";
      const noun = folder.count === 1 ? "picture" : "pictures";
      ask.textContent = `Remove ${folder.name} and move its ${folder.count} ${noun} to the trash?`;
      li.append(ask);
      const yes = document.createElement("button");
      yes.className = "btn btn-small danger";
      yes.type = "button";
      yes.textContent = "Remove folder";
      yes.addEventListener("click", () => {
        state.folderConfirm = null;
        deleteFolderWithPictures(folder);
      });
      const no = document.createElement("button");
      no.className = "btn btn-small";
      no.type = "button";
      no.textContent = "Cancel";
      no.addEventListener("click", () => {
        state.folderConfirm = null;
        renderFolderList(state.artIndex);
      });
      li.append(yes, no);
      els.artFolderList.append(li);
      continue;
    }

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
    del.textContent = "Remove";
    del.setAttribute("aria-label", `Remove ${folder.name}`);
    del.addEventListener("click", () => {
      if (folder.count > 0) {
        // Still holds pictures: ask first (no Undo for this one).
        state.folderConfirm = folder.name;
        renderFolderList(state.artIndex);
        return;
      }
      runAction("/api/art/folder/delete", { name: folder.name });
    });
    li.append(del);

    els.artFolderList.append(li);
  }
}

// The hub only removes an empty folder, so a folder with pictures has them moved
// to the trash first (each restorable for 30 days, which recreates the folder).
async function deleteFolderWithPictures(folder) {
  const pieces = (state.artIndex ? state.artIndex.pieces : []).filter((p) => p.folder === folder.name);
  for (const piece of pieces) {
    // Same path as the single-picture trash: move the frame on first if it is
    // showing this picture.
    await advanceFrameIfShowing(piece);
    const result = await postApi("/api/art/delete", { id: piece.id });
    if (!result.ok) {
      showToast(result.error);
      refreshLibrary();
      return;
    }
  }
  await runAction("/api/art/folder/delete", { name: folder.name });
  showToast("Folder deleted. Its pictures are in the trash.");
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
      time.textContent = `Trashed ${when}`;
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
    showToast("This picture can't be moved to the trash from here.");
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
    showToast("This picture can't be deleted.");
    return false;
  }
  const result = await postApi("/api/art/delete", { id: target });
  refreshArt();
  refreshFrame();
  await refreshLibrary();
  if (!result.ok) {
    showToast(result.error);
    return false;
  }
  // Undo is keyed on the delete target (the piece id), which is what the hub
  // records as the trash entry's `original`. Keying on the basename instead
  // silently failed to restore any piece that lived in a user folder.
  showToast("Moved to trash.", () => undoTrash(target));
  return true;
}

async function undoTrash(target) {
  state.historyHidden.delete(target);
  renderArtHistory(state.artHistory);
  await refreshLibrary();
  const index = state.artIndex;
  const restoreId = index ? trashRestoreId(index.trash, target) : null;
  if (!restoreId) {
    showToast("Couldn't undo. Restore it from the Trash list.");
    return;
  }
  runAction("/api/art/restore", { id: restoreId });
}

// Open the gallery grid, filter to the current piece's folder, page to it, and
// scroll there, so "Show in library" lands on the piece.
function showInLibrary(current) {
  const index = state.artIndex;
  if (!index || !current) {
    return;
  }
  const piece = index.pieces.find((p) => (current.id && p.id === current.id) || p.file === current.file);
  state.viewerPushed = false;
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

// --------------------------------------------------------------------------- //
// The Gallery viewer: one picture, large, in the Gallery tab. It has its own
// hash entry (#gallery/<id>), so Back closes it and the grid is where it was.
// The pure rules (position, edges, swipe) are in gallery-viewer.js.
// --------------------------------------------------------------------------- //

// The pieces the viewer steps through: the grid's current folder filter, or
// every piece when the opened one is not in it (a link, or a folder change).
function viewerList() {
  if (state.viewerKind === "history") {
    return historyViewerItems();
  }
  const index = state.artIndex;
  if (!index) {
    return [];
  }
  const filtered = piecesInFolder(index, state.artFilter);
  if (state.viewerId && !filtered.some((p) => p.id === state.viewerId)) {
    return index.pieces;
  }
  return filtered;
}

// The frame's history as viewer items: newest first, one per picture, without
// the ones removed this session. The library's own URL is used when it knows the
// piece (a folder-deep one is not reachable at the share root).
function historyViewerItems() {
  const seen = new Set();
  const items = [];
  for (const piece of state.artHistory) {
    const id = historyPieceId(piece);
    if (!id || seen.has(id) || state.historyHidden.has(id)) {
      continue;
    }
    seen.add(id);
    const known = state.artIndex ? state.artIndex.pieces.find((p) => p.id === id) : null;
    items.push({
      id,
      file: piece.file,
      title: piece.title,
      artist: piece.artist,
      source_url: piece.source_url,
      origId: historyDeleteTarget(piece),
      folder: known ? known.folder : "",
      url: known ? known.url : artUrl(piece.file),
    });
  }
  return items;
}

function viewerIndexNow(list) {
  return list.findIndex((p) => p.id === state.viewerId);
}

function openViewer(pieceId, kind = "grid") {
  state.viewerKind = kind;
  state.gridScroll = window.scrollY;
  state.viewerPushed = true;
  window.location.hash = galleryHash(pieceId);
}

// Make the view match the hash: show the piece it names, or the grid. Runs after
// every route change and every library render.
function syncViewer() {
  const id = state.screen === "gallery" ? pieceIdFromHash(window.location.hash) : null;
  const wasOpen = !els.galleryViewer.hidden;
  if (!id) {
    closeViewerView(wasOpen);
    return;
  }
  if (state.viewerKind === "grid" && !state.artIndex) {
    return;
  }
  state.viewerId = id;
  const list = viewerList();
  const idx = viewerIndexNow(list);
  if (idx < 0) {
    history.replaceState(null, "", "#gallery");
    closeViewerView(wasOpen);
    return;
  }
  els.galleryGridView.hidden = true;
  els.galleryViewer.hidden = false;
  if (!wasOpen) {
    window.scrollTo({ top: 0, behavior: "auto" });
  }
  renderViewer(list, idx);
  if (!wasOpen) {
    els.viewerClose.focus({ preventScroll: true });
  }
}

function closeViewerView(wasOpen) {
  const lastId = state.viewerId;
  state.viewerId = null;
  state.viewerKind = "grid";
  els.galleryViewer.hidden = true;
  els.galleryGridView.hidden = false;
  if (wasOpen) {
    window.scrollTo({ top: state.gridScroll, behavior: "auto" });
    const tile = lastId
      ? document.querySelector(
          `[data-piece-id="${CSS.escape(lastId)}"] .tile-open, [data-piece-id="${CSS.escape(lastId)}"] .row-tappable`,
        )
      : null;
    if (tile) {
      tile.focus({ preventScroll: true });
    }
  }
}

function renderViewer(list, idx) {
  const piece = list[idx];
  const view = viewerState(list, idx, state.viewerKind);
  const fromHistory = state.viewerKind === "history";
  els.viewerClose.textContent = fromHistory ? "Back to frame" : "Back to gallery";
  els.viewerPosition.textContent = view.position;
  if (els.viewerImage.getAttribute("src") !== piece.url) {
    els.viewerImage.src = piece.url;
  }
  els.viewerImage.alt = piece.title;
  renderViewerTitle(piece);
  els.viewerSub.textContent = [piece.artist, piece.folder].filter(Boolean).join(", ");
  els.viewerPrev.disabled = !view.canPrev;
  els.viewerNext.disabled = !view.canNext;
  els.viewerShow.disabled = view.showBlockedReason !== null;
  els.viewerPin.disabled = view.showBlockedReason !== null;
  els.viewerNote.hidden = view.showBlockedReason === null;
  els.viewerNote.textContent = view.showBlockedReason || "";

  const others = !state.artIndex || !piece.folder ? [] : state.artIndex.folders.map((f) => f.name).filter((n) => n !== piece.folder);
  const sig = JSON.stringify(others);
  if (els.viewerMove.dataset.sig !== sig) {
    els.viewerMove.dataset.sig = sig;
    els.viewerMove.replaceChildren();
    const first = document.createElement("option");
    first.value = "";
    first.textContent = "Choose a folder";
    els.viewerMove.append(first);
    for (const name of others) {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      els.viewerMove.append(option);
    }
  }
  els.viewerMove.value = "";
  els.viewerMove.disabled = others.length === 0;

  // Warm the neighbours so a step does not wait on the network.
  for (const near of [list[idx - 1], list[idx + 1]]) {
    if (near) {
      new Image().src = near.url;
    }
  }
}

// The title, a link to the catalogue page when the piece carries one (same
// rel and target as every other source link).
function renderViewerTitle(piece) {
  els.viewerTitle.replaceChildren();
  if (piece.source_url) {
    const link = document.createElement("a");
    link.textContent = piece.title;
    link.href = piece.source_url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    els.viewerTitle.append(link);
  } else {
    els.viewerTitle.textContent = piece.title;
  }
}

function stepViewer(dir) {
  const list = viewerList();
  const idx = viewerIndexNow(list);
  const next = stepIndex(idx, list.length, dir);
  if (idx < 0 || next === idx) {
    return;
  }
  state.viewerId = list[next].id;
  // Replace, not push: Back still closes the viewer in one step.
  history.replaceState(null, "", galleryHash(state.viewerId));
  renderViewer(list, next);
}

function closeViewer() {
  if (state.viewerPushed) {
    state.viewerPushed = false;
    history.back();
    return;
  }
  history.replaceState(null, "", "#gallery");
  syncViewer();
}

// After the shown piece left the list (trashed, or moved out of this folder),
// land on whatever took its place, or close when nothing is left.
function viewerAfterRemoval(idx, source) {
  const index = state.artIndex;
  // The same source the index was taken from, so the two always agree.
  const list = !index ? [] : source === "all" ? index.pieces : piecesInFolder(index, state.artFilter);
  const id = idAfterRemoval(list, idx);
  if (id === null) {
    closeViewer();
    return;
  }
  state.viewerId = id;
  history.replaceState(null, "", galleryHash(state.viewerId));
  syncViewer();
}

function currentViewerPiece() {
  const list = viewerList();
  const idx = viewerIndexNow(list);
  if (idx < 0) {
    return null;
  }
  if (state.viewerKind === "history") {
    return { piece: list[idx], idx, source: "history" };
  }
  const filtered = state.artIndex ? piecesInFolder(state.artIndex, state.artFilter) : [];
  return { piece: list[idx], idx, source: viewerSource(filtered, state.viewerId) };
}

// If the frame is showing this piece now, move it on first so it is gone from
// the panel in one redraw (as the frame's own Delete does).
async function advanceFrameIfShowing(piece) {
  const current = state.artCurrent;
  if (!current) {
    return;
  }
  const same = (current.id && current.id === piece.id) || current.file === piece.file;
  const command = same ? artCommandBody(state.selectedFrame, "next") : null;
  if (command) {
    await postApi("/api/art/command", command);
  }
}

async function keepOnFrame(piece) {
  const show = artCommandBody(state.selectedFrame, "show", piece.id);
  const pin = framePinBody(state.selectedFrame, "art", "forever");
  if (!show || !pin) {
    return;
  }
  let result = await postApi("/api/art/command", show);
  if (result.ok) {
    result = await postApi("/api/frame/pin", pin);
  }
  showToast(result.ok ? "On the frame until you unpin it." : result.error);
  refreshArt();
  refreshFrame();
  refreshFrameControl();
}

async function moveViewerPiece(folder) {
  const here = currentViewerPiece();
  if (!here || !folder) {
    return;
  }
  const { piece, idx, source } = here;
  await advanceFrameIfShowing(piece);
  const result = await postApi("/api/art/move", { id: piece.id, folder });
  if (!result.ok) {
    showToast(result.error);
    renderViewer(viewerList(), idx);
    return;
  }
  refreshArt();
  refreshFrame();
  await refreshLibrary();
  const movedId = movedPieceId(state.artIndex, folder, piece);
  showToast(
    `Moved to ${folder}.`,
    movedId ? () => runAction("/api/art/move", { id: movedId, folder: piece.folder }) : null,
  );
  if (source === "history") {
    // The piece is still in the history under a new id: follow it.
    if (!movedId) {
      closeViewer();
      return;
    }
    state.historyMoved.set(piece.origId, movedId);
    renderArtHistory(state.artHistory);
    state.viewerId = movedId;
    history.replaceState(null, "", galleryHash(movedId));
    syncViewer();
    return;
  }
  viewerAfterRemoval(idx, source);
}

async function trashViewerPiece() {
  const here = currentViewerPiece();
  if (!here) {
    return;
  }
  const rest = viewerList().filter((p) => p.id !== here.piece.id);
  await advanceFrameIfShowing(here.piece);
  if (!(await moveToTrash(here.piece.id))) {
    return;
  }
  if (here.source !== "history") {
    viewerAfterRemoval(here.idx, here.source);
    return;
  }
  // A history piece: hide it from the log for this session, then land on the
  // next entry (the hub's log may still list a trashed piece).
  state.historyHidden.add(here.piece.id);
  renderArtHistory(state.artHistory);
  const id = idAfterRemoval(rest, here.idx);
  if (id === null) {
    closeViewer();
    return;
  }
  state.viewerId = id;
  history.replaceState(null, "", galleryHash(id));
  syncViewer();
}

function wireViewer() {
  els.viewerClose.addEventListener("click", closeViewer);
  els.viewerPrev.addEventListener("click", () => stepViewer(-1));
  els.viewerNext.addEventListener("click", () => stepViewer(1));
  els.viewerShow.addEventListener("click", () => {
    const here = currentViewerPiece();
    if (here) {
      runArtCommand("show", here.piece.id);
      showToast("Showing on the frame.");
    }
  });
  els.viewerPin.addEventListener("click", () => {
    const here = currentViewerPiece();
    if (here) {
      keepOnFrame(here.piece);
    }
  });
  els.viewerMove.addEventListener("change", () => moveViewerPiece(els.viewerMove.value));
  els.viewerTrash.addEventListener("click", trashViewerPiece);

  // Swipe: the finger moves left to go on. Mostly vertical moves are left to
  // the page (the stage allows vertical panning only).
  let start = null;
  els.viewerStage.addEventListener("pointerdown", (event) => {
    start = { x: event.clientX, y: event.clientY };
  });
  els.viewerStage.addEventListener("pointerup", (event) => {
    if (!start) {
      return;
    }
    const dir = swipeDirection(start.x - event.clientX, event.clientY - start.y);
    start = null;
    if (dir !== 0) {
      stepViewer(dir);
    }
  });
  els.viewerStage.addEventListener("pointercancel", () => {
    start = null;
  });

  document.addEventListener("keydown", (event) => {
    if (els.galleryViewer.hidden || event.defaultPrevented) {
      return;
    }
    const tag = event.target instanceof Element ? event.target.tagName : "";
    if (tag === "SELECT" || tag === "INPUT" || tag === "TEXTAREA") {
      return;
    }
    if (event.key === "ArrowLeft") {
      stepViewer(-1);
    } else if (event.key === "ArrowRight") {
      stepViewer(1);
    } else if (event.key === "Escape") {
      closeViewer();
    }
  });
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

  // Keep showing needs a frame to pin and the frame-control document.
  els.pin.hidden = !frameVisible || !frameControlVisible;

  els.library.hidden = !managerActive;
  els.rotation.hidden = !managerActive;
  els.upload.hidden = !managerActive;
  els.trash.hidden = !managerActive;

  els.artHistorySection.hidden = state.artHistory.length === 0;
}

// The audio server's reachability. While it is down the speaker banner shows and
// the room controls are disabled; the next good status read clears both.
function setSpeakers(connection) {
  if (state.speakers === connection) {
    return;
  }
  state.speakers = connection;
  const message = speakerBanner(connection);
  els.speakers.textContent = message || "";
  els.speakers.hidden = message === null;
  // One calm banner at a time: the slow-updates line waits while the speaker
  // banner shows.
  setConnection(state.connectionMessage);
  renderListen();
}

function renderNotSetUp(hosts) {
  els.notSetUp.hidden = hosts.length === 0;
  els.notSetUpList.replaceChildren(
    ...hosts.map((host) => {
      const li = document.createElement("li");
      li.className = "row";
      li.textContent = host;
      return li;
    }),
  );
}

function renderNeedsAttention(troubled) {
  els.needsAttention.hidden = troubled.length === 0;
  els.needsAttentionList.replaceChildren(
    ...troubled.map(({ host, reason }) => {
      const li = document.createElement("li");
      li.className = "row";
      li.textContent = `${host}: ${reasonLabel(reason)}`;
      return li;
    }),
  );
}

function setConnection(message) {
  state.connectionMessage = message;
  els.connection.textContent = message;
  els.connection.hidden = message === "" || state.speakers === "down";
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

// --------------------------------------------------------------------------- //
// Climate: who is home, the thermostat, and the automatic-change settings. The
// rules are in climate-model.js; this part reads the hub's homebody.json, draws
// it, and saves the settings through one autosave queue.
// --------------------------------------------------------------------------- //

const climateEls = {
  stale: document.getElementById("cl-stale"),
  known: document.getElementById("cl-known"),
  people: document.getElementById("cl-people"),
  peopleEmpty: document.getElementById("cl-people-empty"),
  device: document.getElementById("cl-device"),
  deviceText: document.getElementById("cl-device-text"),
  deviceAdd: document.getElementById("cl-device-add"),
  deviceForm: document.getElementById("cl-device-form"),
  deviceName: document.getElementById("cl-device-name"),
  deviceGo: document.getElementById("cl-device-go"),
  deviceCancel: document.getElementById("cl-device-cancel"),
  deviceError: document.getElementById("cl-device-error"),
  panel: document.getElementById("cl-thermostat-panel"),
  temp: document.getElementById("cl-temp"),
  mode: document.getElementById("cl-mode"),
  setpointWrap: document.getElementById("cl-setpoint-wrap"),
  setpoint: document.getElementById("cl-setpoint"),
  message: document.getElementById("cl-thermo-msg"),
  last: document.getElementById("cl-last"),
  override: document.getElementById("cl-override"),
  enabled: document.getElementById("cl-enabled"),
  error: document.getElementById("cl-error"),
  inputs: {
    comfort_f: document.getElementById("cl-comfort"),
    away_f: document.getElementById("cl-away"),
    away_minutes: document.getElementById("cl-delay"),
  },
};

// What the hub last stored (the flat object the queue writes against), the
// values the owner intends (so the comfort-below-away rule sees edits not yet
// saved), the fields refused inline, and a tick counter for the idle poll.
let climateWritten = {};
let climateDraft = {};
let climateRefused = new Map();
let climateState = "idle";
let climateTicks = 0;
let climatePeopleKey = "";
// People controls: what this phone is (null: the hub cannot say, so no
// controls), whether the name form is open, the row asking to confirm a remove,
// and whether a people write is in flight.
let climateDevice = null;
let climateAdding = false;
let climateRemoveId = null;
let climatePeopleBusy = false;

const climateQueue = createAutosave({
  write: writeClimate,
  onState: (next) => {
    climateState = next;
    showSaveStatus("climate", next, () => climateQueue.retry());
    if (settled(next)) {
      renderClimate();
    }
  },
});

function climateFlat(doc) {
  return { enabled: doc.enabled, ...doc.settings };
}

async function refreshClimate() {
  let doc = null;
  try {
    const response = await fetch("homebody.json", { cache: "no-store" });
    if (response.ok) {
      doc = parseClimateText(await response.text());
    }
  } catch (_) {
    doc = null;
  }
  applyClimate(doc);
  refreshThisDevice();
}

// Ask the hub what this phone is to the climate automation. A 404 (a hub that
// has not got the endpoint yet), an error or a malformed reply means no controls.
async function refreshThisDevice() {
  let device = null;
  try {
    const response = await fetch("api/homebody/people/this-device", { cache: "no-store" });
    if (response.ok) {
      device = parseThisDevice(safeJsonParse(await response.text()));
    }
  } catch (_) {
    device = null;
  }
  climateDevice = device;
  renderClimate();
}

// Take a new document (a poll, or the reply to a write) and draw it. A missing
// or unreadable one hides the tab, and leaves the screen if it was showing.
function applyClimate(doc) {
  state.climate = doc;
  state.climateProbed = true;
  if (doc) {
    climateWritten = climateFlat(doc);
    climateQueue.seed(climateWritten);
    if (settled(climateState) && climateRefused.size === 0) {
      climateDraft = { ...doc.settings };
    }
    renderClimate();
  }
  if (state.screen === "climate" && !navVisible().climate) {
    applyHash();
  } else {
    syncTabs();
  }
}

function climateTick() {
  climateTicks += 1;
  if (state.screen === "climate" || climateTicks % CLIMATE_IDLE_EVERY === 0) {
    refreshClimate();
  }
}

function renderClimate() {
  const doc = state.climate;
  if (!doc) {
    return;
  }
  const now = Date.now();

  const { stale, text: staleText } = staleness(doc, now);
  climateEls.stale.hidden = staleText === "";
  climateEls.stale.textContent = staleText;
  climateEls.people.classList.toggle("last-known", stale);

  const lines = peopleLines(doc, now, stale);
  const key = JSON.stringify([lines, climateRemoveId, climatePeopleBusy]);
  if (key !== climatePeopleKey) {
    climatePeopleKey = key;
    climateEls.people.replaceChildren(
      ...lines.map((line) => {
        const li = document.createElement("li");
        li.className = "cl-person";
        li.dataset.state = line.state;
        const dot = document.createElement("span");
        dot.className = "cl-dot";
        dot.setAttribute("aria-hidden", "true");
        const name = document.createElement("span");
        name.className = "cl-person-name";
        name.textContent = line.name;
        const label = document.createElement("span");
        label.className = "cl-person-state";
        label.textContent = line.label;
        li.append(dot, name, label);
        if (line.seen) {
          const seen = document.createElement("p");
          seen.className = "cl-person-seen";
          seen.textContent = line.seen;
          li.append(seen);
        }
        if (line.removable) {
          li.append(personRemoveControl(line));
        }
        return li;
      })
    );
  }
  climateEls.people.hidden = lines.length === 0;
  climateEls.peopleEmpty.hidden = lines.length > 0;
  climateEls.peopleEmpty.textContent = lines.length > 0 ? "" : EMPTY_PEOPLE_TEXT;

  renderDevice(doc);

  const thermo = thermostatView(doc, stale);
  climateEls.panel.dataset.status = thermo.status;
  climateEls.panel.classList.toggle("last-known", thermo.lastKnown);
  climateEls.known.hidden = !thermo.lastKnown;
  climateEls.known.textContent = thermo.lastKnownLabel;
  climateEls.temp.textContent = thermo.temperature;
  climateEls.mode.textContent = thermo.mode;
  climateEls.setpoint.textContent = thermo.setpoint;
  climateEls.setpointWrap.hidden = thermo.setpoint === "--";
  climateEls.message.hidden = thermo.message === "";
  climateEls.message.textContent = thermo.message;
  climateEls.message.classList.toggle("checking", thermo.status === "checking");
  climateEls.last.textContent = lastChangeText(doc, now);
  const override = overrideText(doc);
  climateEls.override.hidden = override === "";
  climateEls.override.textContent = override;

  // The owner's pending, failed or refused edits are never overwritten.
  if (settled(climateState)) {
    setClimateSwitch(doc.enabled);
    for (const [name, input] of Object.entries(climateEls.inputs)) {
      if (!climateRefused.has(name)) {
        setFieldValue(input, String(doc.settings[name]));
      }
    }
  }
}

function personRemoveControl(line) {
  const box = document.createElement("div");
  box.className = "cl-person-actions";
  const button = (label, cls, onClick, disabled = false) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = cls;
    b.textContent = label;
    b.disabled = disabled;
    b.addEventListener("click", onClick);
    return b;
  };
  if (climateRemoveId === line.id) {
    const q = document.createElement("span");
    q.className = "cl-person-q";
    q.textContent = `Stop counting ${line.name}?`;
    box.append(
      q,
      button("Remove", "btn btn-small danger", () => removePerson(line.id), climatePeopleBusy),
      button("Keep", "btn-ghost btn-small", () => {
        climateRemoveId = null;
        renderClimate();
      })
    );
  } else {
    const b = button("Remove", "btn-ghost btn-small", () => {
      climateRemoveId = line.id;
      renderClimate();
    }, climatePeopleBusy);
    b.setAttribute("aria-label", `Remove ${line.name}`);
    box.append(b);
  }
  return box;
}

function renderDevice(doc) {
  const line = deviceLine(climateDevice);
  const showAdd = line.kind === "add" && !climateAdding;
  const showForm = line.kind === "add" && climateAdding;
  const showText = line.kind === "counted" || line.kind === "remote";
  climateEls.device.hidden = line.kind === "none";
  climateEls.deviceText.hidden = !showText;
  climateEls.deviceText.textContent = showText ? line.text : "";
  climateEls.deviceAdd.hidden = !showAdd;
  climateEls.deviceAdd.disabled = climatePeopleBusy;
  climateEls.deviceForm.hidden = !showForm;
  climateEls.deviceGo.disabled = climatePeopleBusy;
  if (line.kind !== "add") {
    climateAdding = false;
    clearFieldError(climateEls.deviceError);
  }
}

// One people write (add or remove): the reply is the full status document.
async function postPeople(path, bodyText) {
  climatePeopleBusy = true;
  renderClimate();
  try {
    const { url, init } = apiRequest(path);
    init.body = bodyText;
    const response = await fetch(url, init);
    const text = await response.text();
    if (!response.ok) {
      const data = safeJsonParse(text);
      throw new Error((data && data.error) || `Request failed (${response.status}).`);
    }
    const doc = parseClimateText(text);
    if (!doc) {
      throw new Error("The list of people did not come back.");
    }
    applyClimate(doc);
    return true;
  } catch (err) {
    showFieldError(climateEls.deviceError, err.message || "Couldn't save. Try again.");
    return false;
  } finally {
    climatePeopleBusy = false;
    renderClimate();
  }
}

async function removePerson(id) {
  clearFieldError(climateEls.deviceError);
  climateEls.device.hidden = false;
  const ok = await postPeople("/api/homebody/people/remove", removeBody(id));
  climateRemoveId = null;
  if (ok) {
    haptics.roomOff();
    await refreshThisDevice();
  } else {
    renderClimate();
  }
}

async function addThisPhone() {
  const message = checkName(climateEls.deviceName.value, state.climate ? state.climate.people : []);
  if (message) {
    showFieldError(climateEls.deviceError, message);
    return;
  }
  clearFieldError(climateEls.deviceError);
  const ok = await postPeople("/api/homebody/people/add", addBody(climateEls.deviceName.value));
  if (ok) {
    climateAdding = false;
    climateEls.deviceName.value = "";
    await refreshThisDevice();
  }
}

// The same switch the room rows use: aria-checked carries the state, the text
// is for screen readers only.
function setClimateSwitch(on) {
  climateEls.enabled.setAttribute("aria-checked", on ? "true" : "false");
  climateEls.enabled.textContent = on ? "On" : "Off";
}

function wireClimate() {
  climateEls.deviceAdd.addEventListener("click", () => {
    climateAdding = true;
    clearFieldError(climateEls.deviceError);
    renderClimate();
    climateEls.deviceName.focus();
  });
  climateEls.deviceCancel.addEventListener("click", () => {
    climateAdding = false;
    climateEls.deviceName.value = "";
    clearFieldError(climateEls.deviceError);
    renderClimate();
    climateEls.deviceAdd.focus();
  });
  climateEls.deviceForm.addEventListener("submit", (event) => {
    event.preventDefault();
    addThisPhone();
  });
  climateEls.enabled.addEventListener("click", () => {
    const on = climateEls.enabled.getAttribute("aria-checked") !== "true";
    setClimateSwitch(on);
    if (on) haptics.roomOn();
    else haptics.roomOff();
    climateQueue.edit({ enabled: on }, { delayMs: 0 });
  });
  for (const [name, input] of Object.entries(climateEls.inputs)) {
    input.addEventListener("input", () => editClimateNumber(name, parseWhole(input.value)));
    input.addEventListener("blur", () => climateQueue.flush());
    const row = input.closest(".cl-stepper");
    for (const button of row.querySelectorAll(".cl-step")) {
      button.addEventListener("click", () => {
        const current = parseWhole(input.value) ?? climateDraft[name];
        const { min, max } = SETTING_LIMITS[name];
        const next = Math.min(max, Math.max(min, current + Number(button.dataset.dir)));
        haptics.step();
        input.value = String(next);
        editClimateNumber(name, next);
      });
    }
  }
}

// One number changed. Refuse a bad value or a comfort at or above away inline,
// without writing; otherwise queue it (saved 600 ms after the last change, or on
// blur).
function editClimateNumber(name, value) {
  const raws = {};
  for (const other of climateRefused.keys()) {
    raws[other] = parseWhole(climateEls.inputs[other].value);
  }
  const out = editSetting(climateDraft, climateRefused, name, value, raws);
  climateRefused = out.refused;
  climateDraft = out.draft;
  for (const [field, input] of Object.entries(climateEls.inputs)) {
    if (climateRefused.has(field)) {
      input.setAttribute("aria-invalid", "true");
    } else {
      input.removeAttribute("aria-invalid");
    }
  }
  const [first] = climateRefused.values();
  if (first) {
    showFieldError(climateEls.error, first);
  } else {
    clearFieldError(climateEls.error);
  }
  for (const [field, accepted] of Object.entries(out.accepted)) {
    if (field !== name) {
      setFieldValue(climateEls.inputs[field], String(accepted));
    }
  }
  if (Object.keys(out.accepted).length > 0) {
    climateQueue.edit(out.accepted);
  }
}

async function writeClimate(obj) {
  const body = settingsBody(climateWritten, obj);
  if (Object.keys(body).length === 0) {
    return;
  }
  const { url, init } = apiRequest("/api/homebody/settings", body);
  const response = await fetch(url, init);
  const text = await response.text();
  if (!response.ok) {
    const data = safeJsonParse(text);
    throw new Error((data && data.error) || `Request failed (${response.status}).`);
  }
  const doc = parseClimateText(text);
  if (!doc) {
    throw new Error("The thermostat settings did not come back.");
  }
  applyClimate(doc);
}
