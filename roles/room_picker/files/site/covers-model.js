// The rules of the Album cover mosaic, all pure (no DOM, no clock): which layout a
// collection can fill, the grid of covers and the one rule that matters most (no cover
// appears twice on screen), scrolling and recycling rows, the swap schedule. The style
// (covers-style.js) only fetches, decodes and draws. The collection is the hub's
// cover-archive.json.

export const W = 800;
export const H = 480;
export const GAP = 8;
export const MAX_COLS = 6;
export const MIN_COLS = 3;
// A swap needs a cover that is not on the grid, so a layout asks for a couple spare.
export const SPARE = 2;
export const FADE_MS = 2500;
const DAY_PX_S = 1;
const NIGHT_PX_S = 0.5;

// The grid for `cols` columns: square tiles, a small gap, rows covering the screen plus
// one partly visible row and one buffer row below it (so a recycled row is made off screen).
export function gridLayout(cols, width = W, height = H) {
  const tile = (width - GAP * (cols + 1)) / cols;
  const pitch = tile + GAP;
  return { cols, tile, pitch, rows: Math.ceil(height / pitch) + 2, left: GAP, width, height };
}

// The most columns (6 down to 3) whose whole grid plus the spare the collection can fill,
// or null when even three columns cannot (the style then shows Aurora).
export function chooseLayout(count, width = W, height = H) {
  for (let cols = MAX_COLS; cols >= MIN_COLS; cols--) {
    const layout = gridLayout(cols, width, height);
    if (count >= layout.rows * layout.cols + SPARE) return layout;
  }
  return null;
}

export const MIN_COVERS = (() => { const l = gridLayout(MIN_COLS); return l.rows * l.cols + SPARE; })();

// The cover paths in cover-archive.json: same-origin /cover/ paths only, each once.
export function parseArchive(doc) {
  const out = [];
  const seen = new Set();
  const list = doc && Array.isArray(doc.covers) ? doc.covers : [];
  for (const item of list) {
    const url = item && item.url;
    if (typeof url !== "string" || !url.startsWith("/cover/") || url.startsWith("//") || url.includes("..")) continue;
    if (seen.has(url)) continue;
    seen.add(url);
    out.push(url);
  }
  return out;
}

// Up to `n` distinct covers from `pool` that are not in `taken`, in random order.
export function pickCovers(pool, taken, n, random) {
  const free = pool.filter((u) => !taken.has(u));
  for (let i = free.length - 1; i > 0; i--) {
    const j = Math.floor(random() * (i + 1));
    [free[i], free[j]] = [free[j], free[i]];
  }
  return free.slice(0, n);
}

export function speedPxS(night) {
  return night ? NIGHT_PX_S : DAY_PX_S;
}

export function swapDelayMs(random) {
  return 20000 + Math.floor(random() * 20000);
}

// The scroll offset after dt seconds, and how many whole rows have left the top.
export function advanceScroll(y, dtS, pxPerS, pitch) {
  if (!(dtS > 0)) return { y, recycles: 0 };
  const total = y + dtS * pxPerS;
  const recycles = Math.floor(total / pitch);
  return { y: total - recycles * pitch, recycles };
}

// The centred square of an image: where to read from when drawing it into a square tile.
export function cropRect(w, h) {
  const s = Math.min(w, h);
  return { sx: Math.floor((w - s) / 2), sy: Math.floor((h - s) / 2), s: Math.max(0, s) };
}

export function createGrid(layout, pool0, random) {
  const grid = { layout, rows: [], pool: [...new Set(pool0)] };
  const onGrid = () => new Set(grid.rows.flat().filter(Boolean));
  const fill = (row) => { while (row.length < layout.cols) row.push(null); return row; };

  const first = pickCovers(grid.pool, new Set(), layout.rows * layout.cols, random);
  for (let r = 0; r < layout.rows; r++) grid.rows.push(fill(first.slice(r * layout.cols, (r + 1) * layout.cols)));

  grid.urls = onGrid;

  // The top row has scrolled away: a new bottom row of covers not on the grid.
  grid.recycle = (extraTaken = new Set()) => {
    grid.rows.shift();
    const taken = new Set([...onGrid(), ...extraTaken]);
    const row = fill(pickCovers(grid.pool, taken, layout.cols, random));
    grid.rows.push(row);
    return row;
  };

  // A random on-screen cell and a cover that is neither on the grid nor fading out.
  grid.pickSwap = (y, extraTaken = new Set()) => {
    const cells = [];
    for (let r = 0; r < grid.rows.length; r++) {
      const top = r * layout.pitch - y;
      if (top >= layout.height || top + layout.tile <= 0) continue;
      for (let c = 0; c < layout.cols; c++) cells.push({ r, c });
    }
    if (cells.length === 0) return null;
    const [url] = pickCovers(grid.pool, new Set([...onGrid(), ...extraTaken]), 1, random);
    if (!url) return null;
    const cell = cells[Math.floor(random() * cells.length)];
    return { r: cell.r, c: cell.c, url };
  };

  grid.commit = (r, c, url) => {
    if (!grid.rows[r] || onGrid().has(url)) return false;
    grid.rows[r][c] = url;
    return true;
  };

  // A cover that failed to load leaves the pool and is replaced where it sits.
  grid.drop = (url, extraTaken = new Set()) => {
    grid.pool = grid.pool.filter((u) => u !== url);
    const changed = [];
    for (let r = 0; r < grid.rows.length; r++) {
      for (let c = 0; c < grid.rows[r].length; c++) {
        if (grid.rows[r][c] !== url) continue;
        grid.rows[r][c] = null;
        const [next] = pickCovers(grid.pool, new Set([...onGrid(), ...extraTaken]), 1, random);
        grid.rows[r][c] = next || null;
        changed.push({ r, c, url: next || null });
      }
    }
    return changed;
  };

  // A fresh collection: picks from it from now on; what is on screen stays.
  grid.setPool = (list) => { grid.pool = [...new Set(list)]; };

  return grid;
}
