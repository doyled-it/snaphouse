import { test } from "node:test";
import assert from "node:assert/strict";
import { LIFE, createLife, createStepper, ghostLevel, stepIntervalS } from "../../roles/room_picker/files/site/life-model.js";

const empty = (opts = {}) => createLife({ random: () => 1, lowPop: 0, ...opts });
const alive = (life) => {
  const out = [];
  for (let i = 0; i < life.cells.length; i += 1) {
    if (life.cells[i]) out.push([i % life.cols, Math.floor(i / life.cols)]);
  }
  return out.sort((a, b) => a[1] - b[1] || a[0] - b[0]);
};

test("the grid is 80 by 48", () => {
  assert.equal(LIFE.cols, 80);
  assert.equal(LIFE.rows, 48);
  assert.equal(createLife({ random: () => 1 }).cells.length, 80 * 48);
});

test("a blinker flips between horizontal and vertical", () => {
  const life = empty();
  life.setCells([[10, 10], [11, 10], [12, 10]]);
  life.step();
  assert.deepEqual(alive(life), [[11, 9], [11, 10], [11, 11]]);
  life.step();
  assert.deepEqual(alive(life), [[10, 10], [11, 10], [12, 10]]);
});

test("a block stays put", () => {
  const life = empty();
  life.setCells([[5, 5], [6, 5], [5, 6], [6, 6]]);
  life.step();
  assert.deepEqual(alive(life), [[5, 5], [6, 5], [5, 6], [6, 6]]);
});

test("a glider moves one cell down and right every four steps", () => {
  const life = empty();
  const glider = [[1, 0], [2, 1], [0, 2], [1, 2], [2, 2]];
  life.setCells(glider);
  for (let i = 0; i < 4; i += 1) life.step();
  const moved = glider.map(([x, y]) => [x + 1, y + 1]).sort((a, b) => a[1] - b[1] || a[0] - b[0]);
  assert.deepEqual(alive(life), moved);
});

test("the grid wraps at the edges", () => {
  const life = empty();
  life.setCells([[79, 5], [0, 5], [1, 5]]);
  life.step();
  assert.deepEqual(alive(life), [[0, 4], [0, 5], [0, 6]]);
});

test("age counts steps a cell has lived; a newborn is zero", () => {
  const life = empty();
  life.setCells([[5, 5], [6, 5], [5, 6], [6, 6]]);
  life.step();
  life.step();
  assert.equal(life.age[5 * 80 + 5], 2);
  const blink = empty();
  blink.setCells([[10, 10], [11, 10], [12, 10]]);
  blink.step();
  assert.equal(blink.age[9 * 80 + 11], 0);
  assert.equal(blink.age[10 * 80 + 11], 1);
});

test("a dying cell leaves a ghost that fades and ends at zero", () => {
  const life = empty();
  life.setCells([[10, 10], [11, 10], [12, 10]]);
  life.step();
  const i = 10 * 80 + 10;
  assert.equal(life.ghost[i], 1);
  life.decayGhost(1);
  assert.ok(life.ghost[i] < 1 && life.ghost[i] > 0);
  for (let n = 0; n < 40; n += 1) life.decayGhost(1);
  assert.equal(life.ghost[i], 0);
});

test("a still life is reported as a stall and gets a patch", () => {
  const life = empty({ random: () => 0 });
  life.setCells([[5, 5], [6, 5], [5, 6], [6, 6]]);
  life.step();
  const result = life.step();
  assert.equal(result.reseed, "still");
  assert.ok(alive(life).length > 4);
});

test("a short repeating cycle is a stall", () => {
  const life = empty({ random: () => 1 });
  life.setCells([[10, 10], [11, 10], [12, 10]]);
  const seen = [];
  for (let i = 0; i < 5; i += 1) seen.push(life.step().reseed);
  assert.ok(seen.includes("cycle"));
});

test("a low population is reseeded even when it is changing", () => {
  const life = createLife({ random: () => 1, lowPop: 60 });
  life.setCells([[1, 0], [2, 1], [0, 2], [1, 2], [2, 2]]);
  assert.equal(life.step().reseed, "low");
});

test("a lively grid is left alone", () => {
  let n = 0;
  const life = createLife({ random: () => ((n = (n * 9301 + 49297) % 233280), n / 233280) });
  let reseeds = 0;
  for (let i = 0; i < 20; i += 1) if (life.step().reseed) reseeds += 1;
  assert.equal(reseeds, 0);
});

test("a patch is 20 by 18 cells seeded at 30 percent density", () => {
  let r = 1;
  const life = createLife({ random: () => r, lowPop: 0 });
  assert.equal(alive(life).length, 0);
  r = 0;
  life.seedPatch();
  const cells = alive(life);
  assert.equal(cells.length, 20 * 18);
  assert.equal(Math.min(...cells.map((c) => c[0])), 0);
  assert.equal(Math.max(...cells.map((c) => c[0])), 19);
  assert.equal(Math.max(...cells.map((c) => c[1])), 17);
  life.setCells([]);
  r = 0.5;
  life.seedPatch();
  assert.equal(alive(life).length, 0, "0.5 is above the 0.3 density, so no cell");
});

test("a patch never runs off the grid", () => {
  let r = 1;
  const life = createLife({ random: () => r, lowPop: 0 });
  r = 0.2999;
  life.seedPatch();
  for (const [x, y] of alive(life)) assert.ok(x < 80 && y < 48);
  assert.ok(alive(life).length > 0);
});

test("night steps every 1.5 s and day every 0.5 s, easing between", () => {
  assert.equal(stepIntervalS(0), 0.5);
  assert.equal(stepIntervalS(1), 1.5);
  assert.equal(stepIntervalS(0.5), 1);
});

test("ghosts have 48 levels and the faintest visible one is level 1", () => {
  assert.equal(LIFE.ghostLevels, 48);
  const seen = new Set();
  for (let g = 0.02; g <= 1; g += 0.0005) seen.add(ghostLevel(g));
  assert.equal(seen.size, 48);
  assert.equal(ghostLevel(1), 48);
  assert.equal(ghostLevel(0.02), 1);
});

test("a newborn cell starts invisible and fades in over 0.35 s; a survivor stays full", () => {
  assert.equal(LIFE.birthS, 0.35);
  const life = empty();
  life.setCells([[10, 9], [10, 10], [10, 11]]);
  life.step();
  const born = 10 * 80 + 9;
  const kept = 10 * 80 + 10;
  assert.equal(life.cells[born], 1);
  assert.equal(life.birth[born], 0);
  assert.equal(life.birth[kept], 1);
  life.fadeBirth(0.175);
  assert.ok(Math.abs(life.birth[born] - 0.5) < 1e-6);
  life.fadeBirth(0.2);
  assert.equal(life.birth[born], 1);
  life.fadeBirth(1);
  assert.equal(life.birth[born], 1);
});

test("the birth fade does not depend on the frame rate", () => {
  const fade = (frames) => {
    const life = empty();
    life.setCells([[10, 9], [10, 10], [10, 11]]);
    life.step();
    for (let i = 0; i < frames; i += 1) life.fadeBirth(0.2 / frames);
    return life.birth[10 * 80 + 9];
  };
  assert.ok(Math.abs(fade(1) - fade(12)) < 1e-5);
  assert.ok(Math.abs(fade(12) - fade(60)) < 1e-5);
});

test("a cell that dies loses its fade state, and one that is reborn fades again", () => {
  const life = empty();
  life.setCells([[10, 10]]);
  life.step();
  assert.equal(life.cells[10 * 80 + 10], 0);
  assert.equal(life.birth[10 * 80 + 10], 1);
  life.setCells([[10, 9], [10, 10], [10, 11]]);
  assert.equal(life.birth[10 * 80 + 10], 1, "cells given by setCells are full at once");
});

test("the stepper steps on the old timing at any frame rate", () => {
  const steps = (fps, night) => {
    const st = createStepper();
    let n = 0;
    const frames = Math.round(30.2 * fps);
    for (let i = 0; i < frames; i += 1) n += st.advance(1 / fps, night);
    return n;
  };
  assert.equal(steps(10, 0), steps(60, 0));
  assert.equal(steps(60, 0), 61, "an immediate first step, then one per 0.5 s for 30 s");
  assert.equal(steps(60, 1), 20, "1.5 s at night");
  assert.equal(steps(10, 1), 20);
  assert.ok(Math.abs(steps(45, 0) - 61) <= 1);
});
