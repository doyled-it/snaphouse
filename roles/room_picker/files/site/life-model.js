// Pure rules for the Game of Life style: Conway's rules on an 80 by 48 torus, a ghost
// that fades where a cell died, and the stall rules that reseed a patch when the
// pattern stops changing (still, a short repeating cycle, a flat population) or the
// population drops low. Random is injected.

import { lerp } from "./gen-color.js";

export const LIFE = Object.freeze({
  cols: 80,
  rows: 48,
  width: 320,
  height: 192,
  cell: 4,
  dayStepS: 0.5,
  nightStepS: 1.5,
  lowPop: 60,
  density: 0.3,
  patchW: 20,
  patchH: 18,
  cycleLen: 12, // states remembered to spot a repeating cycle
  flatLen: 40, // steps of population history for the flat check
  flatSpread: 6,
  ghostRate: 1.2, // ghost decays by exp(-rate * seconds)
  maxFps: 10,
});

export const stepIntervalS = (nightMix) => lerp(LIFE.dayStepS, LIFE.nightStepS, nightMix);

export function createLife({ cols = LIFE.cols, rows = LIFE.rows, random, lowPop = LIFE.lowPop }) {
  const size = cols * rows;
  let cells = new Uint8Array(size);
  let next = new Uint8Array(size);
  const age = new Uint16Array(size);
  const ghost = new Float32Array(size);
  let hashes = [];
  let pops = [];

  const life = {
    cols,
    rows,
    get cells() {
      return cells;
    },
    age,
    ghost,

    seedAll() {
      for (let i = 0; i < size; i += 1) if (random() < LIFE.density) cells[i] = 1;
    },

    // Add a patch of random cells in a random spot (over whatever is there).
    seedPatch() {
      const x0 = Math.floor(random() * (cols - LIFE.patchW));
      const y0 = Math.floor(random() * (rows - LIFE.patchH));
      for (let y = y0; y < y0 + LIFE.patchH; y += 1) {
        for (let x = x0; x < x0 + LIFE.patchW; x += 1) {
          if (random() < LIFE.density) cells[y * cols + x] = 1;
        }
      }
    },

    setCells(list) {
      cells.fill(0);
      age.fill(0);
      ghost.fill(0);
      for (const [x, y] of list) cells[y * cols + x] = 1;
      hashes = [];
      pops = [];
    },

    // One generation. Returns { population, reseed } where reseed is null or why a
    // patch was added: "still", "cycle", "flat" or "low".
    step() {
      let pop = 0;
      for (let y = 0; y < rows; y += 1) {
        const up = ((y + rows - 1) % rows) * cols;
        const mid = y * cols;
        const down = ((y + 1) % rows) * cols;
        for (let x = 0; x < cols; x += 1) {
          const l = (x + cols - 1) % cols;
          const r = (x + 1) % cols;
          const n =
            cells[up + l] + cells[up + x] + cells[up + r] +
            cells[mid + l] + cells[mid + r] +
            cells[down + l] + cells[down + x] + cells[down + r];
          const i = mid + x;
          const was = cells[i];
          const now = was ? n === 2 || n === 3 : n === 3;
          if (now) {
            next[i] = 1;
            age[i] = was ? age[i] + 1 : 0;
            pop += 1;
          } else {
            next[i] = 0;
            if (was) ghost[i] = 1;
            age[i] = 0;
          }
        }
      }
      const swap = cells;
      cells = next;
      next = swap;

      const reseed = stallReason(this, pop);
      if (reseed) {
        life.seedPatch();
        hashes = [];
        pops = [];
      }
      return { population: pop, reseed };
    },

    decayGhost(dt) {
      const k = Math.exp(-LIFE.ghostRate * dt);
      for (let i = 0; i < size; i += 1) {
        const g = ghost[i] * k;
        ghost[i] = g < 0.02 ? 0 : g;
      }
    },
  };

  function stateHash() {
    let h = 2166136261;
    for (let i = 0; i < size; i += 1) h = Math.imul(h ^ cells[i], 16777619);
    return h >>> 0;
  }

  function stallReason(_, pop) {
    const h = stateHash();
    let reason = null;
    if (hashes.length > 0 && hashes[hashes.length - 1] === h) reason = "still";
    else if (hashes.includes(h)) reason = "cycle";
    hashes.push(h);
    if (hashes.length > LIFE.cycleLen) hashes.shift();
    pops.push(pop);
    if (pops.length > LIFE.flatLen) pops.shift();
    if (!reason && pops.length === LIFE.flatLen && Math.max(...pops) - Math.min(...pops) < LIFE.flatSpread) {
      reason = "flat";
    }
    if (!reason && pop < lowPop) reason = "low";
    return reason;
  }

  life.seedAll();
  return life;
}
