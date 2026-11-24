// Pure rules for the Planets style: where the eight planets are around the sun for a
// given moment, the evenly spaced tilted rings they ride on (not to scale), and a
// label placement that keeps every name clear of the others at any date.
//
// Longitudes are heliocentric ecliptic, J2000 equinox. The mean longitude is the J2000
// value plus a steady rate (the mean elements of E. M. Standish, "Keplerian Elements
// for Approximate Positions of the Major Planets", JPL, valid 1800 to 2050), plus the
// equation of centre to the third power of the eccentricity, so the orbit is an ellipse
// seen only through its speed, not drawn. Checked against an ephemeris in the tests:
// within 1 degree for every planet (worst 0.33, Mercury). Precession since
// J2000 (about 0.4 degree) is ignored.

const J2000_MS = Date.UTC(2000, 0, 1, 12);
const CENTURY_MS = 36525 * 86400000;

// l0: mean longitude at J2000 (deg); rate: deg per Julian century; e: eccentricity;
// peri: longitude of perihelion (deg); size: dot radius; rgb: dot color.
export const PLANETS = Object.freeze([
  { name: "Mercury", l0: 252.2503235, rate: 149472.67411175, e: 0.20563593, peri: 77.45779628, size: 3, rgb: [200, 195, 185] },
  { name: "Venus", l0: 181.9790995, rate: 58517.81538729, e: 0.00677672, peri: 131.60246718, size: 4.5, rgb: [232, 208, 160] },
  { name: "Earth", l0: 100.46457166, rate: 35999.37244981, e: 0.01671123, peri: 102.93768193, size: 4.5, rgb: [110, 180, 230] },
  { name: "Mars", l0: -4.55343205, rate: 19140.30268499, e: 0.0933941, peri: -23.94362959, size: 3.5, rgb: [210, 120, 90] },
  { name: "Jupiter", l0: 34.39644051, rate: 3034.74612775, e: 0.04838624, peri: 14.72847983, size: 8, rgb: [225, 190, 150] },
  { name: "Saturn", l0: 49.95424423, rate: 1222.49362201, e: 0.05386179, peri: 92.59887831, size: 7, rgb: [225, 205, 150] },
  { name: "Uranus", l0: 313.23810451, rate: 428.48202785, e: 0.04725744, peri: 170.9542763, size: 5.5, rgb: [160, 215, 225] },
  { name: "Neptune", l0: -55.12002969, rate: 218.45945325, e: 0.00859048, peri: 44.96476227, size: 5.5, rgb: [120, 150, 235] },
]);

export const ORRERY = Object.freeze({
  width: 800,
  height: 480,
  cx: 400,
  cy: 240,
  firstRx: 46,
  gap: 44,
  tilt: 0.42, // ry = rx * tilt, seen from slightly above
  fontPx: 18,
  fps: 1, // the inner planets creep: one redraw a second is plenty
});

const RAD = Math.PI / 180;

// Heliocentric longitude in degrees [0, 360) at a clock time in ms.
export function longitudeAt(planet, ms) {
  const t = (ms - J2000_MS) / CENTURY_MS;
  const mean = planet.l0 + planet.rate * t;
  const m = (mean - planet.peri) * RAD;
  const e = planet.e;
  const centre = (2 * e - (e ** 3) / 4) * Math.sin(m) + 1.25 * e * e * Math.sin(2 * m) + (13 / 12) * e ** 3 * Math.sin(3 * m);
  return (((mean + centre / RAD) % 360) + 360) % 360;
}

export function ringLayout() {
  return PLANETS.map((_, i) => {
    const rx = ORRERY.firstRx + i * ORRERY.gap;
    return { rx, ry: rx * ORRERY.tilt };
  });
}

const overlapArea = (a, b) => {
  const w = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
  const h = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
  return w > 0 && h > 0 ? w * h : 0;
};
const outside = (b) => Math.max(0, -b.x) + Math.max(0, b.x + b.w - ORRERY.width) + Math.max(0, -b.y) + Math.max(0, b.y + b.h - ORRERY.height);

// Each planet's dot position and the box its name takes. `measure(name)` gives the text
// width in px. Labels are placed inner to outer, trying right, left, above and below
// the dot (and the corners) until one touches no earlier label, no dot and no edge;
// when none is free the least bad one is used.
export function placeLabels({ ms, measure, fontPx = ORRERY.fontPx }) {
  const rings = ringLayout();
  const dots = PLANETS.map((p, i) => {
    const a = longitudeAt(p, ms) * RAD;
    return { name: p.name, size: p.size, x: ORRERY.cx + rings[i].rx * Math.cos(a), y: ORRERY.cy - rings[i].ry * Math.sin(a) };
  });
  const dotBoxes = dots.map((d) => ({ x: d.x - d.size - 1, y: d.y - d.size - 1, w: 2 * d.size + 2, h: 2 * d.size + 2 }));
  const h = fontPx;
  const placed = [];
  return dots.map((d, i) => {
    const w = measure(d.name);
    const gap = d.size + 5;
    const candidates = [
      { x: d.x + gap, y: d.y - h / 2 },
      { x: d.x - gap - w, y: d.y - h / 2 },
      { x: d.x - w / 2, y: d.y - gap - h },
      { x: d.x - w / 2, y: d.y + gap },
      { x: d.x + gap, y: d.y - gap - h },
      { x: d.x - gap - w, y: d.y - gap - h },
      { x: d.x + gap, y: d.y + gap },
      { x: d.x - gap - w, y: d.y + gap },
    ].map((c) => ({ ...c, w, h }));
    let best = null;
    let bestCost = Infinity;
    for (const c of candidates) {
      let cost = outside(c) * 1000;
      for (const o of placed) cost += overlapArea(c, o);
      dotBoxes.forEach((o, j) => {
        if (j !== i) cost += overlapArea(c, o);
      });
      if (cost < bestCost) {
        best = c;
        bestCost = cost;
        if (cost === 0) break;
      }
    }
    placed.push(best);
    return { ...d, label: best };
  });
}

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

// "November 24, 2026" from a Date, in the panel's local time.
export const dateLabel = (date) => `${MONTHS[date.getMonth()]} ${date.getDate()}, ${date.getFullYear()}`;
