import { test } from "node:test";
import assert from "node:assert/strict";
import {
  SKY,
  rotationAt,
  makeStars,
  starPosition,
  starCount,
  createShooter,
  seededRandom,
} from "../../roles/room_picker/files/site/sky-model.js";

const HOUR = 3600 * 1000;
const DEG = Math.PI / 180;

test("the sky turns 15 degrees an hour", () => {
  assert.ok(Math.abs(rotationAt(HOUR) - rotationAt(0) - 15 * DEG) < 1e-9);
  assert.ok(Math.abs(rotationAt(24 * HOUR) - rotationAt(0) - 360 * DEG) < 1e-9);
  assert.ok(Math.abs(rotationAt(HOUR / 2) - rotationAt(0) - 7.5 * DEG) < 1e-9);
});

test("the seeded random is stable and in [0, 1)", () => {
  const a = seededRandom(7);
  const b = seededRandom(7);
  for (let i = 0; i < 50; i += 1) {
    const x = a();
    assert.equal(x, b());
    assert.ok(x >= 0 && x < 1);
  }
});

test("the star field is stable, sized and varied", () => {
  const one = makeStars();
  const two = makeStars();
  assert.deepEqual(one, two);
  assert.equal(one.length, SKY.stars);
  const sizes = new Set(one.map((s) => s.size.toFixed(2)));
  const brights = new Set(one.map((s) => s.bright.toFixed(2)));
  assert.ok(sizes.size > 10 && brights.size > 10);
  assert.ok(one.every((s) => s.size >= 0.4 && s.size <= 2.4 && s.bright >= 0.3 && s.bright <= 1));
  const tinted = one.filter((s) => s.tint > 0).length;
  assert.ok(tinted > 5 && tinted < one.length / 3, "a few colored stars");
});

test("stars sit in a ring around the pole that covers the whole screen at every angle", () => {
  const stars = makeStars();
  const { poleX, poleY, width, height } = SKY;
  const corners = [[0, 0], [width, 0], [0, height], [width, height]].map(([x, y]) => Math.hypot(x - poleX, y - poleY));
  const rMin = -poleY;
  const rMax = Math.max(...corners);
  assert.ok(stars.every((s) => s.r >= rMin && s.r <= rMax + 1));
  for (const hours of [0, 1, 5, 12, 20]) {
    const angle = rotationAt(hours * HOUR);
    const onScreen = stars.filter((s) => {
      const p = starPosition(s, angle);
      return p.x >= 0 && p.x <= width && p.y >= 0 && p.y <= height;
    }).length;
    assert.ok(onScreen > SKY.stars * 0.08, `${hours} h: ${onScreen} on screen`);
  }
});

test("starPosition turns about the pole (clockwise on screen) and moves little in a minute", () => {
  const s = { r: 300, a: 0, size: 1, bright: 1, tint: 0 };
  const p0 = starPosition(s, 0);
  assert.ok(Math.abs(p0.x - (SKY.poleX + 300)) < 1e-9 && Math.abs(p0.y - SKY.poleY) < 1e-9);
  const p1 = starPosition(s, rotationAt(60 * 1000) - rotationAt(0));
  const moved = Math.hypot(p1.x - p0.x, p1.y - p0.y);
  assert.ok(moved > 1.2 && moved < 1.4, `${moved} px a minute at 300 px, about 0.02 px a second`);
});

test("night shows fewer stars", () => {
  assert.equal(starCount(0), SKY.stars);
  assert.ok(starCount(1) < SKY.stars * 0.6);
  assert.ok(starCount(0.5) < starCount(0) && starCount(0.5) > starCount(1));
});

test("a shooting star comes every few minutes by day and more rarely at night", () => {
  const run = (night, seconds) => {
    let n = 0;
    const sh = createShooter({ random: seededRandom(3) });
    let was = false;
    for (let t = 0; t < seconds; t += 0.2) {
      sh.advance(0.2, night);
      const now = sh.current() !== null;
      if (now && !was) n += 1;
      was = now;
    }
    return n;
  };
  const day = run(0, 3600);
  const night = run(1, 3600);
  assert.ok(day >= 10 && day <= 30, `day ${day} an hour`);
  assert.ok(night >= 3 && night <= 8 && night < day / 2, `night ${night} an hour`);
});

test("a shot lasts about a second, fades, and stays near the screen", () => {
  const sh = createShooter({ random: seededRandom(5) });
  let seen = null;
  let frames = 0;
  for (let t = 0; t < 600 && frames === 0; t += 0.05) {
    sh.advance(0.05, 0);
    if (sh.current()) frames = 1;
  }
  assert.equal(frames, 1);
  let steps = 0;
  let last = null;
  while (sh.current() && steps < 100) {
    seen = sh.current();
    if (last) assert.ok(seen.alpha <= last.alpha + 1e-9, "alpha only falls");
    last = seen;
    sh.advance(0.05, 0);
    steps += 1;
  }
  assert.ok(steps * 0.05 >= 0.7 && steps * 0.05 <= 1.4, `${steps * 0.05}s`);
  assert.ok(seen.x >= 0 && seen.x <= SKY.width + 100 && seen.y >= 0 && seen.y <= SKY.height);
  assert.ok(seen.tailX < seen.x, "tail trails behind");
});

test("a shot is dimmer at night", () => {
  const peak = (night) => {
    const sh = createShooter({ random: seededRandom(5) });
    for (let t = 0; t < 2000; t += 0.05) {
      sh.advance(0.05, night);
      if (sh.current()) return sh.current().alpha;
    }
    return null;
  };
  assert.ok(peak(1) < peak(0));
});
