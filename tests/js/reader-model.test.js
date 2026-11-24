import { test } from "node:test";
import assert from "node:assert/strict";
import {
  FADE_MS, createDeck, holdMs, parseEntries, textSize, pickPlace,
} from "../../roles/room_picker/files/site/reader-model.js";

// A seeded generator so shuffles are reproducible.
function lcg(seed) {
  let s = seed;
  return () => {
    s = (s * 1664525 + 1013904223) % 4294967296;
    return s / 4294967296;
  };
}

const items = (n) => Array.from({ length: n }, (_, i) => ({ text: `t${i}`, author: `a${i % 3}` }));

test("the deck shows every entry once per round, shuffled", () => {
  const deck = createDeck(items(10), lcg(1));
  const seen = Array.from({ length: 10 }, () => deck.next().text);
  assert.equal(new Set(seen).size, 10);
  assert.notDeepEqual(seen, items(10).map((i) => i.text));
});

test("a new round never starts with the entry that ended the last one", () => {
  for (let seed = 1; seed < 200; seed += 1) {
    const deck = createDeck(items(5), lcg(seed));
    let last = null;
    for (let i = 0; i < 40; i += 1) {
      const t = deck.next().text;
      assert.notEqual(t, last, `seed ${seed} step ${i}`);
      last = t;
    }
  }
});

test("with a key function two entries with one key never follow each other", () => {
  const list = [];
  for (let i = 0; i < 12; i += 1) list.push({ text: `x${i}`, author: i < 6 ? "A" : `B${i}` });
  for (let seed = 1; seed < 200; seed += 1) {
    const deck = createDeck(list, lcg(seed), (e) => e.author);
    let last = null;
    for (let i = 0; i < 60; i += 1) {
      const e = deck.next();
      if (last) assert.notEqual(e.author, last.author, `seed ${seed} step ${i}`);
      last = e;
    }
  }
});

test("the key rule gives way when there is nothing else to show", () => {
  const deck = createDeck([{ text: "a", author: "A" }, { text: "b", author: "A" }], lcg(3), (e) => e.author);
  assert.ok(deck.next());
  assert.ok(deck.next());
  assert.ok(deck.next());
});

test("a single entry repeats and an empty list gives null", () => {
  const one = createDeck([{ text: "only" }], lcg(1));
  assert.equal(one.next().text, "only");
  assert.equal(one.next().text, "only");
  assert.equal(createDeck([], lcg(1)).next(), null);
});

test("hold time: a fact is about 60 s and longer when long", () => {
  assert.equal(holdMs("facts", "x".repeat(40)), 60000);
  assert.equal(holdMs("facts", "x".repeat(100)), 60000);
  assert.ok(holdMs("facts", "x".repeat(160)) > 60000);
  assert.ok(holdMs("facts", "x".repeat(160)) <= 90000);
});

test("hold time: a quote is 45 to 90 s by length", () => {
  assert.equal(holdMs("quotes", ""), 45000);
  assert.ok(holdMs("quotes", "x".repeat(10)) >= 45000);
  assert.equal(holdMs("quotes", "x".repeat(220)), 90000);
  assert.equal(holdMs("quotes", "x".repeat(500)), 90000);
  const mid = holdMs("quotes", "x".repeat(110));
  assert.ok(mid > 45000 && mid < 90000);
});

test("the fade is 1.6 s", () => {
  assert.equal(FADE_MS, 1600);
});

test("text size: quotes never go under 28 px, short ones are larger", () => {
  assert.ok(textSize("quotes", 20) > textSize("quotes", 220));
  for (const n of [1, 60, 100, 160, 220, 400]) assert.ok(textSize("quotes", n) >= 28, String(n));
  for (const n of [1, 80, 160, 300]) assert.ok(textSize("facts", n) >= 28, String(n));
});

test("the longest entry still fits 800 by 480 at its size", () => {
  // Average glyph is about 0.52 em wide; line height 1.3; the box is 680 px wide.
  for (const [kind, n] of [["quotes", 220], ["facts", 160]]) {
    const px = textSize(kind, n);
    const perLine = Math.floor(680 / (px * 0.52));
    const lines = Math.ceil(n / perLine);
    const height = lines * px * 1.3 + 140;
    assert.ok(height <= 480, `${kind} ${height}`);
  }
});

test("a place is a fraction pair on screen and moves away from the last one", () => {
  const rand = lcg(7);
  let prev = null;
  for (let i = 0; i < 200; i += 1) {
    const p = pickPlace(rand, prev);
    assert.ok(p.x >= 0 && p.x <= 1 && p.y >= 0 && p.y <= 1);
    prev = p;
  }
  const seq = [0.1, 0.1, 0.9, 0.9];
  let i = 0;
  const far = pickPlace(() => seq[i++ % 4], { x: 0.1, y: 0.1 });
  assert.deepEqual(far, { x: 0.9, y: 0.9 });
});

test("parseEntries keeps valid entries and drops the rest", () => {
  const out = parseEntries([{ text: "a" }, { text: "" }, null, { nope: 1 }, { text: "b", author: "Z" }, "str"]);
  assert.deepEqual(out.map((e) => e.text), ["a", "b"]);
  assert.deepEqual(parseEntries("nope"), []);
  assert.deepEqual(parseEntries(null), []);
});
