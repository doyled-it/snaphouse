// Source-text pins for the frame change feedback wiring in rooms.js, index.html and
// style.css, in the style of rooms-panel-wiring.test.js. The rules themselves are
// tested in frame-change-model.test.js.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const site = (name) => readFileSync(fileURLToPath(new URL(`../../roles/room_picker/files/site/${name}`, import.meta.url)), "utf8");
const rooms = site("rooms.js");
const html = site("index.html");
const css = site("style.css");

test("rooms.js imports the change model", () => {
  assert.match(rooms, /from "\.\/frame-change-model\.js"/);
});

test("every frame command goes through sendFrameCommand, which starts a tracked change", () => {
  assert.match(rooms, /async function sendFrameCommand\(/);
  assert.match(rooms, /startChange\(/);
  assert.match(rooms, /async function runArtCommand\(action, piece\) \{\s*const result = await sendFrameCommand\(/);
  assert.match(rooms, /showOnFrame: async \(frameId, pieceId\) => \{\s*const result = await sendFrameCommand\(/);
  assert.match(rooms, /await sendFrameCommand\(state\.selectedFrame, "show", piece\.id\)/);
});

test("the seq comes from the command reply and a newer tap keeps its own change", () => {
  assert.match(rooms, /result\.data\.command\.seq/);
  assert.match(rooms, /if \(state\.change === started\)/);
});

test("the 2 s read registers with page-timers, so it sleeps while the tab is hidden", () => {
  assert.match(rooms, /timers\.add\(changeTick, FAST_POLL_MS\)/);
  assert.equal(/setInterval\(changeTick/.test(rooms), false);
});

test("the fast read fetches the frame's frame.json only, never frame.png", () => {
  const body = rooms.slice(rooms.indexOf("async function changeTick"), rooms.indexOf("function changeLookup"));
  assert.match(body, /urls\.meta/);
  assert.equal(/urls\.image/.test(body), false);
});

test("the art index is loaded for titles when the Frames screen has none", () => {
  assert.match(rooms, /if \(!state\.artIndex\) \{\s*refreshLibrary\(\);/);
});

test("the page has a polite status region with a thumbnail, text and progress bar", () => {
  assert.match(html, /id="frame-change"[^>]*role="status"[^>]*aria-live="polite"/);
  assert.match(html, /id="frame-change-thumb"/);
  assert.match(html, /id="frame-change-text"/);
  assert.match(html, /frame-change-bar/);
});

test("the progress bar stops animating for reduced motion", () => {
  const block = css.slice(css.indexOf(".frame-change-bar"));
  assert.match(block, /@media \(prefers-reduced-motion: reduce\)[\s\S]*?\.frame-change-bar span/);
});

test("renderChange is the only writer of the status text", () => {
  const writers = rooms.match(/frameChangeText\.textContent\s*=/g) ?? [];
  const inRender = rooms.slice(rooms.indexOf("function renderChange")).match(/frameChangeText\.textContent\s*=/g) ?? [];
  assert.equal(writers.length, inRender.length);
});

test("the panel host exposes the change view", () => {
  assert.match(rooms, /frameChange: currentChangeView/);
});
