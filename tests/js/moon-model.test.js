import { test } from "node:test";
import assert from "node:assert/strict";
import {
  SYNODIC_DAYS,
  MOON,
  phaseAt,
  litFraction,
  phaseName,
  nextEvent,
  nextText,
  terminatorU,
  shadeAt,
  driftAt,
  textLines,
} from "../../roles/room_picker/files/site/moon-model.js";

const DAY = 86400000;
const at = (iso) => Date.parse(iso);

// Published instants (UTC) of new and full moons: eclipses and well known supermoons.
const NEW_MOONS = ["2000-01-06T18:14:00Z", "2017-08-21T18:30:00Z", "2024-04-08T18:21:00Z", "2024-10-02T18:49:00Z"];
const FULL_MOONS = ["2000-01-21T04:40:00Z", "2015-09-28T02:50:00Z", "2019-01-21T05:16:00Z", "2022-11-08T11:02:00Z"];

// Distance in days from the nearest cycle boundary: 0 is new, 0.5 is full.
const offFromNew = (ms) => {
  const p = phaseAt(ms);
  return Math.min(p, 1 - p) * SYNODIC_DAYS;
};
const offFromFull = (ms) => Math.abs(phaseAt(ms) - 0.5) * SYNODIC_DAYS;

test("the synodic month is 29.530588 days", () => {
  assert.ok(Math.abs(SYNODIC_DAYS - 29.530588) < 1e-6);
});

test("phase is near 0 at published new moons and near 0.5 at published full moons (within a day)", () => {
  let worst = 0;
  for (const iso of NEW_MOONS) {
    const off = offFromNew(at(iso));
    worst = Math.max(worst, off);
    assert.ok(off < 1, `new ${iso}: ${off.toFixed(2)} d`);
  }
  for (const iso of FULL_MOONS) {
    const off = offFromFull(at(iso));
    worst = Math.max(worst, off);
    assert.ok(off < 1, `full ${iso}: ${off.toFixed(2)} d`);
  }
  assert.ok(worst < 1);
});

test("phase stays in [0, 1) before and after the epoch", () => {
  for (const d of [-100000, -1, -0.001, 0, 0.001, 1, 29.5, 29.6, 100000]) {
    const p = phaseAt(at("2000-01-06T18:14:00Z") + d * DAY);
    assert.ok(p >= 0 && p < 1, `${d}: ${p}`);
  }
});

test("lit fraction is 0 at new, 0.5 at the quarters and 1 at full", () => {
  assert.ok(litFraction(0) < 1e-9);
  assert.ok(Math.abs(litFraction(0.25) - 0.5) < 1e-9);
  assert.ok(Math.abs(litFraction(0.5) - 1) < 1e-9);
  assert.ok(Math.abs(litFraction(0.75) - 0.5) < 1e-9);
});

test("phase names follow the eight phases", () => {
  const names = [0, 0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875].map(phaseName);
  assert.deepEqual(names, ["New moon", "Waxing crescent", "First quarter", "Waxing gibbous", "Full moon", "Waning gibbous", "Last quarter", "Waning crescent"]);
  assert.equal(phaseName(0.99), "New moon");
  assert.equal(phaseName(0.02), "New moon");
  assert.equal(phaseName(0.52), "Full moon");
});

test("the next full or new moon is whichever is nearer, with its days", () => {
  assert.deepEqual(nextEvent(0.1).kind, "full");
  assert.ok(Math.abs(nextEvent(0.1).days - 0.4 * SYNODIC_DAYS) < 1e-9);
  assert.equal(nextEvent(0.6).kind, "new");
  assert.ok(Math.abs(nextEvent(0.6).days - 0.4 * SYNODIC_DAYS) < 1e-9);
  assert.equal(nextEvent(0.9).kind, "new");
  assert.equal(nextEvent(0.4).kind, "full");
  // just after full the next event is the new moon about two weeks away
  assert.equal(nextEvent(0.51).kind, "new");
});

test("the next-event text rounds to whole days and says today and tomorrow", () => {
  assert.equal(nextText({ kind: "full", days: 3.4 }), "Full moon in 3 days");
  assert.equal(nextText({ kind: "new", days: 13.6 }), "New moon in 14 days");
  assert.equal(nextText({ kind: "full", days: 1.2 }), "Full moon tomorrow");
  assert.equal(nextText({ kind: "new", days: 0.3 }), "New moon today");
});

test("the three text lines for a date", () => {
  const lines = textLines(at("2024-04-08T18:21:00Z"));
  assert.equal(lines.name, "New moon");
  assert.equal(lines.lit, "0% lit");
  assert.match(lines.next, /^(Full|New) moon/);
  const full = textLines(at("2022-11-08T11:02:00Z"));
  assert.equal(full.name, "Full moon");
  assert.ok(/^(9\d|100)% lit$/.test(full.lit), full.lit);
});

test("the terminator is lit on the right while waxing and the left while waning", () => {
  assert.ok(Math.abs(terminatorU(0, 0.25)) < 1e-9, "quarter: straight down the middle");
  assert.ok(terminatorU(0, 0.1) > 0.4, "waxing crescent: terminator right of centre");
  assert.ok(terminatorU(0, 0.4) < 0, "waxing gibbous: terminator left of centre");
  assert.ok(terminatorU(0, 0.9) < -0.4, "waning crescent: terminator left");
  assert.ok(terminatorU(0, 0.6) > 0, "waning gibbous: terminator right");
  assert.ok(Math.abs(terminatorU(1, 0.1)) < 1e-9 && Math.abs(terminatorU(-1, 0.1)) < 1e-9, "meets at the poles");
  assert.ok(Math.abs(terminatorU(0.6, 0.1) - 0.8 * terminatorU(0, 0.1)) < 1e-9, "an ellipse");
});

test("shade is dark away from the sun, bright toward it, and soft across the terminator", () => {
  // waxing crescent, lit side right
  assert.ok(shadeAt(0.9, 0, 0.1) > 0.95);
  assert.ok(shadeAt(-0.9, 0, 0.1) < MOON.earthshine + 0.01);
  const edge = terminatorU(0, 0.1);
  const across = [-0.08, -0.03, 0, 0.03, 0.08].map((d) => shadeAt(edge + d, 0, 0.1));
  for (let i = 1; i < across.length; i += 1) assert.ok(across[i] >= across[i - 1], "monotone across the edge");
  assert.ok(across[0] < 0.5 && across[4] > 0.5 && across[2] > 0.1 && across[2] < 0.9, "a soft edge, not a step");
  // the lit side is mirrored while waning
  assert.ok(shadeAt(-0.9, 0, 0.9) > 0.95 && shadeAt(0.9, 0, 0.9) < MOON.earthshine + 0.01);
  // new moon: all dark, full moon: all lit
  assert.ok(shadeAt(0, 0, 0) <= MOON.earthshine + 1e-9);
  assert.ok(shadeAt(0.5, 0.3, 0.5) > 0.95 && shadeAt(-0.5, -0.3, 0.5) > 0.95);
});

test("the lit area of the shading matches the lit fraction", () => {
  const n = 120;
  for (const ph of [0.05, 0.15, 0.25, 0.4, 0.6, 0.75, 0.9]) {
    let lit = 0;
    let all = 0;
    for (let j = 0; j < n; j += 1) {
      for (let i = 0; i < n; i += 1) {
        const u = ((i + 0.5) / n) * 2 - 1;
        const v = ((j + 0.5) / n) * 2 - 1;
        if (u * u + v * v > 1) continue;
        all += 1;
        if (shadeAt(u, v, ph) > 0.5 + MOON.earthshine / 2) lit += 1;
      }
    }
    assert.ok(Math.abs(lit / all - litFraction(ph)) < 0.03, `${ph}: ${(lit / all).toFixed(3)} vs ${litFraction(ph).toFixed(3)}`);
  }
});

test("the drift is slow, continuous, and keeps the moon and its text on screen for days", () => {
  const w = 300; // generous width of the longest text line
  let prev = driftAt(0);
  let maxStep = 0;
  for (let s = 0; s < 3 * 86400; s += 7) {
    const p = driftAt(s * 1000);
    maxStep = Math.max(maxStep, Math.hypot(p.cx - prev.cx, p.cy - prev.cy));
    prev = p;
    assert.ok(p.cx - MOON.radius >= 0 && p.cx + MOON.radius <= MOON.width, `x ${p.cx}`);
    assert.ok(p.cy - MOON.radius >= 0 && p.cy + MOON.radius <= MOON.height, `y ${p.cy}`);
    assert.ok(p.textX + w <= MOON.width && p.textX > p.cx + MOON.radius, `text ${p.textX}`);
    assert.ok(p.textY - 60 >= 0 && p.textY + 80 <= MOON.height);
  }
  assert.ok(maxStep < 2, `${maxStep} px in 7 s`);
  const xs = [];
  for (let s = 0; s < 3600; s += 30) xs.push(driftAt(s * 1000).cx);
  assert.ok(Math.max(...xs) - Math.min(...xs) > 20, "it really moves");
});
