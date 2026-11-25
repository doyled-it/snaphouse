// Pure rules for the Contours style: contour lines of a slowly changing noise field
// found by marching squares. The grid spans the canvas exactly. Every fourth line is
// heavier. At night every other line fades out, leaving fewer, dimmer lines.

import { fbm3 } from "./noise-math.js";

export const CONTOURS = Object.freeze({
  width: 400,
  height: 240,
  cell: 8,
  cols: 51,
  rows: 31,
  levels: Object.freeze([0.22, 0.3, 0.38, 0.46, 0.54, 0.62, 0.7, 0.78]),
  freq: 0.18, // noise frequency per grid cell
  timeRate: 0.015, // noise time units per second
  maxFps: 60,
});

export function fillField(values, cols, rows, t) {
  for (let j = 0; j < rows; j += 1) {
    for (let i = 0; i < cols; i += 1) {
      values[j * cols + i] = fbm3(i * CONTOURS.freq, j * CONTOURS.freq, t);
    }
  }
}

// How a line is drawn: `heavy` for every fourth, `weight` 0..1 (odd lines fade out
// as night comes in, even lines stay).
export function levelView(index, nightMix) {
  return {
    heavy: index % 4 === 0,
    weight: index % 2 === 0 ? 1 : 1 - nightMix,
  };
}

// Edges of a cell: 0 top, 1 right, 2 bottom, 3 left. Corner bits: top-left 8,
// top-right 4, bottom-right 2, bottom-left 1.
const SEGMENTS = [
  [], [[3, 2]], [[2, 1]], [[3, 1]], [[0, 1]], null, [[0, 2]], [[3, 0]],
  [[3, 0]], [[0, 2]], null, [[0, 1]], [[3, 1]], [[2, 1]], [[3, 2]], [],
];

// Append the segments of one level as flat x1,y1,x2,y2 numbers to `out` and return how
// many segments were added. The two saddle cases (5 and 10) join their high corners
// only when the cell's centre average is above the level.
export function marchLevel(values, cols, rows, level, cell, out) {
  let count = 0;
  for (let j = 0; j < rows - 1; j += 1) {
    for (let i = 0; i < cols - 1; i += 1) {
      const a = values[j * cols + i];
      const b = values[j * cols + i + 1];
      const c = values[(j + 1) * cols + i + 1];
      const d = values[(j + 1) * cols + i];
      const idx = (a > level ? 8 : 0) | (b > level ? 4 : 0) | (c > level ? 2 : 0) | (d > level ? 1 : 0);
      let segs = SEGMENTS[idx];
      if (segs === null) {
        const high = (a + b + c + d) / 4 > level;
        if (idx === 5) segs = high ? [[0, 3], [2, 1]] : [[0, 1], [3, 2]];
        else segs = high ? [[0, 1], [3, 2]] : [[0, 3], [2, 1]];
      }
      if (segs.length === 0) continue;
      const x0 = i * cell;
      const y0 = j * cell;
      for (let s = 0; s < segs.length; s += 1) {
        const e1 = segs[s][0];
        const e2 = segs[s][1];
        edgePoint(e1, x0, y0, cell, level, a, b, c, d, out);
        edgePoint(e2, x0, y0, cell, level, a, b, c, d, out);
        count += 1;
      }
    }
  }
  return count;
}

function edgePoint(e, x0, y0, s, L, a, b, c, d, out) {
  if (e === 0) out.push(x0 + (s * (L - a)) / (b - a), y0);
  else if (e === 1) out.push(x0 + s, y0 + (s * (L - b)) / (c - b));
  else if (e === 2) out.push(x0 + (s * (L - d)) / (c - d), y0 + s);
  else out.push(x0, y0 + (s * (L - a)) / (d - a));
}
