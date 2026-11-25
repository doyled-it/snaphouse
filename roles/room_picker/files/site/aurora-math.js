// Pure math for the panel screensaver's Aurora style, ported from the owner's
// spike. No DOM and no canvas: colors, hue mixing and the per-column ribbon
// curves live here so they can be tested; aurora.js only draws them.

import { DEFAULT_HUES, chromaScale } from "./cover-color.js";

export const RIBBONS = Object.freeze([
  { top: 0.4, k: 0.0, a: [0.027, 0.011, 0.05], b: [0.071, 0.059], p: [0.5, 2.1, 4.2, 1.0] },
  { top: 0.48, k: 0.5, a: [0.021, 0.013, 0.04], b: [0.083, 0.047], p: [3.4, 0.3, 1.6, 5.2] },
  { top: 0.56, k: 1.0, a: [0.033, 0.009, 0.06], b: [0.061, 0.067], p: [5.9, 4.4, 2.9, 0.7] },
]);

const encode = (x) => {
  const v = x <= 0 ? 0 : x >= 1 ? 1 : x;
  const s = v <= 0.0031308 ? 12.92 * v : 1.055 * Math.pow(v, 1 / 2.4) - 0.055;
  return Math.round(s * 255);
};

export function oklchToRgb(L, C, hDeg) {
  const a = C * Math.cos((hDeg * Math.PI) / 180);
  const b = C * Math.sin((hDeg * Math.PI) / 180);
  const l_ = L + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = L - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = L - 0.0894841775 * a - 1.291485548 * b;
  const l = l_ * l_ * l_;
  const m = m_ * m_ * m_;
  const s = s_ * s_ * s_;
  return [
    encode(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
    encode(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
    encode(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s),
  ];
}

export function shortHueDiff(a, b) {
  return ((b - a + 540) % 360) - 180;
}

export function smoothstep(x) {
  return x <= 0 ? 0 : x >= 1 ? 1 : x * x * (3 - 2 * x);
}

const num = (v, fallback) => (Number.isFinite(v) ? v : fallback);

function cleanHues(h) {
  const src = h || {};
  return {
    h1: num(src.h1, DEFAULT_HUES.h1),
    h2: num(src.h2, DEFAULT_HUES.h2),
    chroma: Math.min(1, Math.max(0, num(src.chroma, DEFAULT_HUES.chroma))),
  };
}

export function mixHues(from, to, f) {
  const a = cleanHues(from);
  const b = cleanHues(to);
  return {
    h1: a.h1 + shortHueDiff(a.h1, b.h1) * f,
    h2: a.h2 + shortHueDiff(a.h2, b.h2) * f,
    chroma: a.chroma + (b.chroma - a.chroma) * f,
  };
}

// Color at position k (0..1) between the two hues, for the screensaver styles. It is
// richer than the page tokens: chroma 0.19 with a floor of 0.8 on chromaScale (so a grey
// or missing cover still gives a visible colour), and the two hues are spread at least
// MIN_SPREAD degrees apart. The spread keeps the shorter way from h1 to h2; equal hues
// spread upward (toward h1 + 110). A wider spread is left as it is.
export const MIN_SPREAD = 110;
const TINT_CHROMA = 0.19;
const TINT_FLOOR = 0.8;

export function tintColor(hues, L, k) {
  const h = cleanHues(hues);
  const d = shortHueDiff(h.h1, h.h2);
  const spread = Math.abs(d) < MIN_SPREAD ? (d < 0 ? -MIN_SPREAD : MIN_SPREAD) : d;
  return oklchToRgb(L, TINT_CHROMA * Math.max(TINT_FLOOR, chromaScale(h.chroma)), h.h1 + spread * k);
}

export function ribbonColumn(ribbon, x, t, H) {
  const off =
    H *
    (0.09 * Math.sin(x * 0.03 + t * ribbon.a[0] * 4 + ribbon.p[0]) +
      0.05 * Math.sin(x * 0.071 - t * ribbon.a[1] * 4 + ribbon.p[1]));
  let br =
    0.55 +
    0.45 *
      Math.sin(x * ribbon.b[0] + t * ribbon.a[2] * 2 + ribbon.p[2]) *
      Math.sin(x * ribbon.b[1] * 0.4 - t * 0.05 + ribbon.p[3]);
  br = br < 0.12 ? 0.12 : br;
  return { y: H * ribbon.top + off, alpha: br * 0.85 };
}

export function ribbonStops(color) {
  const rgba = (a) => `rgba(${color[0]},${color[1]},${color[2]},${a})`;
  return [
    [0, rgba(0)],
    [0.55, rgba(0.22)],
    [0.85, rgba(0.7)],
    [1, rgba(0)],
  ];
}
