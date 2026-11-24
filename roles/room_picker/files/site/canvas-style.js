// The shared shell of the generative canvas styles (Flow field, Contours, Game of
// Life, Harmonograph). Each draws to a small canvas that CSS scales up, like Aurora.
// This module owns what they have in common: one rAF loop capped at the style's fps
// (and no loop at all while stopped), the album hue crossfade, and the night mix (0
// day, 1 night) that eases over a few seconds so an hour passing never pops. A style
// supplies { width, height, fps(night), make({ ctx, width, height, random }) } and
// make returns a painter { frame(dt, { hues, hueVersion, night }) }. The 2d context
// and the painter are made on the first start.

import { DEFAULT_HUES } from "./cover-color.js";
import { mixHues, smoothstep } from "./aurora-math.js";

const HUE_FADE_S = 3;
const NIGHT_FADE_S = 3;
const MAX_DT_S = 0.25; // a hitch or a long gap never jumps the animation

export function createCanvasStyle({ doc, win, random }, { width, height, fps, make }) {
  const canvas = doc.createElement("canvas");
  canvas.width = width;
  canvas.height = height;

  let painter = null;
  let rafId = 0;
  let lastFrame = null;
  let lastDraw = null;
  let from = { ...DEFAULT_HUES };
  let to = from;
  let current = from;
  let fadeStart = null;
  let version = 0;
  let nightTarget = 0;
  let nightMix = 0;
  let started = false;

  function updateHues(now) {
    if (fadeStart === null) return;
    const f = smoothstep((now - fadeStart) / 1000 / HUE_FADE_S);
    current = mixHues(from, to, f);
    version += 1;
    if (f >= 1) {
      from = current;
      fadeStart = null;
    }
  }

  function frame(now) {
    if (lastDraw !== null && now - lastDraw < 1000 / fps(nightMix) - 2) return false;
    const dt = lastFrame === null ? 0 : Math.min((now - lastFrame) / 1000, MAX_DT_S);
    lastFrame = now;
    lastDraw = now;
    if (nightMix !== nightTarget) {
      const step = dt / NIGHT_FADE_S;
      nightMix = nightTarget > nightMix ? Math.min(nightTarget, nightMix + step) : Math.max(nightTarget, nightMix - step);
    }
    updateHues(now);
    painter.frame(dt, { hues: current, hueVersion: version, night: nightMix });
    return true;
  }

  function loop(now) {
    rafId = win.requestAnimationFrame(loop);
    frame(now);
  }

  return {
    node: canvas,
    start() {
      if (rafId) return;
      if (!painter) painter = make({ ctx: canvas.getContext("2d"), width, height, random });
      started = true;
      lastFrame = null;
      lastDraw = null;
      rafId = win.requestAnimationFrame(loop);
    },
    stop() {
      if (!rafId) return;
      win.cancelAnimationFrame(rafId);
      rafId = 0;
    },
    setHues(h) {
      const now = win.performance.now();
      updateHues(now);
      from = current;
      to = { ...DEFAULT_HUES, ...h };
      fadeStart = now;
    },
    setNight(on) {
      nightTarget = on ? 1 : 0;
      if (!started) nightMix = nightTarget;
    },
    running: () => rafId !== 0,
    frame,
  };
}
