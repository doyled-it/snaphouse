// The Album cover mosaic: the hub's collection of covers (cover-archive.json) as a wall
// of square tiles that scrolls slowly up, with one tile crossfading to another cover
// every 20 to 40 seconds. The rules (layout, no cover twice, recycling, timing) are in
// covers-model.js; this module fetches, decodes and draws.
//
// Cost: one 2d canvas at the panel size, redrawn from a timer (not a rAF loop): four
// times a second by day and twice at night (the wall moves a pixel a second), up to
// 60 times a second only while a crossfade runs. Images are decoded off the hot path
// (img.decode) and only the ones on the grid are kept. Nothing runs while stopped.
//
// With too few covers for three columns the style shows Aurora itself (the fallback
// factory), and swaps to the mosaic on a refresh that brings enough.

import { createAuroraStyle } from "./aurora-style.js";
import {
  FADE_MS, GAP, H, W, advanceScroll, chooseLayout, cropRect, createGrid, parseArchive, speedPxS, swapDelayMs,
} from "./covers-model.js";

const FILE = "cover-archive.json";
const REFRESH_MS = 10 * 60 * 1000;
const RETRY_MS = 30000;
const TICK_DAY_MS = 250;
const TICK_NIGHT_MS = 500;
const TICK_FADE_MS = 16; // about 60 fps while a crossfade runs
const MAX_DT_S = 5;
const RADIUS = 6;

function defaultLoadImage(win) {
  return (url) => {
    const img = new win.Image();
    img.decoding = "async";
    img.src = url;
    return img.decode().then(() => img);
  };
}

export function createCoversStyle(env) {
  const { doc, win, now, setTimeout, clearTimeout, random } = env;
  const get = env.fetch || ((url) => win.fetch(url, { cache: "no-store" }));
  const loadImage = env.loadImage || defaultLoadImage(win);
  const fallback = (env.createFallback || createAuroraStyle)(env);

  const node = doc.createElement("div");
  node.className = "saver-covers";
  const canvas = doc.createElement("canvas");
  canvas.width = W;
  canvas.height = H;
  const shade = doc.createElement("div");
  shade.className = "saver-covers-shade";
  node.append(canvas, shade);
  let fallbackMounted = false;

  let running = false;
  let night = false;
  let gen = 0; // bumps on every stop, so a late fetch or image is ignored
  let mode = "none"; // none | aurora | mosaic
  let pool = null;
  let grid = null;
  let ctx = null;
  let y = 0;
  let scrolled = 0;
  let recycled = 0;
  let lastTick = 0;
  let fade = null; // { r, c, from, to, t0 }
  let tickTimer = null;
  let swapTimer = null;
  let refreshTimer = null;
  const cache = new Map();
  const inflight = new Set();

  const clear = (t) => { if (t !== null) clearTimeout(t); return null; };

  // ---- Aurora fallback -------------------------------------------------- //

  function showAurora() {
    if (mode === "aurora") return;
    leaveMosaic();
    if (!fallbackMounted) {
      node.append(fallback.node);
      fallbackMounted = true;
    }
    mode = "aurora";
    node.classList.add("is-aurora");
    fallback.setNight(night);
    fallback.start();
  }

  function leaveAurora() {
    fallback.stop();
    node.classList.remove("is-aurora");
    // Aurora's canvas is opaque and last in the tree, so a stopped one would paint over the mosaic.
    if (fallbackMounted) {
      fallback.node.remove();
      fallbackMounted = false;
    }
  }

  // ---- loading ----------------------------------------------------------- //

  const fadingPair = () => (fade ? new Set([fade.from, fade.to]) : new Set());

  function onGrid(url) {
    if (!grid) return false;
    if (fade && (fade.from === url || fade.to === url)) return true;
    return grid.urls().has(url);
  }

  function want(url) {
    if (!url || cache.has(url) || inflight.has(url)) return;
    inflight.add(url);
    const mine = gen;
    Promise.resolve()
      .then(() => loadImage(url))
      .then((img) => {
        inflight.delete(url);
        if (mine !== gen || !running || !grid) return;
        if (onGrid(url)) {
          cache.set(url, img);
          if (!fade) draw();
        }
      })
      .catch(() => {
        inflight.delete(url);
        if (mine !== gen || !running || !grid) return;
        for (const cell of grid.drop(url, fadingPair())) want(cell.url);
        draw();
      });
  }

  function wantGrid() {
    for (const row of grid.rows) for (const url of row) want(url);
  }

  function evict() {
    for (const url of [...cache.keys()]) if (!onGrid(url)) cache.delete(url);
  }

  // ---- drawing ----------------------------------------------------------- //

  function tilePath(x, top, s) {
    ctx.beginPath();
    ctx.moveTo(x + RADIUS, top);
    ctx.arcTo(x + s, top, x + s, top + s, RADIUS);
    ctx.arcTo(x + s, top + s, x, top + s, RADIUS);
    ctx.arcTo(x, top + s, x, top, RADIUS);
    ctx.arcTo(x, top, x + s, top, RADIUS);
    ctx.closePath();
  }

  function drawCover(img, x, top, s, alpha) {
    const { sx, sy, s: side } = cropRect(img.naturalWidth || s, img.naturalHeight || s);
    ctx.save();
    tilePath(x, top, s);
    ctx.clip();
    ctx.globalAlpha = alpha;
    ctx.drawImage(img, sx, sy, side, side, x, top, s, s);
    ctx.restore();
  }

  function draw() {
    if (!ctx || !grid) return;
    const { layout } = grid;
    const f = fade ? Math.min(1, (now() - fade.t0) / FADE_MS) : 1;
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, W, H);
    for (let r = 0; r < grid.rows.length; r++) {
      const top = r * layout.pitch - y;
      if (top >= H || top + layout.tile <= 0) continue;
      for (let c = 0; c < layout.cols; c++) {
        const x = layout.left + c * layout.pitch;
        const url = grid.rows[r][c];
        const isFading = fade && fade.r === r && fade.c === c;
        const old = isFading ? cache.get(fade.from) : null;
        const img = url ? cache.get(url) : null;
        if (!img && !old) {
          ctx.fillStyle = "#111";
          tilePath(x, top, layout.tile);
          ctx.fill();
          continue;
        }
        if (old) drawCover(old, x, top, layout.tile, 1);
        if (img) drawCover(img, x, top, layout.tile, isFading && old ? f : 1);
      }
    }
  }

  // ---- the loop ----------------------------------------------------------- //

  function tick() {
    tickTimer = null;
    if (!running || mode !== "mosaic") return;
    const t = now();
    const dt = Math.min((t - lastTick) / 1000, MAX_DT_S);
    lastTick = t;
    const step = advanceScroll(y, dt, speedPxS(night), grid.layout.pitch);
    scrolled += dt * speedPxS(night);
    y = step.y;
    if (step.recycles > 0) {
      for (let i = 0; i < step.recycles; i++) {
        grid.recycle(fade ? new Set([fade.from, fade.to]) : new Set());
        recycled += 1;
        if (fade) fade.r = Math.max(0, fade.r - 1);
      }
      evict();
      wantGrid();
    }
    if (fade && t - fade.t0 >= FADE_MS) {
      fade = null;
      evict();
    }
    draw();
    schedule();
  }

  function schedule() {
    if (tickTimer !== null || !running || mode !== "mosaic") return;
    tickTimer = setTimeout(tick, fade ? TICK_FADE_MS : night ? TICK_NIGHT_MS : TICK_DAY_MS);
  }

  function scheduleSwap() {
    swapTimer = clear(swapTimer);
    if (!running || mode !== "mosaic") return;
    swapTimer = setTimeout(swap, swapDelayMs(random));
  }

  function swap() {
    swapTimer = null;
    if (!running || mode !== "mosaic") return;
    const pick = fade ? null : grid.pickSwap(y, new Set());
    if (!pick) return scheduleSwap();
    const mine = gen;
    const grabbed = pick.url;
    Promise.resolve()
      .then(() => loadImage(grabbed))
      .then((img) => {
        if (mine !== gen || !running || mode !== "mosaic") return;
        const from = grid.rows[pick.r][pick.c];
        if (!grid.commit(pick.r, pick.c, grabbed)) return;
        cache.set(grabbed, img);
        fade = { r: pick.r, c: pick.c, from, to: grabbed, t0: now() };
        schedule();
      })
      .catch(() => {
        if (mine === gen && grid) grid.drop(grabbed, fadingPair());
      })
      .finally(() => {
        if (mine === gen) scheduleSwap();
      });
  }

  function enterMosaic(layout) {
    leaveAurora();
    if (!ctx) ctx = canvas.getContext("2d");
    grid = createGrid(layout, pool, random);
    cache.clear();
    inflight.clear();
    fade = null;
    y = 0;
    mode = "mosaic";
    lastTick = now();
    node.classList.remove("is-aurora");
    wantGrid();
    draw();
    schedule();
    scheduleSwap();
  }

  function leaveMosaic() {
    tickTimer = clear(tickTimer);
    swapTimer = clear(swapTimer);
    grid = null;
    fade = null;
    cache.clear();
    inflight.clear();
  }

  // ---- the collection ------------------------------------------------------ //

  function apply() {
    if (!running) return;
    const layout = chooseLayout(pool.length);
    if (mode === "mosaic" && layout) {
      grid.setPool(pool);
      return;
    }
    if (layout) enterMosaic(layout);
    else showAurora();
  }

  function load() {
    const mine = gen;
    Promise.resolve()
      .then(() => get(FILE))
      .then((res) => {
        if (!res || !res.ok) throw new Error("no collection");
        return res.json();
      })
      .then((body) => {
        if (mine !== gen || !running) return;
        pool = parseArchive(body);
        apply();
        refreshTimer = setTimeout(() => { refreshTimer = null; load(); }, REFRESH_MS);
      })
      .catch(() => {
        if (mine !== gen || !running) return;
        if (!pool) showAurora();
        refreshTimer = setTimeout(() => { refreshTimer = null; load(); }, pool ? REFRESH_MS : RETRY_MS);
      });
  }

  return {
    node,
    start() {
      if (running) return;
      running = true;
      gen += 1;
      pool = null;
      load();
    },
    stop() {
      if (!running) return;
      running = false;
      gen += 1;
      refreshTimer = clear(refreshTimer);
      leaveMosaic();
      fallback.stop();
      mode = "none";
      y = 0;
    },
    setHues(h) {
      fallback.setHues(h);
    },
    setNight(on) {
      night = Boolean(on);
      node.classList.toggle("is-night", night);
      fallback.setNight(night);
    },
    debug: () => ({
      mode,
      cols: grid ? grid.layout.cols : 0,
      rowCount: grid ? grid.layout.rows : 0,
      rows: grid ? grid.rows.map((r) => [...r]) : [],
      pool: grid ? grid.pool.length : pool ? pool.length : 0,
      scrolled,
      recycled,
      cached: cache.size,
      gap: GAP,
      fade: fade ? { from: fade.from, to: fade.to } : null,
    }),
  };
}
