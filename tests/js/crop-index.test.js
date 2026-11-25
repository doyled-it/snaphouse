// The page's reading of `crop` and `auto_crop` from art-index.json (contract in
// docs/cross-repo-features.md) and the grid tile signature that follows them.
import { test } from "node:test";
import assert from "node:assert/strict";

import { parseArtIndex, libraryTileSignature } from "../../roles/room_picker/files/site/rooms-model.js";

const piece = (extra = {}) => ({
  id: "Photos/a.jpg",
  folder: "Photos",
  file: "a.jpg",
  title: "A",
  artist: null,
  source_url: null,
  width: 4000,
  height: 3000,
  landscape: true,
  thumb: "/art-thumbs/a.jpg",
  url: "/art/Photos/a.jpg",
  ...extra,
});

const doc = (pieces) => ({
  folders: [{ name: "Photos", count: pieces.length, enabled: true, builtin: false }],
  pieces,
  trash: [],
});

test("parseArtIndex keeps a valid crop and automatic box", () => {
  const crop = { x: 0.1, y: 0.05, w: 0.6, h: 0.6048 };
  const auto = { x: 0, y: 0, w: 0.9921, h: 1 };
  const [p] = parseArtIndex(doc([piece({ crop, auto_crop: auto })])).pieces;
  assert.deepEqual(p.crop, crop);
  assert.deepEqual(p.autoCrop, auto);
});

test("parseArtIndex reads a missing or malformed box as null and keeps the piece", () => {
  for (const extra of [{}, { crop: null, auto_crop: null }, { crop: "x", auto_crop: { x: 2, y: 0, w: 1, h: 1 } }]) {
    const index = parseArtIndex(doc([piece(extra)]));
    assert.equal(index.pieces.length, 1);
    assert.equal(index.pieces[0].crop, null);
    assert.equal(index.pieces[0].autoCrop, null);
  }
});

const sig = (p, folders = "Photos") => libraryTileSignature(p, folders);

test("the tile signature changes when the crop or the automatic box changes", () => {
  const base = parseArtIndex(doc([piece()])).pieces[0];
  const cropped = parseArtIndex(doc([piece({ crop: { x: 0, y: 0, w: 0.5, h: 0.5 * 0.75 * 0.6 / 0.6 } })])).pieces[0];
  const moved = parseArtIndex(doc([piece({ crop: { x: 0.1, y: 0, w: 0.5, h: 0.375 } })])).pieces[0];
  const auto = parseArtIndex(doc([piece({ auto_crop: { x: 0, y: 0, w: 1, h: 1 } })])).pieces[0];
  const sigs = new Set([sig(base), sig(cropped), sig(moved), sig(auto)]);
  assert.equal(sigs.size, 4);
});

test("the tile signature is stable for an unchanged piece and still follows the old fields", () => {
  const a = parseArtIndex(doc([piece()])).pieces[0];
  const b = parseArtIndex(doc([piece()])).pieces[0];
  assert.equal(sig(a), sig(b));
  const retitled = parseArtIndex(doc([piece({ title: "B" })])).pieces[0];
  assert.notEqual(sig(a), sig(retitled));
  const thumbed = parseArtIndex(doc([piece({ thumb: "/art-thumbs/z.jpg" })])).pieces[0];
  assert.notEqual(sig(a), sig(thumbed));
  assert.notEqual(sig(a, "Photos"), sig(a, "Photos\u0001Other"));
});
