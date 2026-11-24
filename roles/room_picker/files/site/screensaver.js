// The panel screensaver overlay. Panel mode is switched on by the kiosk URL
// (?panel=1&...), and rooms.js loads this module only then, so phones never fetch
// it, never create the canvas and never start a timer or rAF loop.
//
// While idle past the limit (see screensaver-model.js) a full-screen black layer
// with the Aurora canvas fades in. While it is up it swallows every pointer, touch
// and key event; the first pointerdown starts a quick fade out. Swallowing goes on
// for SWALLOW_MS after the last swallowed event so the tap's own pointerup and
// click never reach a room switch underneath.

import { swapImage as realSwapImage } from "./cover-color.js";
import { ambientWanted, pausedClock } from "./now-model.js";
import { progressAt } from "./rooms-model.js";
import { createStyle as realCreateStyle } from "./style-registry.js";
import { nextPick, shippedPicks } from "./style-list.js";
import { parsePanelParams, paramsFromSettings, shouldShow, idleLimitMs, inQuietHours } from "./screensaver-model.js";

const FADE_OUT_MS = 300;
const SWALLOW_MS = 400;
const RECHECK_MS = 60000;
const AMBIENT_TICK_MS = 1000;
const CROSSFADE_MS = 3000;

const SWALLOWED = [
  "pointerdown", "pointerup", "pointermove", "pointercancel", "click",
  "touchstart", "touchmove", "touchend", "touchcancel",
  "mousedown", "mouseup", "keydown", "keyup",
];
const DISMISS = new Set(["pointerdown", "keydown"]);

export function createScreensaver({
  doc,
  win,
  params: initialParams,
  createStyle = realCreateStyle,
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
  // The style layers sit in the stage, one wrapper each; the ambient layer is above.
  const stage = doc.createElement("div");
  stage.className = "saver-stage";
  node.append(stage);
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

  // The wait, hours and on/off. They start from the kiosk URL and are replaced by
  // the panel's own settings from the hub (setSettings).
  let params = initialParams;
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
  // Style layers: one instance (style plus its wrapper) per id, made when first needed.
  const styleEnv = { doc, win, now, setTimeout, clearTimeout, random: () => Math.random() };
  const instances = new Map();
  const started = new Set();
  const fadeStops = new Map();
  let current = null;
  let lastShownId = null;
  let rotationTimer = null;
  let hues = null;
  let night = false;

  function ambient() {
    return ambientWanted(track, clock, now());
  }

  // Draw the track into the ambient layer (kept current even while down, so the
  // first frame after a show is right).
  function drawAmbient() {
    // Idle while the page is hidden; the visibility handler redraws on return.
    if (!track || doc.hidden) return;
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

  // ---- style layers ------------------------------------------------------ //

  function instance(id) {
    let inst = instances.get(id);
    if (inst) return inst;
    const style = createStyle(id, styleEnv);
    if (!style) return null;
    const wrap = doc.createElement("div");
    wrap.className = "saver-layer";
    wrap.append(style.node);
    stage.append(wrap);
    if (hues) style.setHues(hues);
    style.setNight(night);
    inst = { id, style, wrap };
    instances.set(id, inst);
    return inst;
  }

  // The instance for an id, Aurora when this page has no module for it.
  const instanceOrDefault = (id) => instance(id) || instance("aurora");

  function startInst(inst) {
    const pending = fadeStops.get(inst.id);
    if (pending !== undefined) {
      clearTimeout(pending);
      fadeStops.delete(inst.id);
    }
    if (started.has(inst.id)) return;
    started.add(inst.id);
    inst.style.start();
  }

  function stopInst(inst) {
    const pending = fadeStops.get(inst.id);
    if (pending !== undefined) {
      clearTimeout(pending);
      fadeStops.delete(inst.id);
    }
    if (!started.has(inst.id)) return;
    started.delete(inst.id);
    inst.style.stop();
  }

  // Every animation stops (ambient view, hidden page, fade out). `current` is kept so
  // the same style comes back.
  function haltStyles() {
    for (const inst of instances.values()) {
      stopInst(inst);
      if (inst !== current) inst.wrap.classList.remove("is-in");
    }
  }

  function resetLayers() {
    haltStyles();
    for (const inst of instances.values()) inst.wrap.classList.remove("is-in", "is-snap");
    current = null;
  }

  // Fade to `id` over about 3 s; the old layer keeps running until it has faded.
  function crossfadeTo(id) {
    const inst = instanceOrDefault(id);
    if (!inst || inst === current) return;
    const prev = current;
    current = inst;
    startInst(inst);
    inst.wrap.classList.remove("is-snap");
    inst.wrap.classList.add("is-in");
    if (prev) {
      prev.wrap.classList.remove("is-in");
      fadeStops.set(prev.id, setTimeout(() => {
        fadeStops.delete(prev.id);
        stopInst(prev);
      }, CROSSFADE_MS));
    }
  }

  function clearRotation() {
    if (rotationTimer !== null) clearTimeout(rotationTimer);
    rotationTimer = null;
  }

  // One timer, only while a style is on screen: every change interval the next pick.
  function startRotation() {
    if (rotationTimer !== null || params.changeMin === 0) return;
    if (shippedPicks(params.styles).length < 2) return;
    rotationTimer = setTimeout(rotate, params.changeMin * 60000);
  }

  function rotate() {
    rotationTimer = null;
    if (!up || ambient() || doc.hidden) return;
    lastShownId = nextPick(params.styles, lastShownId);
    crossfadeTo(lastShownId);
    startRotation();
  }

  // The hour passing while it runs: each style switches look (and fades itself).
  function syncNight() {
    const n = inQuietHours(new Date(now()), params);
    if (n === night) return;
    night = n;
    for (const inst of instances.values()) inst.style.setNight(night);
  }

  // Ambient while music plays (or was paused under 5 minutes ago), else the picked
  // style. No style animates while the ambient layer or a hidden page covers it.
  function applyMode() {
    if (!up) return;
    const wantAmbient = ambient();
    if (wantAmbient) drawAmbient();
    amb.hidden = !wantAmbient;
    if (wantAmbient || doc.hidden) {
      haltStyles();
      clearRotation();
      return;
    }
    if (!current) {
      current = instanceOrDefault(lastShownId);
      current.wrap.classList.add("is-in", "is-snap");
    }
    startInst(current);
    startRotation();
  }

  function check() {
    if (checkTimer !== null) clearTimeout(checkTimer);
    checkTimer = null;
    if (up || params.enabled === false) return;
    const date = new Date(now());
    const idleMs = now() - lastInput;
    if (shouldShow({ idleMs, date, params, inputHeld: held })) {
      show();
      return;
    }
    const wait = Math.max(1000, idleLimitMs(date, params) - idleMs);
    checkTimer = setTimeout(check, wait);
  }

  function tick() {
    syncNight();
    applyMode();
  }

  function show() {
    if (stopTimer !== null) {
      clearTimeout(stopTimer);
      stopTimer = null;
    }
    up = true;
    resetLayers();
    night = inQuietHours(new Date(now()), params);
    lastShownId = nextPick(params.styles, lastShownId);
    node.classList.add("is-up");
    applyMode();
    ambientTimer = setInterval(tick, AMBIENT_TICK_MS);
  }

  // `silent` closes the overlay without the dismiss callback: the screensaver was
  // switched off, so nothing should jump to Now playing.
  function dismiss(silent = false) {
    up = false;
    clearRotation();
    if (ambientTimer !== null) {
      clearInterval(ambientTimer);
      ambientTimer = null;
    }
    if (track && !silent) onDismiss(track);
    held = false;
    lastInput = now();
    node.classList.remove("is-up");
    stopTimer = setTimeout(() => {
      stopTimer = null;
      resetLayers();
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
      haltStyles();
      clearRotation();
    } else {
      applyMode();
    }
  }

  // New picks or change time while it is up: leave a style that is no longer picked
  // and restart the timer for the new interval.
  function reconcile() {
    if (!up) return;
    clearRotation();
    if (current && !ambient() && !shippedPicks(params.styles).includes(current.id)) {
      lastShownId = nextPick(params.styles, current.id);
      crossfadeTo(lastShownId);
    }
    applyMode();
  }

  for (const type of SWALLOWED) doc.addEventListener(type, onInput, true);
  doc.addEventListener("visibilitychange", onVisibility);
  const recheck = setInterval(check, RECHECK_MS);
  check();

  return {
    setHues(h) {
      hues = h;
      for (const inst of instances.values()) inst.style.setHues(h);
    },
    // The style the next start (or the next change) shows, without moving the rotation.
    nextStyle: () => nextPick(params.styles, lastShownId),
    // This panel's settings from the hub (panel-settings-model.js). A new wait,
    // hours or style apply at once; switching it off closes an open overlay and
    // switching it on starts a fresh wait.
    setSettings(settings) {
      const next = paramsFromSettings(settings, params);
      if (JSON.stringify(next) === JSON.stringify(params)) return;
      const wasOn = params.enabled !== false;
      params = next;
      if (!params.enabled) {
        if (checkTimer !== null) {
          clearTimeout(checkTimer);
          checkTimer = null;
        }
        if (up) dismiss(true);
        return;
      }
      if (!wasOn) lastInput = now();
      reconcile();
      check();
    },
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
      clearRotation();
      haltStyles();
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
