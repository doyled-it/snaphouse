import { test } from "node:test";
import assert from "node:assert/strict";
import {
  PLANETS,
  ORRERY,
  longitudeAt,
  ringLayout,
  placeLabels,
  dateLabel,
  driftAt,
  DATE_SPOT,
} from "../../roles/room_picker/files/site/planets-model.js";

// Heliocentric ecliptic longitudes (J2000 frame) from astropy's built-in planetary
// ephemeris (ERFA plan94), the reference the formulas are checked against.
// Order: Mercury, Venus, Earth, Mars, Jupiter, Saturn, Uranus, Neptune.
const REFERENCE = [
  ["2000-01-01T12:00:00Z", [253.79, 182.6, 100.38, 359.45, 36.29, 45.73, 316.42, 303.93]],
  ["2010-06-21T12:00:00Z", [52.72, 189.22, 269.88, 192.62, 349.85, 184.2, 357.46, 326.78]],
  ["2020-12-21T12:00:00Z", [272.23, 215.12, 89.8, 60.08, 305.74, 303.03, 38.96, 349.87]],
  ["2026-03-20T12:00:00Z", [214.54, 41.65, 179.52, 332.43, 115.42, 4.2, 60.43, 1.47]],
  ["2026-11-24T12:00:00Z", [158.02, 80.7, 61.84, 111.45, 135.46, 12.67, 63.28, 2.98]],
  ["2035-09-23T00:00:00Z", [347.94, 208.18, 359.32, 356.83, 40.95, 130.39, 101.27, 22.55]],
];
const diff = (a, b) => Math.abs(((a - b + 540) % 360) - 180);

test("there are the eight planets, sun outward", () => {
  assert.deepEqual(PLANETS.map((p) => p.name), ["Mercury", "Venus", "Earth", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune"]);
});

test("longitudes match the reference ephemeris within 1 degree (measured worst: 0.33, Mercury)", () => {
  let worst = 0;
  for (const [iso, want] of REFERENCE) {
    const ms = Date.parse(iso);
    PLANETS.forEach((p, i) => {
      const err = diff(longitudeAt(p, ms), want[i]);
      worst = Math.max(worst, err);
      assert.ok(err < 1, `${p.name} ${iso}: ${longitudeAt(p, ms).toFixed(2)} vs ${want[i]} (${err.toFixed(2)})`);
    });
  }
  assert.ok(worst < 1);
});

test("Earth sits near 90, 180, 270 and 0 degrees at the solstices and equinoxes", () => {
  const earth = PLANETS.find((p) => p.name === "Earth");
  // The sun's longitude is the Earth's plus 180. Dates are the published UTC instants.
  const cases = [
    ["2020-03-20T03:50:00Z", 180], // march equinox: sun at 0, Earth at 180
    ["2020-06-20T21:44:00Z", 270], // june solstice: sun at 90
    ["2020-09-22T13:31:00Z", 0], // september equinox: sun at 180
    ["2020-12-21T10:02:00Z", 90], // december solstice: sun at 270
  ];
  for (const [iso, want] of cases) {
    // longitudes here are of the J2000 equinox, so allow about half a degree of precession
    assert.ok(diff(longitudeAt(earth, Date.parse(iso)), want) < 1.5, `${iso}`);
  }
});

test("the inner planets move faster, and each planet completes its period", () => {
  const day = 86400000;
  const t0 = Date.parse("2026-01-01T00:00:00Z");
  const step = (p) => diff(longitudeAt(p, t0 + day), longitudeAt(p, t0));
  assert.ok(step(PLANETS[0]) > step(PLANETS[1]));
  assert.ok(step(PLANETS[1]) > step(PLANETS[2]));
  assert.ok(step(PLANETS[2]) > step(PLANETS[7]));
  const earth = PLANETS[2];
  assert.ok(diff(longitudeAt(earth, t0 + 365.256 * day), longitudeAt(earth, t0)) < 0.5);
});

test("rings are evenly spaced, tilted, and inside the screen", () => {
  const rings = ringLayout();
  assert.equal(rings.length, 8);
  const gaps = rings.slice(1).map((r, i) => r.rx - rings[i].rx);
  assert.ok(gaps.every((g) => Math.abs(g - gaps[0]) < 1e-9));
  assert.ok(rings.every((r) => r.ry < r.rx && r.ry > 0));
  const last = rings.at(-1);
  assert.ok(ORRERY.cx + last.rx < ORRERY.width && ORRERY.cx - last.rx > 0);
  assert.ok(ORRERY.cy + last.ry < ORRERY.height && ORRERY.cy - last.ry > 0);
});

test("a planet's position follows its longitude around the tilted ring", () => {
  const rings = ringLayout();
  const spots = placeLabels({ ms: Date.parse("2026-11-24T12:00:00Z"), measure: (n) => n.length * 10, fontPx: 18 });
  spots.forEach((s, i) => {
    const a = (longitudeAt(PLANETS[i], Date.parse("2026-11-24T12:00:00Z")) * Math.PI) / 180;
    assert.ok(Math.abs(s.x - (ORRERY.cx + rings[i].rx * Math.cos(a))) < 1e-9);
    assert.ok(Math.abs(s.y - (ORRERY.cy - rings[i].ry * Math.sin(a))) < 1e-9);
  });
});

test("labels never overlap each other or the screen edge, at any date over 120 years", () => {
  const measure = (n) => Math.ceil(n.length * 18 * 0.58); // generous system-ui estimate
  const day = 86400000;
  const start = Date.UTC(2000, 0, 1);
  let checked = 0;
  for (let d = 0; d < 120 * 365; d += 11) {
    const spots = placeLabels({ ms: start + d * day, measure, fontPx: 18 });
    const boxes = spots.map((s) => s.label);
    boxes.forEach((b, i) => {
      assert.ok(b.x >= 0 && b.y >= 0 && b.x + b.w <= ORRERY.width && b.y + b.h <= ORRERY.height, `edge, day ${d}, ${spots[i].name}`);
      for (let j = 0; j < i; j += 1) {
        const o = boxes[j];
        const clear = b.x >= o.x + o.w || o.x >= b.x + b.w || b.y >= o.y + o.h || o.y >= b.y + b.h;
        assert.ok(clear, `overlap, day ${d}: ${spots[i].name} / ${spots[j].name}`);
      }
    });
    checked += 1;
  }
  assert.ok(checked > 3500);
});

test("the date reads as month day, year, in local time", () => {
  assert.equal(dateLabel(new Date(2026, 10, 24, 15, 0)), "November 24, 2026");
  assert.equal(dateLabel(new Date(2027, 0, 5, 0, 1)), "January 5, 2027");
});

test("the composition drifts slowly: at least 1 px in 3 minutes, smooth", () => {
  const min = 60000;
  let least = Infinity;
  let maxStep = 0;
  let prev = driftAt(0);
  for (let t = 0; t < 86400000; t += 20000) {
    const p = driftAt(t);
    const q = driftAt(t + 3 * min);
    least = Math.min(least, Math.hypot(q.dx - p.dx, q.dy - p.dy));
  }
  for (let t = 1000; t < 3600000; t += 1000) {
    const p = driftAt(t);
    maxStep = Math.max(maxStep, Math.hypot(p.dx - prev.dx, p.dy - prev.dy));
    prev = p;
  }
  assert.ok(least >= 1, `least 3 minute move ${least.toFixed(2)} px`);
  assert.ok(maxStep < 0.5, `${maxStep.toFixed(2)} px a second, smooth at one redraw a second`);
});

test("sun, rings, names and date stay on screen across a day of drift", () => {
  const measure = (n) => Math.ceil(n.length * 18 * 0.58);
  const rings = ringLayout();
  const last = rings.at(-1);
  const dateW = 280; // generous width of the longest date at 20 px
  for (let t = 0; t < 86400000; t += 61000) {
    const { dx, dy } = driftAt(t);
    const cx = ORRERY.cx + dx;
    const cy = ORRERY.cy + dy;
    assert.ok(cx - last.rx - 1 >= 0 && cx + last.rx + 1 <= ORRERY.width, `rings x at ${t}`);
    assert.ok(cy - last.ry - 1 >= 0 && cy + last.ry + 1 <= ORRERY.height, `rings y at ${t}`);
    assert.ok(cx - 40 >= 0 && cy - 40 >= 0, "sun glow");
    for (const spot of placeLabels({ ms: Date.UTC(2026, 10, 24) + t * 37, measure, cx, cy })) {
      const b = spot.label;
      assert.ok(b.x >= 0 && b.y >= 0 && b.x + b.w <= ORRERY.width && b.y + b.h <= ORRERY.height, `${spot.name} at ${t}`);
    }
    assert.ok(DATE_SPOT.x + dx >= 0 && DATE_SPOT.x + dx + dateW <= ORRERY.width, "date x");
    assert.ok(DATE_SPOT.y + dy - 20 >= 0 && DATE_SPOT.y + dy + 6 <= ORRERY.height, "date y");
  }
});
