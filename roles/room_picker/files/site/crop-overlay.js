// The crop overlay drawn over a whole picture: an outline, a light dim outside
// it and a label. DOM only; the rules (which box, which label) are crop-model.js.
// The overlay layer sits inside a wrapper exactly as big as the picture, so the
// box is placed in percent of it and needs no measuring.

import { cropLabel, effectiveBox, labelAnchor } from "./crop-model.js";

const pct = (v) => `${(v * 100).toFixed(4)}%`;

// Place `boxEl` for a box (fractions of the picture).
export function placeBox(boxEl, box) {
  boxEl.style.left = pct(box.x);
  boxEl.style.top = pct(box.y);
  boxEl.style.width = pct(box.w);
  boxEl.style.height = pct(box.h);
}

// The layer's structure, for a page that builds its viewer in script (the
// wall panel). The page's own viewer has the same nodes in index.html.
export function buildCropOverlay(doc) {
  const layer = doc.createElement("div");
  layer.className = "viewer-crop";
  const box = doc.createElement("div");
  box.className = "vc-box";
  const label = doc.createElement("span");
  label.className = "vc-label";
  box.append(label);
  layer.append(box);
  layer.hidden = true;
  return { layer, box, label };
}

// Show the effective box of `piece` with its label, or hide the layer when the
// picture has neither a saved nor an automatic box yet.
export function paintCropOverlay({ layer, box, label }, piece) {
  const current = effectiveBox(piece);
  layer.hidden = !current;
  if (!current) {
    return;
  }
  placeBox(box, current.box);
  const text = cropLabel(piece);
  label.textContent = text.text;
  box.dataset.tone = text.tone;
  box.dataset.anchor = labelAnchor(current.box);
}
