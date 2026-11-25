import { test } from "node:test";
import assert from "node:assert/strict";

import {
  ASPECT,
  boxHeight,
  boxRect,
  clampBox,
  cropErrorMessage,
  cropLabel,
  cropRequest,
  effectiveBox,
  initialBox,
  labelAnchor,
  maxBoxWidth,
  minBoxWidth,
  moveBox,
  nudgeBox,
  parseBox,
  pointToFraction,
  resizeBox,
  tileCropStyle,
} from "../../roles/room_picker/files/site/crop-model.js";

const near = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) <= eps, `${a} is not near ${b}`);

// A box obeys the frame shape in picture pixels: w*W / (h*H) = 800/480.
const shapeOk = (box, W, H) => near((box.w * W) / (box.h * H), ASPECT, 1e-9);
const insideOk = (box) => {
  assert.ok(box.x >= -1e-9 && box.y >= -1e-9, "starts inside");
  assert.ok(box.x + box.w <= 1 + 1e-9 && box.y + box.h <= 1 + 1e-9, "ends inside");
};

// ---- the minimum size pins to the hub's validate_crop (tests/python/test_framecrop.py
// carries the same table) ---------------------------------------------------

const MIN_TABLE = [
  // [W, H, minWidth, maxWidth]
  [2000, 1500, 0.2, 1],
  [1500, 2000, 400 / 1500, 1],
  [300, 800, 1, 1],
  [400, 200, 1 / 1.2, 1 / 1.2],
  [4032, 3024, 400 / 4032, 1],
];

test("minimum and maximum box width match the hub's rule on example pictures", () => {
  for (const [W, H, min, max] of MIN_TABLE) {
    near(minBoxWidth(W, H), min);
    near(maxBoxWidth(W, H), max);
  }
});

test("a picture too small for 400 px only allows the largest box that fits", () => {
  // 300 px wide: the whole width. 400x200: the full height (333 px wide).
  assert.equal(minBoxWidth(300, 800), maxBoxWidth(300, 800));
  assert.equal(minBoxWidth(400, 200), maxBoxWidth(400, 200));
});

// ---- geometry ----------------------------------------------------------

test("boxHeight keeps the 5:3 shape in picture pixels", () => {
  near(boxHeight(0.6, 4000, 3000), 0.6 * (4000 / 3000) * 0.6);
  near(boxHeight(1, 1000, 1000), 0.6);
});

test("boxRect maps a box onto the displayed image rect at several aspect ratios", () => {
  const box = { x: 0.1, y: 0.2, w: 0.5, h: 0.3 };
  // landscape, portrait and square displays; the rect is where the image sits.
  for (const rect of [
    { left: 20, top: 30, width: 600, height: 400 },
    { left: 100, top: 10, width: 240, height: 320 },
    { left: 0, top: 0, width: 300, height: 300 },
  ]) {
    const r = boxRect(box, rect);
    near(r.left, rect.left + 0.1 * rect.width);
    near(r.top, rect.top + 0.2 * rect.height);
    near(r.width, 0.5 * rect.width);
    near(r.height, 0.3 * rect.height);
  }
});

test("pointToFraction is relative to the image rect and not clamped", () => {
  const rect = { left: 100, top: 50, width: 400, height: 200 };
  assert.deepEqual(pointToFraction(300, 150, rect), { x: 0.5, y: 0.5 });
  assert.deepEqual(pointToFraction(0, 50, rect), { x: -0.25, y: 0 });
});

test("effectiveBox prefers the saved crop, else the automatic one, else null", () => {
  const crop = { x: 0.1, y: 0.1, w: 0.5, h: 0.3 };
  const auto = { x: 0, y: 0, w: 1, h: 0.8 };
  assert.deepEqual(effectiveBox({ crop, autoCrop: auto }), { box: crop, saved: true });
  assert.deepEqual(effectiveBox({ crop: null, autoCrop: auto }), { box: auto, saved: false });
  assert.equal(effectiveBox({ crop: null, autoCrop: null }), null);
  assert.equal(effectiveBox(null), null);
});

test("parseBox accepts a contract box and rejects anything else", () => {
  assert.deepEqual(parseBox({ x: 0, y: 0.1, w: 0.5, h: 0.3 }), { x: 0, y: 0.1, w: 0.5, h: 0.3 });
  for (const bad of [
    null,
    undefined,
    "box",
    {},
    { x: 0, y: 0, w: 0.5 },
    { x: "0", y: 0, w: 0.5, h: 0.3 },
    { x: 0, y: 0, w: NaN, h: 0.3 },
    { x: -0.1, y: 0, w: 0.5, h: 0.3 },
    { x: 0.6, y: 0, w: 0.5, h: 0.3 },
    { x: 0, y: 0.8, w: 0.5, h: 0.3 },
    { x: 0, y: 0, w: 0, h: 0.3 },
    { x: true, y: 0, w: 0.5, h: 0.3 },
  ]) {
    assert.equal(parseBox(bad), null, JSON.stringify(bad));
  }
});

// ---- the four labels -------------------------------------------------------

test("label: a saved crop reads 'Your crop'", () => {
  const label = cropLabel({ crop: { x: 0, y: 0, w: 1, h: 0.6 }, autoCrop: null, landscape: true });
  assert.deepEqual(label, { text: "Your crop", tone: "saved" });
});

test("label: a landscape piece without a crop reads 'Automatic'", () => {
  const label = cropLabel({ crop: null, autoCrop: { x: 0, y: 0, w: 1, h: 1 }, landscape: true });
  assert.deepEqual(label, { text: "Automatic", tone: "auto" });
});

test("label: an uncropped non-Ente portrait is not on the frames until cropped", () => {
  const label = cropLabel({ crop: null, autoCrop: { x: 0, y: 0, w: 1, h: 0.4 }, landscape: false });
  assert.deepEqual(label, { text: "Automatic · not on the frames until cropped", tone: "warn" });
});

test("label: an Ente portrait without a crop is plain Automatic (it rotates anyway)", () => {
  const label = cropLabel({ crop: null, autoCrop: { x: 0, y: 0, w: 1, h: 0.4 }, landscape: false, source: "ente" });
  assert.deepEqual(label, { text: "Automatic", tone: "auto" });
});

test("label: a saved crop on a portrait is still 'Your crop'", () => {
  const label = cropLabel({ crop: { x: 0, y: 0.2, w: 1, h: 0.4 }, landscape: true });
  assert.equal(label.text, "Your crop");
});

// ---- clamping, minimum size ---------------------------------------------------

test("clampBox keeps the box inside, at least the minimum, at most the maximum, and 5:3", () => {
  const W = 4000;
  const H = 3000;
  const huge = clampBox({ x: 0.9, y: 0.9, w: 3, h: 3 }, W, H);
  near(huge.w, 1);
  assert.equal(huge.x, 0);
  insideOk(huge);
  shapeOk(huge, W, H);
  const tiny = clampBox({ x: 0.5, y: 0.5, w: 0.001, h: 0.001 }, W, H);
  near(tiny.w, 400 / W);
  shapeOk(tiny, W, H);
  insideOk(tiny);
});

test("clampBox on a portrait picture caps the width so the height still fits", () => {
  const W = 3000;
  const H = 4000;
  const box = clampBox({ x: 0, y: 0, w: 1, h: 1 }, W, H);
  near(box.w, 1);
  near(box.h, (W / H) * 0.6);
  insideOk(box);
  const wide = clampBox({ x: 0, y: 0, w: 1, h: 1 }, 4000, 1000);
  near(wide.h, 1);
  near(wide.w, 1000 * ASPECT / 4000);
});

test("initialBox is the effective box fixed up, else the largest centred box", () => {
  const W = 3000;
  const H = 4000;
  const centred = initialBox({ crop: null, autoCrop: null }, W, H);
  shapeOk(centred, W, H);
  insideOk(centred);
  near(centred.x + centred.w / 2, 0.5);
  near(centred.y + centred.h / 2, 0.5);
  const fromAuto = initialBox({ crop: null, autoCrop: { x: 0, y: 0.1, w: 0.9921, h: 0.5 } }, 4000, 3000);
  shapeOk(fromAuto, 4000, 3000);
  insideOk(fromAuto);
});

// ---- move and resize while dragging ------------------------------------------

test("moveBox slides the box and stops at every edge", () => {
  const W = 4000;
  const H = 3000;
  const start = { x: 0.2, y: 0.2, w: 0.5, h: boxHeight(0.5, W, H) };
  const moved = moveBox(start, 0.1, 0.05, W, H);
  near(moved.x, 0.3);
  near(moved.y, 0.25);
  assert.equal(moved.w, start.w);
  near(moved.h, start.h);
  const left = moveBox(start, -5, -5, W, H);
  assert.equal(left.x, 0);
  assert.equal(left.y, 0);
  const right = moveBox(start, 5, 5, W, H);
  near(right.x, 1 - start.w);
  near(right.y, 1 - start.h);
});

const CORNERS = ["nw", "ne", "sw", "se"];
const anchorOf = (box, corner) => ({
  x: corner.includes("w") ? box.x + box.w : box.x,
  y: corner.includes("n") ? box.y + box.h : box.y,
});

test("resizeBox from every corner keeps the opposite corner fixed and the shape 5:3", () => {
  const W = 4000;
  const H = 3000;
  const start = { x: 0.3, y: 0.3, w: 0.4, h: boxHeight(0.4, W, H) };
  for (const corner of CORNERS) {
    const anchor = anchorOf(start, corner);
    // Drag the corner outward from the anchor by a bit.
    const out = {
      x: corner.includes("w") ? start.x - 0.05 : start.x + start.w + 0.05,
      y: corner.includes("n") ? start.y - 0.05 : start.y + start.h + 0.05,
    };
    const next = resizeBox(start, corner, out, W, H);
    shapeOk(next, W, H);
    insideOk(next);
    assert.ok(next.w > start.w, `${corner} grows`);
    const anchorAfter = anchorOf(next, corner);
    near(anchorAfter.x, anchor.x);
    near(anchorAfter.y, anchor.y);
  }
});

test("resizeBox shrinks toward the anchor and stops at the minimum size", () => {
  const W = 4000;
  const H = 3000;
  const start = { x: 0.2, y: 0.2, w: 0.5, h: boxHeight(0.5, W, H) };
  for (const corner of CORNERS) {
    const anchor = anchorOf(start, corner);
    const next = resizeBox(start, corner, anchor, W, H);
    near(next.w, minBoxWidth(W, H));
    shapeOk(next, W, H);
    const anchorAfter = anchorOf(next, corner);
    near(anchorAfter.x, anchor.x);
    near(anchorAfter.y, anchor.y);
  }
});

test("resizeBox cannot leave the picture whichever corner is dragged", () => {
  const W = 3000;
  const H = 4000;
  const start = { x: 0.1, y: 0.4, w: 0.5, h: boxHeight(0.5, W, H) };
  for (const corner of CORNERS) {
    const far = { x: corner.includes("w") ? -3 : 4, y: corner.includes("n") ? -3 : 4 };
    const next = resizeBox(start, corner, far, W, H);
    insideOk(next);
    shapeOk(next, W, H);
    assert.ok(next.w <= maxBoxWidth(W, H) + 1e-9);
  }
});

test("resizeBox follows the larger of the two pointer distances (aspect lock)", () => {
  const W = 4000;
  const H = 3000;
  const start = { x: 0.2, y: 0.2, w: 0.4, h: boxHeight(0.4, W, H) };
  // se corner dragged mostly sideways, then mostly down: the same width comes from the larger pull.
  const sideways = resizeBox(start, "se", { x: 0.8, y: start.y + start.h }, W, H);
  near(sideways.w, 0.6);
  const down = resizeBox(start, "se", { x: start.x + start.w, y: 0.2 + boxHeight(0.6, W, H) }, W, H);
  near(down.w, 0.6);
});

// ---- keyboard --------------------------------------------------------------

test("nudgeBox: arrows move by one percent of the picture, shift resizes about the centre", () => {
  const W = 4000;
  const H = 3000;
  const start = { x: 0.3, y: 0.3, w: 0.4, h: boxHeight(0.4, W, H) };
  near(nudgeBox(start, "ArrowRight", false, W, H).x, 0.31);
  near(nudgeBox(start, "ArrowLeft", false, W, H).x, 0.29);
  near(nudgeBox(start, "ArrowDown", false, W, H).y, 0.31);
  near(nudgeBox(start, "ArrowUp", false, W, H).y, 0.29);
  const bigger = nudgeBox(start, "ArrowRight", true, W, H);
  assert.ok(bigger.w > start.w);
  near(bigger.x + bigger.w / 2, start.x + start.w / 2);
  const smaller = nudgeBox(start, "ArrowLeft", true, W, H);
  assert.ok(smaller.w < start.w);
  shapeOk(bigger, W, H);
  shapeOk(smaller, W, H);
  assert.equal(nudgeBox(start, "a", false, W, H), null);
});

test("nudgeBox stops at the edges and at the minimum", () => {
  const W = 4000;
  const H = 3000;
  const corner = { x: 0, y: 0, w: 0.4, h: boxHeight(0.4, W, H) };
  assert.equal(nudgeBox(corner, "ArrowLeft", false, W, H).x, 0);
  const small = { x: 0.4, y: 0.4, w: minBoxWidth(W, H), h: boxHeight(minBoxWidth(W, H), W, H) };
  near(nudgeBox(small, "ArrowLeft", true, W, H).w, small.w);
});

// ---- requests and errors -----------------------------------------------------

test("cropRequest builds the POST body for a box and for Automatic", () => {
  const box = { x: 0.1, y: 0.2, w: 0.5, h: 0.3 };
  assert.deepEqual(cropRequest("Photos/a.jpg", box), {
    path: "/api/art/crop",
    body: { id: "Photos/a.jpg", crop: box },
  });
  assert.deepEqual(cropRequest("Photos/a.jpg", null).body, { id: "Photos/a.jpg", crop: null });
});

test("cropRequest sends only the four numbers", () => {
  const body = cropRequest("a.jpg", { x: 0.1, y: 0.2, w: 0.5, h: 0.3, extra: 1 }).body;
  assert.deepEqual(Object.keys(body.crop), ["x", "y", "w", "h"]);
});

test("cropErrorMessage shows the service's own sentence, else a plain fallback", () => {
  assert.equal(cropErrorMessage("That crop is too small."), "That crop is too small.");
  assert.equal(cropErrorMessage(""), "Couldn't save. Retry");
  assert.equal(cropErrorMessage(undefined), "Couldn't save. Retry");
});

// ---- thumbnails ---------------------------------------------------------------

test("tileCropStyle shows the box over a thumbnail with background size and position", () => {
  const style = tileCropStyle({ x: 0.25, y: 0.1, w: 0.5, h: 0.4 });
  assert.equal(style.size, "200% 250%");
  // x: 0.25 / (1 - 0.5) = 50%, y: 0.1 / (1 - 0.4) = 16.667%
  assert.equal(style.position, "50% 16.667%");
});

test("tileCropStyle on the whole picture needs no offset", () => {
  const style = tileCropStyle({ x: 0, y: 0, w: 1, h: 1 });
  assert.equal(style.size, "100% 100%");
  assert.equal(style.position, "0% 0%");
});

// ---- label placement ------------------------------------------------------------

test("the label sits on the box's left edge, or its right edge when the box is on the right half", () => {
  assert.equal(labelAnchor({ x: 0, y: 0, w: 1, h: 0.6 }), "left");
  assert.equal(labelAnchor({ x: 0.1, y: 0, w: 0.3, h: 0.2 }), "left");
  assert.equal(labelAnchor({ x: 0.6, y: 0, w: 0.3, h: 0.2 }), "right");
});
