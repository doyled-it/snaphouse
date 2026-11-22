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
import { parsePanelParams, shouldShow, idleLimitMs } from "./screensaver-model.js";

const FADE_OUT_MS = 300;
const SWALLOW_MS = 400;
const RECHECK_MS = 60000;

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
    node.classList.add("is-up");
    if (!doc.hidden) aurora.start();
  }

  function dismiss() {
    up = false;
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
    if (doc.hidden) aurora.stop();
    else aurora.start();
  }

  for (const type of SWALLOWED) doc.addEventListener(type, onInput, true);
  doc.addEventListener("visibilitychange", onVisibility);
  const recheck = setInterval(check, RECHECK_MS);
  check();

  return {
    setHues: (h) => aurora.setHues(h),
    isUp: () => up,
    destroy() {
      for (const type of SWALLOWED) doc.removeEventListener(type, onInput, true);
      doc.removeEventListener("visibilitychange", onVisibility);
      clearInterval(recheck);
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
    now: () => Date.now(),
    setInterval: (f, ms) => win.setInterval(f, ms),
    clearInterval: (id) => win.clearInterval(id),
    setTimeout: (f, ms) => win.setTimeout(f, ms),
    clearTimeout: (id) => win.clearTimeout(id),
  });
}
