// The panel screensaver overlay. Panel mode is switched on by the kiosk URL
// (?panel=1&...), and rooms.js loads this module only then, so phones never fetch
// it, never create the canvas and never start a timer or rAF loop.
//
// While idle past the limit (see screensaver-model.js) a full-screen black layer
// with the Aurora canvas fades in. While it is up it swallows every pointer, touch
// and key event; the first pointerdown starts a quick fade out. Swallowing goes on
// for SWALLOW_MS after the last swallowed event so the tap's own pointerup and
// click never reach a room switch underneath.

import { createAurora as realCreateAurora } from "./aurora.js";
import { swapImage as realSwapImage } from "./cover-color.js";
import { ambientWanted, pausedClock } from "./now-model.js";
import { progressAt } from "./rooms-model.js";
import { parsePanelParams, shouldShow, idleLimitMs } from "./screensaver-model.js";

const FADE_OUT_MS = 300;
const SWALLOW_MS = 400;
const RECHECK_MS = 60000;
const AMBIENT_TICK_MS = 1000;

const SWALLOWED = [
  "pointerdown", "pointerup", "pointermove", "pointercancel", "click",
  "touchstart", "touchmove", "touchend", "touchcancel",
  "mousedown", "mouseup", "keydown", "keyup",
];
const DISMISS = new Set(["pointerdown", "keydown"]);

export function createScreensaver({
  doc,
  win,
  params,
  createAurora = realCreateAurora,
  swapImage = realSwapImage,
  onDismiss = () => {},
  now,
  setInterval,
  clearInterval,
  setTimeout,
  clearTimeout,
}) {
  const node = doc.createElement("div");
  node.className = "screensaver";
  node.setAttribute("aria-hidden", "true");
  const canvas = doc.createElement("canvas");
  node.append(canvas);
  // The ambient Now Playing layer (cover, title, artist, a thin progress line).
  // The page's cover hues (--h, --k on the root) tint its background.
  const amb = doc.createElement("div");
  amb.className = "p-amb";
  amb.hidden = true;
  const ambArt = doc.createElement("img");
  ambArt.className = "p-amb-art";
  ambArt.alt = "";
  const ambText = doc.createElement("div");
  ambText.className = "p-amb-text";
  const ambTitle = doc.createElement("div");
  ambTitle.className = "p-amb-title";
  const ambArtist = doc.createElement("div");
  ambArtist.className = "p-amb-artist";
  const ambLine = doc.createElement("div");
  ambLine.className = "p-amb-line";
  const ambFill = doc.createElement("div");
  ambFill.className = "p-amb-fill";
  ambLine.append(ambFill);
  ambText.append(ambTitle, ambArtist, ambLine);
  amb.append(ambArt, ambText);
  node.append(amb);
  doc.body.append(node);

  const aurora = createAurora({
    canvas,
    getSize: () => ({ width: win.innerWidth, height: win.innerHeight }),
    raf: (fn) => win.requestAnimationFrame(fn),
    caf: (id) => win.cancelAnimationFrame(id),
    nowMs: () => win.performance.now(),
  });

  let up = false;
  let held = false;
  let lastInput = now();
  let swallowUntil = 0;
  let checkTimer = null;
  let stopTimer = null;
  let track = null;
  let clock = { key: null, since: null };
  let ambientTimer = null;
  let artSrc = null;
  let shown = null;
  let auroraWaiting = false;

  function ambient() {
    return ambientWanted(track, clock, now());
  }

  // Draw the track into the ambient layer (kept current even while down, so the
  // first frame after a show is right).
  function drawAmbient() {
    if (!track) return;
    ambTitle.textContent = track.title;
    ambArtist.textContent = track.artist || "";
    const src = track.artUrl || null;
    ambArt.hidden = !src;
    if (src !== artSrc) {
      artSrc = src;
      if (src) swapImage(ambArt, src);
    }
    const p = progressAt(track, now());
    ambLine.hidden = !p;
    ambFill.style.transform = `scaleX(${(p ? p.fraction : 0).toFixed(4)})`;
  }

  // Ambient while music plays (or was paused under 5 minutes ago), else Aurora.
  // The Aurora loop never runs while the ambient layer is showing.
  function applyMode() {
    if (!up) return;
    const wantAmbient = ambient();
    if (wantAmbient) drawAmbient();
    const mode = wantAmbient ? "ambient" : "aurora";
    if (mode === shown && !(mode === "aurora" && auroraWaiting && !doc.hidden)) return;
    shown = mode;
    amb.hidden = !wantAmbient;
    if (wantAmbient) {
      aurora.stop();
      auroraWaiting = false;
    } else if (doc.hidden) {
      auroraWaiting = true;
    } else {
      aurora.start();
      auroraWaiting = false;
    }
  }

  function check() {
    if (checkTimer !== null) clearTimeout(checkTimer);
    checkTimer = null;
    if (up) return;
    const date = new Date(now());
    const idleMs = now() - lastInput;
    if (shouldShow({ idleMs, date, params, inputHeld: held })) {
      show();
      return;
    }
    const wait = Math.max(1000, idleLimitMs(date, params) - idleMs);
    checkTimer = setTimeout(check, wait);
  }

  function show() {
    if (stopTimer !== null) {
      clearTimeout(stopTimer);
      stopTimer = null;
    }
    up = true;
    shown = null;
    node.classList.add("is-up");
    applyMode();
    ambientTimer = setInterval(applyMode, AMBIENT_TICK_MS);
  }

  function dismiss() {
    up = false;
    shown = null;
    if (ambientTimer !== null) {
      clearInterval(ambientTimer);
      ambientTimer = null;
    }
    if (track) onDismiss(track);
    held = false;
    lastInput = now();
    node.classList.remove("is-up");
    stopTimer = setTimeout(() => {
      stopTimer = null;
      aurora.stop();
    }, FADE_OUT_MS);
    check();
  }

  function onInput(event) {
    const t = now();
    if (up || t < swallowUntil) {
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
      swallowUntil = t + SWALLOW_MS;
      if (up && DISMISS.has(event.type)) dismiss();
      return;
    }
    lastInput = t;
    if (event.type === "pointerdown") held = true;
    else if (event.type === "pointerup" || event.type === "pointercancel") held = false;
  }

  function onVisibility() {
    if (!up) return;
    if (doc.hidden) {
      aurora.stop();
      auroraWaiting = shown === "aurora";
    } else {
      applyMode();
    }
  }

  for (const type of SWALLOWED) doc.addEventListener(type, onInput, true);
  doc.addEventListener("visibilitychange", onVisibility);
  const recheck = setInterval(check, RECHECK_MS);
  check();

  return {
    setHues: (h) => aurora.setHues(h),
    // The page's current track (a parseNowPlayingDoc result) or null. The paused
    // moment is this device's own clock, never a hub timestamp.
    setTrack(next) {
      track = next || null;
      clock = pausedClock(clock, track, now());
      drawAmbient();
      applyMode();
    },
    isUp: () => up,
    destroy() {
      for (const type of SWALLOWED) doc.removeEventListener(type, onInput, true);
      doc.removeEventListener("visibilitychange", onVisibility);
      clearInterval(recheck);
      if (ambientTimer !== null) clearInterval(ambientTimer);
      if (checkTimer !== null) clearTimeout(checkTimer);
      if (stopTimer !== null) clearTimeout(stopTimer);
      aurora.stop();
      node.remove();
    },
  };
}

export function initScreensaver(search, env = {}) {
  const params = parsePanelParams(search);
  if (!params) return null;
  const doc = env.doc || document;
  const win = env.win || window;
  return createScreensaver({
    doc,
    win,
    params,
    onDismiss: env.onDismiss,
    now: () => Date.now(),
    setInterval: (f, ms) => win.setInterval(f, ms),
    clearInterval: (id) => win.clearInterval(id),
    setTimeout: (f, ms) => win.setTimeout(f, ms),
    clearTimeout: (id) => win.clearTimeout(id),
  });
}
