// Frame crop rules, pure: which box a picture shows, how it is labelled, how a
// drag moves or resizes it, and the request that saves it. rooms.js and
// crop-editor.js own the DOM. Design: docs/superpowers/specs/2026-11-25-frame-crop-design.md.
//
// A box is { x, y, w, h }: left, top, width and height as fractions of the
// UPRIGHT picture. Its shape in picture pixels is the frame's 5:3, so
// w * W / (h * H) = ASPECT. W and H passed below are the picture's RENDERED
// natural size (the loaded <img>), never the index's as-stored width and height,
// which can differ for a file that still carries an EXIF orientation tag.

export const ASPECT = 800 / 480;

// The smallest box covers this many pixels of the picture's width, as on the
// hub (framecrop.py MIN_BOX_PIXELS). A picture too small for that only allows
// the largest box that fits. tests/js/crop-model.test.js and
// tests/python/test_framecrop.py carry the same example table.
export const MIN_BOX_PIXELS = 400;

// One arrow press moves the box by this fraction of the picture; with Shift it
// changes the width by twice this.
const NUDGE = 0.01;

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

const isNumber = (v) => typeof v === "number" && Number.isFinite(v);

// A box from the index or the service, or null when it is not one.
export function parseBox(raw) {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const { x, y, w, h } = raw;
  if (![x, y, w, h].every(isNumber)) {
    return null;
  }
  const eps = 1e-6;
  if (x < 0 || y < 0 || w <= 0 || h <= 0 || x + w > 1 + eps || y + h > 1 + eps) {
    return null;
  }
  return { x, y, w, h };
}

// The box the frame shows for a piece: the saved crop, else the automatic box.
export function effectiveBox(piece) {
  if (!piece) {
    return null;
  }
  const crop = parseBox(piece.crop);
  if (crop) {
    return { box: crop, saved: true };
  }
  const auto = parseBox(piece.autoCrop);
  return auto ? { box: auto, saved: false } : null;
}

// What the overlay says. An uncropped non-Ente portrait is not on the frames:
// the rotation rule still leaves it out until it has a crop.
export function cropLabel(piece) {
  if (parseBox(piece && piece.crop)) {
    return { text: "Your crop", tone: "saved" };
  }
  if (piece && piece.landscape === false && piece.source !== "ente") {
    return { text: "Automatic · not on the frames until cropped", tone: "warn" };
  }
  return { text: "Automatic", tone: "auto" };
}

// ---- size rules --------------------------------------------------------------

// The widest box: the whole width, or less when the picture is too short for a
// 5:3 box that wide.
export function maxBoxWidth(W, H) {
  return Math.min(1, (H * ASPECT) / W);
}

export function minBoxWidth(W, H) {
  return Math.min(MIN_BOX_PIXELS / W, maxBoxWidth(W, H));
}

// The height that keeps the 5:3 shape for a width.
export function boxHeight(w, W, H) {
  return (w * W) / H / ASPECT;
}

// The width that goes with a height (the inverse of boxHeight).
function boxWidthForHeight(h, W, H) {
  return (h * H * ASPECT) / W;
}

// Fit a box to the rules: width in range, the 5:3 height, inside the picture.
export function clampBox(box, W, H) {
  const w = clamp(box.w, minBoxWidth(W, H), maxBoxWidth(W, H));
  const h = boxHeight(w, W, H);
  return { x: clamp(box.x, 0, 1 - w), y: clamp(box.y, 0, 1 - h), w, h };
}

// The box the editor opens with: what the frame shows now, fixed up to the
// rules, or the largest centred box for a piece with neither.
export function initialBox(piece, W, H) {
  const current = effectiveBox(piece);
  if (current) {
    return clampBox(current.box, W, H);
  }
  const w = maxBoxWidth(W, H);
  const h = boxHeight(w, W, H);
  return { x: (1 - w) / 2, y: (1 - h) / 2, w, h };
}

// ---- screen geometry -----------------------------------------------------------

// The box as a rectangle on the page, given where the picture is displayed
// ({ left, top, width, height }, from getBoundingClientRect).
export function boxRect(box, rect) {
  return {
    left: rect.left + box.x * rect.width,
    top: rect.top + box.y * rect.height,
    width: box.w * rect.width,
    height: box.h * rect.height,
  };
}

// A pointer position as fractions of the displayed picture (not clamped).
export function pointToFraction(clientX, clientY, rect) {
  return { x: (clientX - rect.left) / rect.width, y: (clientY - rect.top) / rect.height };
}

// ---- drags -----------------------------------------------------------------------

// Slide the box by a fraction of the picture, stopping at every edge.
export function moveBox(box, dx, dy, W, H) {
  return {
    x: clamp(box.x + dx, 0, 1 - box.w),
    y: clamp(box.y + dy, 0, 1 - box.h),
    w: box.w,
    h: box.h,
  };
}

// Drag one corner ("nw", "ne", "sw", "se") to `point` (fractions of the
// picture). The opposite corner stays where it is and the shape stays 5:3: the
// width follows whichever of the two pointer distances asks for more.
export function resizeBox(start, corner, point, W, H) {
  const west = corner.includes("w");
  const north = corner.includes("n");
  const sx = west ? -1 : 1;
  const sy = north ? -1 : 1;
  const ax = west ? start.x + start.w : start.x;
  const ay = north ? start.y + start.h : start.y;
  const pullX = Math.max(0, (point.x - ax) * sx);
  const pullY = Math.max(0, (point.y - ay) * sy);
  const wanted = Math.max(pullX, boxWidthForHeight(pullY, W, H));
  const roomX = sx > 0 ? 1 - ax : ax;
  const roomY = sy > 0 ? 1 - ay : ay;
  const upper = Math.min(maxBoxWidth(W, H), roomX, boxWidthForHeight(roomY, W, H));
  const w = Math.min(Math.max(wanted, minBoxWidth(W, H)), upper);
  const h = boxHeight(w, W, H);
  return {
    x: Math.max(0, sx > 0 ? ax : ax - w),
    y: Math.max(0, sy > 0 ? ay : ay - h),
    w,
    h,
  };
}

// Arrow keys: move the box by one percent of the picture; with Shift, Right
// and Up grow it and Left and Down shrink it about its centre. Null for any
// other key.
export function nudgeBox(box, key, shift, W, H) {
  const delta = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[key];
  if (!delta) {
    return null;
  }
  if (!shift) {
    return moveBox(box, delta[0] * NUDGE, delta[1] * NUDGE, W, H);
  }
  const grow = delta[0] > 0 || delta[1] < 0;
  const w = clamp(box.w + (grow ? 2 : -2) * NUDGE, minBoxWidth(W, H), maxBoxWidth(W, H));
  const h = boxHeight(w, W, H);
  return clampBox({ x: box.x + box.w / 2 - w / 2, y: box.y + box.h / 2 - h / 2, w, h }, W, H);
}

// ---- saving --------------------------------------------------------------------------

// The POST that saves a box, or removes the crop when `box` is null.
export function cropRequest(id, box) {
  const crop = box ? { x: box.x, y: box.y, w: box.w, h: box.h } : null;
  return { path: "/api/art/crop", body: { id, crop } };
}

export const SAVE_FAILED = "Couldn't save. Retry";

// The service's own short sentence, else the plain failure line.
export function cropErrorMessage(message) {
  return typeof message === "string" && message.trim() ? message : SAVE_FAILED;
}

// ---- thumbnails --------------------------------------------------------------------------

const trim = (n) => String(Number(n.toFixed(3)));

// CSS for a 5:3 tile that shows `box` of a thumbnail, as a background.
export function tileCropStyle(box) {
  const px = box.w >= 1 ? 0 : (box.x / (1 - box.w)) * 100;
  const py = box.h >= 1 ? 0 : (box.y / (1 - box.h)) * 100;
  return {
    size: `${trim(100 / box.w)}% ${trim(100 / box.h)}%`,
    position: `${trim(px)}% ${trim(py)}%`,
  };
}

// Which edge of the box the overlay label hangs from, so a long label on a
// small box on the right never runs off the picture.
export function labelAnchor(box) {
  return box.x + box.w / 2 > 0.5 ? "right" : "left";
}
