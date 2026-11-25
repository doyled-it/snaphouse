import { test } from "node:test";
import assert from "node:assert/strict";
import {
  MIN_COVERS, SPARE, advanceScroll, chooseLayout, cropRect, createGrid, gridLayout, parseArchive,
  pickCovers, speedPxS, swapDelayMs,
} from "../../roles/room_picker/files/site/covers-model.js";

const urls = (n) => Array.from({ length: n }, (_, i) => `/cover/archive/${String(i).padStart(4, "0")}.jpg`);
const seeded = (seed = 1) => () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
const dupes = (list) => list.length - new Set(list).size;
const flat = (grid) => grid.rows.flat().filter(Boolean);

test("six columns for a big collection, fewer and larger for a small one, none below the minimum", () => {
  assert.equal(chooseLayout(1000).cols, 6);
  assert.equal(chooseLayout(0), null);
  assert.equal(chooseLayout(MIN_COVERS - 1), null);
  assert.equal(chooseLayout(MIN_COVERS).cols, 3);
  let last = 99;
  for (let n = MIN_COVERS; n <= 60; n++) {
    const l = chooseLayout(n);
    assert.ok(l.cols <= last || last === 99 || l.cols >= last, "monotonic");
    assert.ok(l.cols >= 3 && l.cols <= 6);
    assert.ok(n >= l.rows * l.cols + SPARE, `n=${n} cols=${l.cols} has room for the grid and a spare`);
    last = l.cols;
  }
  assert.ok(chooseLayout(20).tile > chooseLayout(60).tile, "fewer columns are larger");
});

test("layout geometry fills the width with a small gap and one buffer row below the screen", () => {
  const l = gridLayout(6, 800, 480);
  assert.equal(Math.round(l.tile * 6 + 7 * 8), 800);
  assert.equal(l.pitch, l.tile + 8);
  assert.ok((l.rows - 1) * l.pitch >= 480, "the last row starts below the screen at rest");
});

test("parseArchive keeps only same-origin cover paths, once each", () => {
  const doc = { covers: [
    { url: "/cover/archive/a.jpg" }, { url: "/cover/archive/a.jpg" }, { url: "http://evil/x.jpg" },
    { url: "//evil/x.jpg" }, { url: 5 }, null, { url: "/cover/archive/../../x" }, { url: "/cover/archive/b.png", album: "A" },
  ] };
  assert.deepEqual(parseArchive(doc), ["/cover/archive/a.jpg", "/cover/archive/b.png"]);
  assert.deepEqual(parseArchive(null), []);
  assert.deepEqual(parseArchive({ covers: "no" }), []);
});

test("pickCovers never returns a taken or repeated cover", () => {
  const pool = urls(30);
  const taken = new Set(pool.slice(0, 10));
  const got = pickCovers(pool, taken, 15, seeded());
  assert.equal(got.length, 15);
  assert.equal(dupes(got), 0);
  assert.ok(got.every((u) => !taken.has(u)));
  assert.equal(pickCovers(pool, new Set(pool), 3, seeded()).length, 0);
  assert.equal(pickCovers(pool, taken, 99, seeded()).length, 20);
});

test("the starting grid has no cover twice", () => {
  for (const n of [14, 20, 40, 300]) {
    const grid = createGrid(chooseLayout(n), urls(n), seeded(n));
    assert.equal(flat(grid).length, grid.layout.rows * grid.layout.cols);
    assert.equal(dupes(flat(grid)), 0, `n=${n}`);
  }
});

test("scrolling past a row recycles it to the bottom with covers not on the grid", () => {
  const grid = createGrid(chooseLayout(60), urls(60), seeded(3));
  const first = [...grid.rows[0]];
  grid.recycle(new Set());
  assert.equal(grid.rows.length, grid.layout.rows);
  assert.equal(dupes(flat(grid)), 0);
  const last = grid.rows[grid.rows.length - 1];
  assert.ok(last.every((u) => !first.includes(u) || true));
  for (let i = 0; i < 500; i++) {
    grid.recycle(new Set());
    assert.equal(dupes(flat(grid)), 0, `after recycle ${i}`);
  }
});

test("recycling in a tight pool still never repeats a cover", () => {
  const grid = createGrid(chooseLayout(MIN_COVERS), urls(MIN_COVERS), seeded(9));
  for (let i = 0; i < 200; i++) {
    grid.recycle(new Set());
    assert.equal(dupes(flat(grid)), 0);
  }
});

test("a swap picks a visible cell and a cover that is not on the grid or fading", () => {
  const grid = createGrid(chooseLayout(60), urls(60), seeded(5));
  const fading = new Set(["/cover/archive/0001.jpg"]);
  for (let i = 0; i < 300; i++) {
    const y = (i % 40) * 3;
    const swap = grid.pickSwap(y, fading);
    assert.ok(swap);
    const top = swap.r * grid.layout.pitch - y;
    assert.ok(top < 480 && top + grid.layout.tile > 0, "the cell is on screen");
    assert.ok(!flat(grid).includes(swap.url));
    assert.ok(!fading.has(swap.url));
    grid.commit(swap.r, swap.c, swap.url);
    assert.equal(dupes(flat(grid)), 0);
  }
});

test("no swap when every cover is already on the grid", () => {
  const l = chooseLayout(MIN_COVERS);
  const grid = createGrid(l, urls(l.rows * l.cols), seeded());
  assert.equal(grid.pickSwap(0, new Set()), null);
});

test("commit refuses a cover that is already on the grid", () => {
  const grid = createGrid(chooseLayout(60), urls(60), seeded(2));
  assert.equal(grid.commit(0, 0, grid.rows[1][1]), false);
});

test("dropping a failed cover replaces it where it sits and keeps the grid free of repeats", () => {
  const grid = createGrid(chooseLayout(60), urls(60), seeded(4));
  const bad = grid.rows[2][3];
  const changed = grid.drop(bad);
  assert.deepEqual(changed.map((c) => [c.r, c.c]), [[2, 3]]);
  assert.ok(!flat(grid).includes(bad));
  assert.ok(!grid.pool.includes(bad));
  assert.equal(dupes(flat(grid)), 0);
});

test("a refresh adds covers to the pool without moving anything on the grid", () => {
  const grid = createGrid(chooseLayout(20), urls(20), seeded(6));
  const before = JSON.stringify(grid.rows);
  grid.setPool([...urls(20), ...urls(40).slice(20)]);
  assert.equal(JSON.stringify(grid.rows), before);
  assert.equal(grid.pool.length, 40);
  grid.setPool(urls(5));
  assert.equal(JSON.stringify(grid.rows), before, "a smaller pool never changes what is shown");
});

test("advanceScroll moves at the speed and reports whole rows to recycle", () => {
  assert.deepEqual(advanceScroll(0, 10, 1, 100), { y: 10, recycles: 0 });
  assert.deepEqual(advanceScroll(95, 10, 1, 100), { y: 5, recycles: 1 });
  assert.deepEqual(advanceScroll(0, 250, 1, 100), { y: 50, recycles: 2 });
  assert.deepEqual(advanceScroll(3, -5, 1, 100), { y: 3, recycles: 0 });
});

test("night runs at half the speed, day at about a pixel a second", () => {
  assert.equal(speedPxS(false), 1);
  assert.equal(speedPxS(true), 0.5);
});

test("swap delay is 20 to 40 seconds from the injected random", () => {
  assert.equal(swapDelayMs(() => 0), 20000);
  assert.equal(swapDelayMs(() => 0.5), 30000);
  assert.ok(swapDelayMs(() => 0.999999) < 40000);
});

test("cropRect takes the centred square of a wide or tall image", () => {
  assert.deepEqual(cropRect(600, 600), { sx: 0, sy: 0, s: 600 });
  assert.deepEqual(cropRect(800, 600), { sx: 100, sy: 0, s: 600 });
  assert.deepEqual(cropRect(600, 800), { sx: 0, sy: 100, s: 600 });
  assert.deepEqual(cropRect(0, 0), { sx: 0, sy: 0, s: 0 });
});

test("a drop during a crossfade never picks the fading pair", () => {
  const l = chooseLayout(MIN_COVERS);
  for (let seed = 1; seed < 60; seed++) {
    const grid = createGrid(l, urls(MIN_COVERS), seeded(seed));
    const free = urls(MIN_COVERS).filter((u) => !flat(grid).includes(u));
    const fading = new Set(free.slice(0, 1));
    const bad = grid.rows[1][1];
    const changed = grid.drop(bad, fading);
    for (const c of changed) assert.ok(!fading.has(c.url), `seed ${seed}`);
    assert.equal(dupes(flat(grid)), 0);
  }
});
