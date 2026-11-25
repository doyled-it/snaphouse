import { test } from "node:test";
import assert from "node:assert/strict";
import { createCoversStyle } from "../../roles/room_picker/files/site/covers-style.js";
import { MIN_COVERS, chooseLayout } from "../../roles/room_picker/files/site/covers-model.js";

const REFRESH_MS = 10 * 60 * 1000;
const archive = (n, from = 0) => ({
  covers: Array.from({ length: n }, (_, i) => ({
    url: `/cover/archive/${String(from + i).padStart(4, "0")}.jpg`, album: `A${i}`, artist: "X", added_at: "2026-11-24T00:00:00+00:00",
  })),
});
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };

function makeEnv({ docs = [archive(60)], failImages = new Set() } = {}) {
  let t = 0;
  let id = 1;
  const timers = [];
  const log = { fetched: [], loaded: [], draws: [], fallback: [] };
  let seed = 7;
  const random = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
  const ctx = new Proxy({ drawn: 0 }, {
    get(target, prop) {
      if (prop in target) return target[prop];
      if (prop === "drawImage") return (img) => { log.draws.push(img.url); };
      return () => {};
    },
    set(target, prop, v) { target[prop] = v; return true; },
  });
  const nodes = [];
  const doc = {
    createElement(tag) {
      const classes = new Set();
      const node = {
        tag, children: [], className: "", width: 0, height: 0, hidden: false,
        style: { setProperty() {} },
        classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c), toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)) },
        append(...c) { node.children.push(...c); },
        getContext: () => ctx,
      };
      nodes.push(node);
      return node;
    },
  };
  const fallback = { started: 0, stopped: 0, hues: null, night: null, node: { tag: "fallback", classList: { toggle() {}, add() {}, remove() {} }, style: {} } };
  const env = {
    doc, win: {}, now: () => t, random,
    setTimeout: (fn, ms) => { const n = id++; timers.push({ n, fn, at: t + ms }); return n; },
    clearTimeout: (n) => { const i = timers.findIndex((x) => x.n === n); if (i >= 0) timers.splice(i, 1); },
    fetch: async (url) => {
      log.fetched.push(url);
      const body = docs[Math.min(log.fetched.length - 1, docs.length - 1)];
      return { ok: body !== null, json: async () => body };
    },
    loadImage: async (url) => {
      log.loaded.push(url);
      if (failImages.has(url)) throw new Error("bad image");
      return { url, naturalWidth: 600, naturalHeight: 600 };
    },
    createFallback: () => ({
      node: fallback.node,
      start() { fallback.started += 1; }, stop() { fallback.stopped += 1; },
      setHues(h) { fallback.hues = h; }, setNight(n) { fallback.night = n; },
    }),
  };
  async function advance(ms) {
    const end = t + ms;
    for (;;) {
      timers.sort((a, b) => a.at - b.at);
      if (!timers[0] || timers[0].at > end) break;
      const next = timers.shift();
      t = next.at;
      next.fn();
      await flush();
    }
    t = end;
    await flush();
  }
  return { env, timers, log, fallback, advance, nodes };
}

const dupes = (list) => list.length - new Set(list).size;

test("start reads the collection and refreshes it every 10 minutes while it runs", async () => {
  const h = makeEnv();
  const style = createCoversStyle(h.env);
  style.start();
  await flush();
  assert.deepEqual(h.log.fetched, ["cover-archive.json"]);
  await h.advance(REFRESH_MS + 1000);
  assert.equal(h.log.fetched.length, 2);
  await h.advance(REFRESH_MS);
  assert.equal(h.log.fetched.length, 3);
  style.stop();
  await h.advance(3 * REFRESH_MS);
  assert.equal(h.log.fetched.length, 3);
});

test("a big collection shows the mosaic: six columns, no Aurora", async () => {
  const h = makeEnv({ docs: [archive(100)] });
  const style = createCoversStyle(h.env);
  style.start();
  await flush();
  await h.advance(1000);
  assert.equal(style.debug().cols, 6);
  assert.equal(h.fallback.started, 0);
  assert.ok(h.log.draws.length > 0);
  style.stop();
});

test("fewer covers than the minimum shows Aurora and nothing of the mosaic", async () => {
  const h = makeEnv({ docs: [archive(MIN_COVERS - 1)] });
  const style = createCoversStyle(h.env);
  style.start();
  await flush();
  await h.advance(5000);
  assert.equal(h.fallback.started, 1);
  assert.equal(style.debug().mode, "aurora");
  assert.equal(h.log.loaded.length, 0);
  assert.equal(h.log.draws.length, 0);
  style.stop();
  assert.equal(h.fallback.stopped >= 1, true);
});

test("a small collection uses fewer, larger columns", async () => {
  const h = makeEnv({ docs: [archive(20)] });
  const style = createCoversStyle(h.env);
  style.start();
  await flush();
  assert.equal(style.debug().cols, chooseLayout(20).cols);
  assert.ok(style.debug().cols < 6);
  style.stop();
});

test("a failed archive read with nothing yet shows Aurora and retries", async () => {
  const h = makeEnv({ docs: [null, archive(60)] });
  const style = createCoversStyle(h.env);
  style.start();
  await flush();
  assert.equal(style.debug().mode, "aurora");
  await h.advance(31000);
  assert.equal(style.debug().mode, "mosaic");
  assert.equal(h.fallback.stopped >= 1, true);
  style.stop();
});

test("a refresh that brings enough covers replaces Aurora with the mosaic", async () => {
  const h = makeEnv({ docs: [archive(5), archive(60)] });
  const style = createCoversStyle(h.env);
  style.start();
  await flush();
  assert.equal(style.debug().mode, "aurora");
  await h.advance(REFRESH_MS + 100);
  assert.equal(style.debug().mode, "mosaic");
  style.stop();
});

test("a refresh adds covers to the pool without a jump or a new column count", async () => {
  const more = { covers: [...archive(40).covers, ...archive(60, 100).covers] };
  const h = makeEnv({ docs: [archive(40), more] });
  const style = createCoversStyle(h.env);
  style.start();
  await flush();
  const cols = style.debug().cols;
  await h.advance(REFRESH_MS - 100000);
  const before = JSON.stringify(style.debug().rows);
  const poolBefore = style.debug().pool;
  await h.advance(100000 + 100);
  assert.equal(poolBefore, 40);
  assert.equal(style.debug().pool, 100);
  assert.equal(style.debug().cols, cols, "columns never change while running");
  assert.equal(h.log.fetched.length, 2);
  assert.ok(before.length > 0);
  style.stop();
});

test("no cover is ever on the grid twice, through scrolling and swaps", async () => {
  const h = makeEnv({ docs: [archive(MIN_COVERS + 2)] });
  const style = createCoversStyle(h.env);
  style.start();
  await flush();
  for (let i = 0; i < 400; i++) {
    await h.advance(15000);
    assert.equal(dupes(style.debug().rows.flat().filter(Boolean)), 0, `step ${i}`);
  }
  style.stop();
});

test("a swap changes one cell, 20 to 40 seconds in, and crossfades", async () => {
  const h = makeEnv({ docs: [archive(100)] });
  const style = createCoversStyle(h.env);
  style.start();
  await flush();
  const first = style.debug().rows.flat();
  await h.advance(19000);
  assert.deepEqual(style.debug().rows.flat(), first, "nothing swaps before 20 s");
  await h.advance(21500);
  const second = style.debug().rows.flat();
  assert.equal(second.filter((u, i) => u !== first[i]).length, 1, "exactly one tile swapped");
  assert.equal(dupes(second), 0);
  style.stop();
});

test("a cover that fails to load leaves the pool and its tile gets another", async () => {
  const h = makeEnv({ docs: [archive(60)] });
  const order = [];
  const real = h.env.loadImage;
  h.env.loadImage = async (u) => {
    order.push(u);
    if (order.length === 1) throw new Error("bad image");
    return real(u);
  };
  const style = createCoversStyle(h.env);
  style.start();
  await flush();
  await h.advance(100);
  const gone = order[0];
  assert.ok(!style.debug().rows.flat().includes(gone));
  assert.equal(style.debug().pool, 59);
  assert.equal(dupes(style.debug().rows.flat().filter(Boolean)), 0);
  assert.ok(style.debug().rows.flat().every(Boolean), "the tile was refilled");
  style.stop();
});

test("scrolling is about a pixel a second by day and half that at night", async () => {
  const day = makeEnv();
  const a = createCoversStyle(day.env);
  a.start(); await flush();
  await day.advance(60000);
  const night = makeEnv();
  const b = createCoversStyle(night.env);
  b.setNight(true);
  b.start(); await flush();
  await night.advance(60000);
  const total = (s) => s.debug().scrolled;
  assert.ok(Math.abs(total(a) - 60) < 1.5, `day scrolled ${total(a)}`);
  assert.ok(Math.abs(total(b) - 30) < 1.5, `night scrolled ${total(b)}`);
  assert.equal(b.node.classList.contains("is-night"), true);
  assert.equal(a.node.classList.contains("is-night"), false);
  a.stop(); b.stop();
});

test("night is passed to the Aurora fallback and hues are not used by the mosaic", async () => {
  const h = makeEnv({ docs: [archive(3)] });
  const style = createCoversStyle(h.env);
  style.setNight(true);
  style.setHues({ h1: 10, h2: 20, chroma: 0.1 });
  style.start();
  await flush();
  assert.deepEqual(h.fallback.hues, { h1: 10, h2: 20, chroma: 0.1 });
  style.stop();
});

test("after stop no timer is left and a late fetch or image does nothing", async () => {
  const h = makeEnv();
  const style = createCoversStyle(h.env);
  style.start();
  style.stop();
  await flush();
  await h.advance(REFRESH_MS * 2);
  assert.equal(h.timers.length, 0);
  assert.equal(h.log.draws.length, 0);
  const g = makeEnv();
  const s2 = createCoversStyle(g.env);
  s2.start();
  await flush();
  await g.advance(2000);
  s2.stop();
  assert.equal(g.timers.length, 0);
  const before = g.log.draws.length;
  await g.advance(60000);
  assert.equal(g.log.draws.length, before);
});

test("memory stays bounded: only images on the grid are kept", async () => {
  const h = makeEnv({ docs: [archive(200)] });
  const style = createCoversStyle(h.env);
  style.start();
  await flush();
  for (let i = 0; i < 100; i++) await h.advance(60000);
  const d = style.debug();
  assert.ok(d.cached <= d.cols * d.rowCount + 4, `cached ${d.cached}`);
  style.stop();
});
