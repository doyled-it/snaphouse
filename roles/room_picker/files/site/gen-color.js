// Color helpers shared by the generative styles. Colors come from the album hues the
// page derives from the cover (the same two hues and chroma Aurora uses), sampled
// into a short table so the hot loops never convert a color per thread or per cell.

import { tintColor } from "./aurora-math.js";

export const lerp = (a, b, t) => a + (b - a) * t;

// `bins` colors from the first hue (k = 0) to the second (k = 1) at lightness L.
export function buildColors(hues, bins, L) {
  const out = [];
  for (let i = 0; i < bins; i += 1) out.push(tintColor(hues, L, bins === 1 ? 0 : i / (bins - 1)));
  return out;
}

export const rgba = (c, a) => `rgba(${c[0]},${c[1]},${c[2]},${a.toFixed(3)})`;
