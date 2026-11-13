// Page color from the now-playing cover. The pure part (pixels to hues) has no
// DOM; the DOM is touched only inside loadCoverHues and swapImage, through
// injected factories that default to Image and a canvas element.

export const DEFAULT_HUES = Object.freeze({ h1: 255, h2: 285, chroma: 0 });

const SIZE = 32;
const BINS = 36;
const BIN_DEG = 360 / BINS;
const MIN_ALPHA = 128;
const MIN_L = 0.2;
const MAX_L = 0.92;
const MIN_CHROMA = 0.04;
const MIN_KEPT = 0.05;
const MIN_KEPT_PIXELS = 4;
const CHROMA_FULL = 0.12;
const SECOND_MIN_SEPARATION = 40;
const SECOND_MIN_WEIGHT = 0.25;

function toLinear(c) {
  const v = c / 255;
  return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
}

function toOklab(r, g, b) {
  const lr = toLinear(r);
  const lg = toLinear(g);
  const lb = toLinear(b);
  const l = Math.cbrt(0.4122214708 * lr + 0.5363325363 * lg + 0.0514459929 * lb);
  const m = Math.cbrt(0.2119034982 * lr + 0.6806995451 * lg + 0.1073969566 * lb);
  const s = Math.cbrt(0.0883024619 * lr + 0.2817188376 * lg + 0.6299787005 * lb);
  return {
    L: 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    a: 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    b: 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  };
}

function angleDistance(a, b) {
  const d = Math.abs(a - b) % 360;
  return Math.min(d, 360 - d);
}

export function extractHues(rgba) {
  const pixels = Math.floor((rgba ? rgba.length : 0) / 4);
  if (pixels < 1) return { ...DEFAULT_HUES };
  const weight = new Array(BINS).fill(0);
  const sinSum = new Array(BINS).fill(0);
  const cosSum = new Array(BINS).fill(0);
  let kept = 0;
  let chromaTotal = 0;
  for (let i = 0; i < pixels; i += 1) {
    const o = i * 4;
    if (rgba[o + 3] < MIN_ALPHA) continue;
    const { L, a, b } = toOklab(rgba[o], rgba[o + 1], rgba[o + 2]);
    const c = Math.hypot(a, b);
    if (L < MIN_L || L > MAX_L || c < MIN_CHROMA) continue;
    const rad = Math.atan2(b, a);
    const deg = ((rad * 180) / Math.PI + 360) % 360;
    const bin = Math.min(BINS - 1, Math.floor(deg / BIN_DEG));
    weight[bin] += c;
    sinSum[bin] += Math.sin(rad) * c;
    cosSum[bin] += Math.cos(rad) * c;
    kept += 1;
    chromaTotal += c;
  }
  if (kept < MIN_KEPT_PIXELS || kept / pixels < MIN_KEPT) return { ...DEFAULT_HUES };

  let top = 0;
  for (let i = 1; i < BINS; i += 1) if (weight[i] > weight[top]) top = i;
  const meanHue = (center) => {
    let s = 0;
    let c = 0;
    for (const d of [-1, 0, 1]) {
      const k = (center + d + BINS) % BINS;
      s += sinSum[k];
      c += cosSum[k];
    }
    return ((Math.atan2(s, c) * 180) / Math.PI + 360) % 360;
  };
  const h1 = meanHue(top);

  let second = -1;
  for (let i = 0; i < BINS; i += 1) {
    if (weight[i] < weight[top] * SECOND_MIN_WEIGHT) continue;
    if (angleDistance(i * BIN_DEG + BIN_DEG / 2, h1) < SECOND_MIN_SEPARATION) continue;
    if (second < 0 || weight[i] > weight[second]) second = i;
  }
  const h2 = second >= 0 ? meanHue(second) : (h1 + 30) % 360;
  const chroma = Math.min(1, chromaTotal / kept / CHROMA_FULL);
  return { h1, h2, chroma };
}

export function chromaScale(chroma) {
  return 0.15 + 0.85 * chroma;
}

export function applyCoverColor(root, hues) {
  const next = {
    "--h": String(Math.round(hues.h1 * 10) / 10),
    "--h2": String(Math.round(hues.h2 * 10) / 10),
    "--k": String(Math.round(chromaScale(hues.chroma) * 100) / 100),
  };
  const last = root.__coverColor;
  if (last && last["--h"] === next["--h"] && last["--h2"] === next["--h2"] && last["--k"] === next["--k"]) {
    return;
  }
  for (const [name, value] of Object.entries(next)) root.style.setProperty(name, value);
  root.__coverColor = next;
}

const defaultImage = () => new Image();
const defaultCanvas = () => document.createElement("canvas");

export function loadCoverHues(src, { makeImage = defaultImage, makeCanvas = defaultCanvas } = {}) {
  return new Promise((resolve) => {
    try {
      const img = makeImage();
      img.onload = () => {
        try {
          const canvas = makeCanvas();
          canvas.width = SIZE;
          canvas.height = SIZE;
          const ctx = canvas.getContext("2d", { willReadFrequently: true });
          ctx.drawImage(img, 0, 0, SIZE, SIZE);
          resolve(extractHues(ctx.getImageData(0, 0, SIZE, SIZE).data));
        } catch (err) {
          resolve({ ...DEFAULT_HUES });
        }
      };
      img.onerror = () => resolve({ ...DEFAULT_HUES });
      img.src = src;
    } catch (err) {
      resolve({ ...DEFAULT_HUES });
    }
  });
}

// Preload then swap, so the visible image never blanks. The latest request per
// element wins when several overlap.
const latest = new WeakMap();

export async function swapImage(img, src, { makeImage = defaultImage } = {}) {
  latest.set(img, src);
  try {
    const loader = makeImage();
    loader.src = src;
    await loader.decode();
  } catch (err) {
    // Still show it; the element's own error handling takes over.
  }
  if (latest.get(img) === src) img.src = src;
}
