// Wiring checks for the frame crop page work, read as text like the other
// rooms-*-wiring tests: the DOM the editor needs exists, the editor is never
// loaded on the wall panel, and the viewer's frame controls follow the shared
// selection.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const site = new URL("../../roles/room_picker/files/site/", import.meta.url);
const read = (name) => readFileSync(new URL(name, site), "utf8");
const rooms = read("rooms.js");
const html = read("index.html");
const panelMore = read("panel-more.js");
const css = read("style.css");
const panelCss = read("panel.css").replace(/\/\*[\s\S]*?\*\//g, "");

test("every id the crop editor and viewer controls read exists in index.html", () => {
  for (const id of [
    "viewer-frame",
    "viewer-crop",
    "viewer-crop-box",
    "viewer-crop-label",
    "viewer-edit-crop",
    "viewer-frame-pick",
    "viewer-frame-select",
    "viewer-show-hint",
    "viewer-pin-hint",
    "crop-panel",
    "crop-preview",
    "crop-status",
    "crop-save",
    "crop-cancel",
    "crop-auto",
  ]) {
    assert.match(html, new RegExp(`id="${id}"`), id);
    assert.match(rooms, new RegExp(`getElementById\\("${id}"\\)`), `${id} read in rooms.js`);
  }
  assert.equal((html.match(/class="vc-handle /g) || []).length, 4, "four corner handles");
});

test("the corner handles start hidden and every control is a real button", () => {
  for (const corner of ["nw", "ne", "sw", "se"]) {
    assert.match(html, new RegExp(`data-corner="${corner}" hidden`));
  }
  assert.match(html, /<button id="viewer-edit-crop"[^>]*type="button"/);
  assert.match(html, /<button id="crop-save"[^>]*type="button"/);
});

test("the wall panel never loads the editor, the preview or the Edit crop button", () => {
  assert.doesNotMatch(panelMore, /crop-editor|crop-preview|Edit crop/);
  assert.doesNotMatch(read("panel.js"), /crop-editor|crop-preview|Edit crop/);
  // The panel's viewer draws the overlay through the shared, read-only helper.
  assert.match(panelMore, /from "\.\/crop-overlay\.js"/);
  assert.doesNotMatch(read("crop-overlay.js"), /pointerdown|keydown|fetch\(/);
});

test("the editor posts through the page's guarded write helper, never its own fetch", () => {
  assert.match(rooms, /cropRequest\(piece\.id, box\)/);
  assert.match(rooms, /postApi\(path, body\)/);
  assert.doesNotMatch(read("crop-editor.js"), /fetch\(/);
});

test("a save reads the library again so the grid, overlay and label follow at once", () => {
  const start = rooms.indexOf("function startCropEdit()");
  const end = rooms.indexOf("function wireViewer()");
  assert.match(rooms.slice(start, end), /await refreshLibrary\(\)/);
});

test("the viewer's picker reads and writes the shared selection", () => {
  assert.match(rooms, /framePickerView\(rows, state\.selectedFrame\)/);
  assert.match(rooms, /viewerFrameSelect\.addEventListener\("change", \(\) => selectFrame\(/);
  // selectFrame refreshes an open viewer, so a Frame tab change shows here too.
  const at = rooms.indexOf("function selectFrame(id)");
  assert.match(rooms.slice(at, at + 700), /syncViewer\(\)/);
});

test("the editor stops the swipe and the gallery's arrow keys while it is open", () => {
  assert.match(rooms, /start = getCropEditor\(\)\.isOpen\(\) \? null/);
  assert.match(read("crop-editor.js"), /Never step the gallery behind the editor/);
});

test("a grid tile's signature comes from the model, so a crop change replaces only its tile", () => {
  assert.match(rooms, /sig: \(piece\) => libraryTileSignature\(piece, folderNames\)/);
});

test("the touch targets are at least 44 px and the dragged area blocks page scrolling", () => {
  assert.match(css, /\.viewer-frame\.is-editing \.vc-handle \{[^}]*width: min\(44px, 45%\)/);
  assert.match(css, /\.viewer-frame\.is-editing \{[^}]*touch-action: none/);
});

test("panel overlay rules are scoped to the panel and keep text at 20 px or more", () => {
  const rules = panelCss.match(/[^{}]*\.vc-label[^{}]*\{[^}]*\}/g) || [];
  assert.ok(rules.length > 0);
  for (const rule of rules) {
    assert.match(rule, /^\s*html\[data-panel\]/);
    assert.match(rule, /font-size: [2-9]\dpx/);
  }
});

test("no em dash or en dash in the new copy", () => {
  for (const name of ["crop-model.js", "crop-preview.js", "crop-editor.js", "crop-overlay.js", "gallery-viewer.js", "index.html"]) {
    assert.doesNotMatch(read(name), /[–—]/, name);
  }
});
