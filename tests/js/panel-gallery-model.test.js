import { test } from "node:test";
import assert from "node:assert/strict";
import { galleryPage, showButtons, moreTiles } from "../../roles/room_picker/files/site/panel-gallery-model.js";

const items = (n) => Array.from({ length: n }, (_, i) => ({ id: `p${i}`, landscape: true }));

test("galleryPage shows three per page with a range label", () => {
  const p = galleryPage(items(143), 0);
  assert.deepEqual([p.items.map((x) => x.id), p.label, p.hasPrev, p.hasNext, p.page], [["p0", "p1", "p2"], "1 to 3 of 143", false, true, 0]);
});

test("galleryPage's last page can be partial, and an out-of-range page clamps", () => {
  const p = galleryPage(items(143), 999);
  assert.deepEqual([p.items.length, p.label, p.hasPrev, p.hasNext, p.page], [2, "142 to 143 of 143", true, false, 47]);
  assert.equal(galleryPage(items(4), 1).label, "4 of 4");
});

test("galleryPage with nothing says so and has no pager", () => {
  const p = galleryPage([], 0);
  assert.deepEqual([p.items.length, p.label, p.hasPrev, p.hasNext], [0, "No pictures yet", false, false]);
});

const F1 = [{ id: "nightstand", label: "Nightstand" }];
const F2 = [...F1, { id: "office", label: "Office" }];

test("showButtons is one Show on the frame for one frame", () => {
  assert.deepEqual(showButtons({ id: "a", landscape: true }, F1), { buttons: [{ frameId: "nightstand", label: "Show on the frame" }], note: null });
});

test("showButtons is one per frame when there are several", () => {
  assert.deepEqual(
    showButtons({ id: "a", landscape: true }, F2).buttons.map((b) => b.label),
    ["Show on Nightstand", "Show on Office"],
  );
});

test("showButtons gives the phone's sentence and no button for a tall art piece", () => {
  assert.deepEqual(showButtons({ id: "a", landscape: false }, F2), { buttons: [], note: "Too tall to show on the frame." });
  assert.equal(showButtons({ id: "a", landscape: false, source: "ente" }, F1).buttons.length, 1);
});

test("moreTiles lists four tiles with short lines", () => {
  const t = moreTiles({ frames: 2, pictureCount: 143, troubled: 0 });
  assert.deepEqual(t.map((x) => x.id), ["frames", "gallery", "bluetooth", "health"]);
  assert.deepEqual(t.map((x) => x.line), ["2 frames", "143 pictures", "Pair a phone, devices", "All good"]);
  assert.equal(t[3].warn, false);
});

test("moreTiles pluralises and warns when something needs attention", () => {
  const t = moreTiles({ frames: 1, pictureCount: 1, troubled: 2 });
  assert.deepEqual([t[0].line, t[1].line, t[3].line, t[3].warn], ["1 frame", "1 picture", "2 need attention", true]);
  assert.equal(moreTiles({ frames: 1, pictureCount: 1, troubled: 1 })[3].line, "1 needs attention");
});

test("moreTiles leaves out Frames when there are no frames", () => {
  assert.deepEqual(moreTiles({ frames: null, pictureCount: 3, troubled: 0 }).map((x) => x.id), ["gallery", "bluetooth", "health"]);
});
