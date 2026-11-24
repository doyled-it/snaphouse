// Pure rules for the Moon style: the phase from the mean synodic month, the lit
// fraction, the phase name and days to the next full or new moon, the terminator and
// the per-pixel shading that turns a full-moon photo into tonight's phase, and the slow
// drift of the whole composition across the screen.
//
// The phase is the MEAN lunation, counted from the new moon of 2000-01-06 18:14 UTC.
// The real moon runs up to about 14 hours ahead or behind the mean, so the phase can
// be off by about half a day (checked against published new and full moons in the
// tests, all within a day). Libration, the real tilt of the terminator, and rise and
// set are out of scope. Waxing is lit on the right and waning on the left, as seen from
// the northern hemisphere.

export const SYNODIC_DAYS = 29.530588853;
export const NEW_MOON_EPOCH_MS = Date.UTC(2000, 0, 6, 18, 14);
const DAY_MS = 86400000;

export const MOON = Object.freeze({
  width: 800,
  height: 480,
  radius: 150, // the moon on screen, in px
  photoPx: 304, // the photo is drawn this wide; its disc fills 0.99 of it
  discFraction: 0.495, // disc radius over photo width in moon.jpg
  earthshine: 0.07, // how much of the photo the dark side keeps
  soft: 0.15, // half width of the terminator's softness, in n.s units
  fps: 2,
  driftX: { mid: 262, amp: 30, periodS: 900 },
  driftY: { mid: 240, amp: 18, periodS: 640 },
  textGap: 55,
});

// Phase [0, 1): 0 new, 0.25 first quarter, 0.5 full, 0.75 last quarter.
export function phaseAt(ms) {
  const age = ((((ms - NEW_MOON_EPOCH_MS) / DAY_MS) % SYNODIC_DAYS) + SYNODIC_DAYS) % SYNODIC_DAYS;
  return age / SYNODIC_DAYS;
}

export const litFraction = (phase) => (1 - Math.cos(phase * 2 * Math.PI)) / 2;

const NAMES = ["New moon", "Waxing crescent", "First quarter", "Waxing gibbous", "Full moon", "Waning gibbous", "Last quarter", "Waning crescent"];
export const phaseName = (phase) => NAMES[Math.round(phase * 8) % 8];

// The nearer of the next full and next new moon: { kind: "full" | "new", days }.
export function nextEvent(phase) {
  const toFull = ((0.5 - phase + 1) % 1) * SYNODIC_DAYS;
  const toNew = ((1 - phase) % 1) * SYNODIC_DAYS;
  return toFull < toNew ? { kind: "full", days: toFull } : { kind: "new", days: toNew };
}

export function nextText({ kind, days }) {
  const what = kind === "full" ? "Full moon" : "New moon";
  const n = Math.round(days);
  if (n <= 0) return `${what} today`;
  if (n === 1) return `${what} tomorrow`;
  return `${what} in ${n} days`;
}

// The three lines beside the moon.
export function textLines(ms) {
  const phase = phaseAt(ms);
  return { name: phaseName(phase), lit: `${Math.round(litFraction(phase) * 100)}% lit`, next: nextText(nextEvent(phase)) };
}

// Where the terminator crosses the row v of the unit disc (u to the right, v down).
// An ellipse that meets the poles; the lit side is right while waxing, left while waning.
export function terminatorU(v, phase) {
  const k = Math.cos(phase * 2 * Math.PI) * Math.sqrt(Math.max(0, 1 - v * v));
  return phase < 0.5 ? k : -k;
}

const smoothstep = (a, b, x) => {
  const t = Math.min(1, Math.max(0, (x - a) / (b - a)));
  return t * t * (3 - 2 * t);
};

// How much of the full-moon photo shows at a point (u, v) of the unit disc: the
// sun's direction lit against the sphere's normal, through a smoothstep for the soft
// terminator, never below the earthshine.
export function shadeAt(u, v, phase) {
  const alpha = phase * 2 * Math.PI;
  const w = Math.sqrt(Math.max(0, 1 - u * u - v * v));
  const d = u * Math.sin(alpha) - w * Math.cos(alpha);
  return MOON.earthshine + (1 - MOON.earthshine) * smoothstep(-MOON.soft, MOON.soft, d);
}

// The composition's centre and the text block's left edge and middle line, at a clock
// time in ms. Two slow sines of different periods, so the picture never rests.
export function driftAt(ms) {
  const t = ms / 1000;
  const cx = MOON.driftX.mid + MOON.driftX.amp * Math.sin((2 * Math.PI * t) / MOON.driftX.periodS);
  const cy = MOON.driftY.mid + MOON.driftY.amp * Math.sin((2 * Math.PI * t) / MOON.driftY.periodS);
  return { cx, cy, textX: cx + MOON.radius + MOON.textGap, textY: cy };
}
