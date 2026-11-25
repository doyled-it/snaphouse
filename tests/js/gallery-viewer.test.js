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
  historyViewerItem,
  viewerControls,
  frameButtons,
  framePickerView,
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

test("the history viewer offers removeFromHistory and the grid does not", () => {
  assert.ok(viewerState(items(3), 1, "history").actions.includes("removeFromHistory"));
  assert.ok(!viewerState(items(3), 1, "grid").actions.includes("removeFromHistory"));
});

test("history items step and clamp like grid items", () => {
  const v = viewerState(items(3), 2, "history");
  assert.equal(v.position, "3 of 3");
  assert.equal(v.canNext, false);
  assert.equal(v.canPrev, true);
});

test("a tall ente piece is not blocked from showing on the frame", () => {
  const list = [{ id: "ente-a/f.jpg", landscape: false, source: "ente" }];
  assert.equal(viewerState(list, 0, "grid").showBlockedReason, null);
});

test("the history viewer hides Move and Move to trash for an ente piece", () => {
  const known = { id: "ente-a/f.jpg", folder: "ente-a", url: "/art/ente-a/f.jpg", source: "ente" };
  const item = historyViewerItem({
    piece: { file: "f.jpg", title: "f", artist: null, source_url: null },
    id: known.id,
    origId: known.id,
    known,
    fallbackUrl: "/art/f.jpg",
  });
  assert.equal(item.source, "ente");
  assert.deepEqual(viewerControls(item), { showMove: false, showTrash: false });
});

test("the history viewer keeps Move and Move to trash for an art piece", () => {
  const known = { id: "Photos/a.jpg", folder: "Photos", url: "/art/Photos/a.jpg" };
  const item = historyViewerItem({
    piece: { file: "a.jpg", title: "a", artist: null, source_url: null },
    id: known.id,
    origId: known.id,
    known,
    fallbackUrl: "/art/a.jpg",
  });
  assert.equal(item.source, undefined);
  assert.deepEqual(viewerControls(item), { showMove: true, showTrash: true });
  const unknown = historyViewerItem({ piece: { file: "a.jpg", title: "a" }, id: "a.jpg", origId: "a.jpg", known: null, fallbackUrl: "/art/a.jpg" });
  assert.equal(unknown.url, "/art/a.jpg");
  assert.equal(unknown.folder, "");
});

// ---- frame buttons, the picker, and a cropped tall piece ----------------------

test("a tall art piece with a saved crop can be shown and pinned", () => {
  const tall = { id: "a", landscape: false };
  assert.equal(viewerState([tall], 0, "grid").showBlockedReason, "Too tall to show on the frame.");
  const cropped = { id: "a", landscape: false, crop: { x: 0, y: 0.2, w: 1, h: 0.45 } };
  assert.equal(viewerState([cropped], 0, "grid").showBlockedReason, null);
});

test("an unusable crop does not unblock a tall piece", () => {
  assert.equal(
    viewerState([{ id: "a", landscape: false, crop: { x: 2 } }], 0, "grid").showBlockedReason,
    "Too tall to show on the frame."
  );
});

test("the two frame buttons name the frame and say what each does", () => {
  const buttons = frameButtons("Nightstand");
  assert.deepEqual(buttons.show, { label: "Show now on Nightstand", hint: "Rotation carries on" });
  assert.deepEqual(buttons.pin, { label: "Pin to Nightstand", hint: "Stays until you unpin it" });
});

test("with no known frame the buttons say 'the frame'", () => {
  assert.equal(frameButtons(null).show.label, "Show now on the frame");
  assert.equal(frameButtons("").pin.label, "Pin to the frame");
});

test("the frame picker is hidden for fewer than two frames", () => {
  assert.equal(framePickerView([], "a").visible, false);
  assert.equal(framePickerView([{ id: "a", label: "A", offline: false }], "a").visible, false);
});

test("the frame picker lists every frame and marks the shared selection", () => {
  const rows = [
    { id: "nightstand", label: "Nightstand", offline: false },
    { id: "office", label: "Office", offline: true },
  ];
  const view = framePickerView(rows, "office");
  assert.equal(view.visible, true);
  assert.deepEqual(
    view.options.map((o) => [o.id, o.label, o.selected]),
    [
      ["nightstand", "Nightstand", false],
      ["office", "Office", true],
    ]
  );
  assert.equal(view.selectedLabel, "Office");
});

test("an unknown selection falls back to the first frame in the picker", () => {
  const rows = [
    { id: "nightstand", label: "Nightstand", offline: false },
    { id: "office", label: "Office", offline: false },
  ];
  const view = framePickerView(rows, "gone");
  assert.equal(view.selectedLabel, "Nightstand");
  assert.equal(view.options[0].selected, true);
});

test("a history item carries the library piece's crop, automatic box and eligibility", () => {
  const known = {
    folder: "Photos",
    url: "/art/Photos/a.jpg",
    crop: { x: 0, y: 0, w: 1, h: 0.5 },
    autoCrop: { x: 0, y: 0.1, w: 1, h: 0.5 },
    landscape: true,
  };
  const item = historyViewerItem({ piece: { file: "a.jpg", title: "A" }, id: "Photos/a.jpg", origId: "x", known, fallbackUrl: "/art/a.jpg" });
  assert.deepEqual(item.crop, known.crop);
  assert.deepEqual(item.autoCrop, known.autoCrop);
  assert.equal(item.landscape, true);
  const unknown = historyViewerItem({ piece: { file: "a.jpg", title: "A" }, id: "a.jpg", origId: "x", known: null, fallbackUrl: "/art/a.jpg" });
  assert.equal(unknown.crop, null);
  assert.equal(unknown.autoCrop, null);
});
