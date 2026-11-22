// Canvas drawing for the panel screensaver's Aurora style. The math is in
// aurora-math.js. A small offscreen-sized canvas (about 200 px wide) is scaled up
// by CSS, so the browser's smooth upscale gives the soft look. One rAF loop,
// capped at maxFps (30), and no loop at all while stopped.

import { DEFAULT_HUES } from "./cover-color.js";
import { RIBBONS, mixHues, ribbonColumn, ribbonStops, smoothstep, tintColor } from "./aurora-math.js";

const FADE_S = 3;
const COLUMN_STEP = 2;
const COLUMN_WIDTH = 2.5;

export function createAurora({
  canvas,
  getSize,
  raf,
  caf,
  nowMs,
  baseWidth = 200,
  maxFps = 30,
}) {
  const ctx = canvas.getContext("2d", { alpha: false });
  let W = baseWidth;
  let H = 120;
  let vignette = null;
  let t = 0;
  let lastFrame = null;
  let lastDraw = null;
  let rafId = 0;
  let from = { ...DEFAULT_HUES };
  let to = from;
  let fadeStart = null;
  let current = from;

  function resize() {
    const { width, height } = getSize();
    const ar = (height || 480) / (width || 800);
    W = baseWidth;
    H = Math.max(60, Math.min(400, Math.round(W * ar)));
    canvas.width = W;
    canvas.height = H;
    vignette = ctx.createRadialGradient(W / 2, H / 2, H * 0.25, W / 2, H / 2, W * 0.62);
    vignette.addColorStop(0, "rgba(0,0,0,0)");
    vignette.addColorStop(1, "rgba(0,0,0,0.78)");
  }
  resize();

  function updateHues(now) {
    if (fadeStart === null) return;
    const f = smoothstep((now - fadeStart) / 1000 / FADE_S);
    current = mixHues(from, to, f);
    if (f >= 1) {
      from = current;
      fadeStart = null;
    }
  }

  function draw() {
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, W, H);
    ctx.globalCompositeOperation = "lighter";
    const hc = H * 0.45;
    for (const ribbon of RIBBONS) {
      const g = ctx.createLinearGradient(0, 0, 0, hc);
      for (const [pos, color] of ribbonStops(tintColor(current, 0.68, ribbon.k))) {
        g.addColorStop(pos, color);
      }
      ctx.fillStyle = g;
      for (let x = 0; x < W; x += COLUMN_STEP) {
        const col = ribbonColumn(ribbon, x, t, H);
        ctx.globalAlpha = col.alpha;
        ctx.setTransform(1, 0, 0, 1, x, col.y);
        ctx.fillRect(0, 0, COLUMN_WIDTH, hc);
      }
      ctx.setTransform(1, 0, 0, 1, 0, 0);
    }
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";
    ctx.fillStyle = vignette;
    ctx.fillRect(0, 0, W, H);
  }

  function frame(now) {
    if (lastDraw !== null && now - lastDraw < 1000 / maxFps - 2) return false;
    const real = lastFrame === null ? 1000 / 60 : now - lastFrame;
    t += Math.min(real / 1000, 0.1);
    lastFrame = now;
    lastDraw = now;
    updateHues(now);
    draw();
    return true;
  }

  function loop(now) {
    rafId = raf(loop);
    frame(now);
  }

  return {
    start() {
      if (rafId) return;
      lastFrame = null;
      lastDraw = null;
      rafId = raf(loop);
    },
    stop() {
      if (!rafId) return;
      caf(rafId);
      rafId = 0;
    },
    running: () => rafId !== 0,
    setHues(h) {
      const now = nowMs();
      updateHues(now);
      from = current;
      to = { ...DEFAULT_HUES, ...h };
      fadeStart = now;
    },
    frame,
  };
}
