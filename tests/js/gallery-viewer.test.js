import { test } from "node:test";
import assert from "node:assert/strict";

import {
  viewerState,
  stepIndex,
  swipeDirection,
  galleryHash,
  pieceIdFromHash,
  viewerSource,
  idAfterRemoval,
} from "../../roles/room_picker/files/site/gallery-viewer.js";

const items = (n) =>
  Array.from({ length: n }, (_, i) => ({ id: `p${i}`, title: `Piece ${i}`, landscape: true }));

test("position reads as 'N of M' and is one-based", () => {
  assert.equal(viewerState(items(41), 2, "grid").position, "3 of 41");
});

test("edge flags at the first and last piece", () => {
  const first = viewerState(items(3), 0, "grid");
  assert.equal(first.canPrev, false);
  assert.equal(first.canNext, true);
  const last = viewerState(items(3), 2, "grid");
  assert.equal(last.canPrev, true);
  assert.equal(last.canNext, false);
});

test("a single piece has neither neighbour", () => {
  const only = viewerState(items(1), 0, "grid");
  assert.equal(only.canPrev, false);
  assert.equal(only.canNext, false);
});

test("grid actions", () => {
  assert.deepEqual(viewerState(items(2), 0, "grid").actions, ["show", "pin", "move", "trash"]);
});

test("history adds removeFromHistory", () => {
  assert.deepEqual(viewerState(items(2), 0, "history").actions, [
    "show",
    "pin",
    "move",
    "trash",
    "removeFromHistory",
  ]);
});

test("showBlockedReason is set for a too-tall piece and null otherwise", () => {
  const list = [
    { id: "a", landscape: true },
    { id: "b", landscape: false },
  ];
  assert.equal(viewerState(list, 0, "grid").showBlockedReason, null);
  assert.match(viewerState(list, 1, "grid").showBlockedReason, /too tall/i);
});

test("an out of range index is clamped; an empty list is safe", () => {
  assert.equal(viewerState(items(3), 9, "grid").position, "3 of 3");
  const empty = viewerState([], 0, "grid");
  assert.equal(empty.position, "0 of 0");
  assert.equal(empty.canPrev, false);
  assert.equal(empty.canNext, false);
});

test("stepIndex clamps and does not wrap", () => {
  assert.equal(stepIndex(0, 5, -1), 0);
  assert.equal(stepIndex(4, 5, 1), 4);
  assert.equal(stepIndex(2, 5, 1), 3);
  assert.equal(stepIndex(2, 5, -1), 1);
  assert.equal(stepIndex(0, 0, 1), 0);
});

test("swipeDirection", () => {
  assert.equal(swipeDirection(60, 5), 1);
  assert.equal(swipeDirection(-60, 5), -1);
  assert.equal(swipeDirection(60, 80), 0);
  assert.equal(swipeDirection(10, 0), 0);
  assert.equal(swipeDirection(-48, 0), -1);
  assert.equal(swipeDirection(30, 0, 20), 1);
});

test("galleryHash and pieceIdFromHash round trip, folders included", () => {
  assert.equal(galleryHash("Trip/a b.jpg"), "#gallery/Trip%2Fa%20b.jpg");
  assert.equal(pieceIdFromHash("#gallery/Trip%2Fa%20b.jpg"), "Trip/a b.jpg");
  assert.equal(pieceIdFromHash("#gallery"), null);
  assert.equal(pieceIdFromHash("#art/x.jpg"), "x.jpg");
  assert.equal(pieceIdFromHash("#listen"), null);
  assert.equal(pieceIdFromHash("#gallery/%E0%A4%A"), null);
});

test("a deep-linked piece outside the filter steps through the whole library", () => {
  const all = items(6);
  const filtered = [all[0], all[1]];
  assert.equal(viewerSource(filtered, "p4"), "all");
  assert.equal(viewerSource(filtered, "p1"), "filter");
  // p4 (index 4 of all) is removed; the list after removal is all minus p4.
  const after = all.filter((p) => p.id !== "p4");
  assert.equal(idAfterRemoval(after, 4), "p5");
  assert.equal(idAfterRemoval(after.slice(0, 4), 4), "p3");
  assert.equal(idAfterRemoval([], 0), null);
});
