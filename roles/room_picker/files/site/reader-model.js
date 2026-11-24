// Pure rules shared by the Weird facts and Quotes screensaver styles: the shuffled
// deck (no repeat until the list is used up, no neighbour with the same key), how
// long an entry is held, how large its text is, and where it goes next. No DOM.

import { pickSpot } from "./clock-model.js";

export const FADE_MS = 1600;

const FACT_BASE_MS = 60000;
const FACT_FREE_CHARS = 100;
const FACT_MS_PER_CHAR = 500;
const HOLD_MAX_MS = 90000;
const QUOTE_MIN_MS = 45000;
const QUOTE_FULL_CHARS = 220;

// A shuffled deck over `entries`. next() returns each entry once per round; a new
// round is reshuffled and never starts with the entry that ended the last one. With
// `keyOf`, an entry never follows another with the same key (two quotes by one
// author), unless nothing else is left to show. `random` is injected.
export function createDeck(entries, random, keyOf = (e) => e) {
  const list = Array.isArray(entries) ? [...entries] : [];
  let queue = [];
  let lastKey;
  let hasLast = false;

  function build() {
    const pool = [...list];
    const counts = new Map();
    for (const e of pool) counts.set(keyOf(e), (counts.get(keyOf(e)) || 0) + 1);
    const out = [];
    let prev = lastKey;
    let havePrev = hasLast;
    while (pool.length > 0) {
      const r = pool.length;
      let top = null;
      for (const [k, c] of counts) if (top === null || c > counts.get(top)) top = k;
      // A key holding more than half of what is left must be placed now, or it
      // would be forced next to itself later.
      const forced = top !== null && counts.get(top) * 2 >= r + 1 && !(havePrev && top === prev);
      let candidates = pool.filter((e) => (forced ? keyOf(e) === top : !havePrev || keyOf(e) !== prev));
      if (candidates.length === 0) candidates = pool;
      const pick = candidates[Math.min(candidates.length - 1, Math.floor(random() * candidates.length))];
      pool.splice(pool.indexOf(pick), 1);
      counts.set(keyOf(pick), counts.get(keyOf(pick)) - 1);
      out.push(pick);
      prev = keyOf(pick);
      havePrev = true;
    }
    return out;
  }

  return {
    next() {
      if (list.length === 0) return null;
      if (queue.length === 0) queue = build();
      const entry = queue.shift();
      lastKey = keyOf(entry);
      hasLast = true;
      return entry;
    },
  };
}

// Milliseconds to show an entry. A fact is held about 60 s, longer when long (up to
// 90 s); a quote 45 s for a short one up to 90 s at 220 characters.
export function holdMs(kind, text) {
  const n = String(text || "").length;
  if (kind === "quotes") {
    const frac = Math.min(1, n / QUOTE_FULL_CHARS);
    return Math.round(QUOTE_MIN_MS + frac * (HOLD_MAX_MS - QUOTE_MIN_MS));
  }
  const over = Math.max(0, n - FACT_FREE_CHARS);
  return Math.min(HOLD_MAX_MS, FACT_BASE_MS + over * FACT_MS_PER_CHAR);
}

// Font size in px for `length` characters: large when short, never under 28 px, and
// small enough that the longest allowed entry fits 800 by 480 in a 680 px box.
export function textSize(kind, length) {
  if (length <= 60) return 44;
  if (length <= 110) return 38;
  if (kind === "quotes" && length > 160) return 28;
  return 32;
}

// Where the next entry goes, as fractions (0 to 1) of the free area, away from the
// last place. The page turns a fraction into a position that keeps the box on screen.
export const pickPlace = pickSpot;

// The usable entries of a loaded file: objects with a non-empty `text`.
export function parseEntries(doc) {
  if (!Array.isArray(doc)) return [];
  return doc.filter((e) => e && typeof e === "object" && typeof e.text === "string" && e.text.trim() !== "");
}
