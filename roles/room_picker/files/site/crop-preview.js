// The crop editor's preview: the box cut to 800x480 and dithered to the
// frame's six colors, as the frame does it. Pure (typed arrays in and out);
// crop-editor.js draws it on a canvas.

// How each of the panel's six colors reads on the e-ink. Copied from
// nightstand's src/nightstand/palette.py (PANEL: black, white, red, yellow,
// green, blue); never imported. Retune here when that table is retuned.
export const PANEL_COLORS = [
  [43, 41, 42],
  [233, 231, 224],
  [173, 68, 60],
  [206, 188, 88],
  [74, 110, 75],
  [60, 70, 120],
];

// The real frame and the smaller size used while a handle is being dragged, so
// the pointer is never held up by the full dither.
export const PREVIEW_FULL = { w: 800, h: 480 };
export const PREVIEW_DRAG = { w: 200, h: 120 };

export function previewSize(dragging) {
  return dragging ? PREVIEW_DRAG : PREVIEW_FULL;
}

// The box in picture pixels, for drawImage's source rectangle.
export function sourceRect(box, W, H) {
  return { sx: box.x * W, sy: box.y * H, sw: box.w * W, sh: box.h * H };
}

function nearest(r, g, b) {
  let best = 0;
  let bestDist = Infinity;
  for (let i = 0; i < PANEL_COLORS.length; i += 1) {
    const c = PANEL_COLORS[i];
    const dr = r - c[0];
    const dg = g - c[1];
    const db = b - c[2];
    const dist = dr * dr + dg * dg + db * db;
    if (dist < bestDist) {
      bestDist = dist;
      best = i;
    }
  }
  return best;
}

// Floyd-Steinberg against the six panel colors, left to right, top to bottom.
// `rgba` is w * h * 4 bytes and is not changed; the result is a new opaque
// RGBA array of panel colors.
export function ditherToPanel(rgba, w, h) {
  const work = new Float32Array(w * h * 3);
  for (let i = 0, j = 0; i < w * h * 4; i += 4, j += 3) {
    work[j] = rgba[i];
    work[j + 1] = rgba[i + 1];
    work[j + 2] = rgba[i + 2];
  }
  const out = new Uint8ClampedArray(w * h * 4);
  const spread = (x, y, k, er, eg, eb) => {
    if (x < 0 || x >= w || y >= h) return;
    const j = (y * w + x) * 3;
    work[j] += er * k;
    work[j + 1] += eg * k;
    work[j + 2] += eb * k;
  };
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const j = (y * w + x) * 3;
      const r = Math.min(255, Math.max(0, work[j]));
      const g = Math.min(255, Math.max(0, work[j + 1]));
      const b = Math.min(255, Math.max(0, work[j + 2]));
      const c = PANEL_COLORS[nearest(r, g, b)];
      const o = (y * w + x) * 4;
      out[o] = c[0];
      out[o + 1] = c[1];
      out[o + 2] = c[2];
      out[o + 3] = 255;
      const er = r - c[0];
      const eg = g - c[1];
      const eb = b - c[2];
      spread(x + 1, y, 7 / 16, er, eg, eb);
      spread(x - 1, y + 1, 3 / 16, er, eg, eb);
      spread(x, y + 1, 5 / 16, er, eg, eb);
      spread(x + 1, y + 1, 1 / 16, er, eg, eb);
    }
  }
  return out;
}
