import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const site = new URL("../../roles/room_picker/files/site/", import.meta.url);
const rooms = readFileSync(new URL("rooms.js", site), "utf8");
const html = readFileSync(new URL("index.html", site), "utf8");

test("rooms.js loads the panel layout only by dynamic import, guarded by panel=1", () => {
  assert.doesNotMatch(rooms, /^import[^;]*\.\/panel\.js/m);
  // No static import of any panel module (panel-model.js, panel-*.js): phones
  // must never fetch panel code.
  assert.doesNotMatch(rooms, /^import[^;(]*from\s+"\.\/panel[-.][^"]*"/m);
  const at = rooms.indexOf('import("./panel.js")');
  assert.ok(at > -1, "dynamic import of panel.js");
  const guard = rooms.lastIndexOf('get("panel") === "1"', at);
  assert.ok(guard > -1 && at - guard < 300, "import sits under the panel=1 guard");
});

test("the shipped page carries no panel attribute, stylesheet or node", () => {
  assert.doesNotMatch(html, /data-panel/);
  assert.doesNotMatch(html, /panel\.css/);
  assert.doesNotMatch(html, /panel\.js/);
  assert.doesNotMatch(html, /id="p-|class="p-/);
});

test("the Music tab reads the phone name from the devices list and the now-model rules", () => {
  const rooms = readFileSync(new URL("rooms.js", site), "utf8");
  assert.match(rooms, /phoneName:\s*\(\)\s*=>/);
  assert.match(rooms, /devices\.state\.devices\.find\(\(d\) => d\.connected\)/);
  const music = readFileSync(new URL("panel-music.js", site), "utf8");
  assert.match(music, /from "\.\/now-model\.js"/);
  const model = readFileSync(new URL("rooms-model.js", site), "utf8");
  assert.match(model, /raw\.resume/);
});

test("the settings modules load only through panel.js, never from rooms.js", () => {
  assert.doesNotMatch(rooms, /panel-settings/);
  const panel = readFileSync(new URL("panel.js", site), "utf8");
  assert.match(panel, /from "\.\/panel-settings-controller\.js"/);
  assert.match(panel, /from "\.\/panel-settings\.js"/);
  assert.match(panel, /createPanelSettings\(/);
  assert.match(panel, /createSettingsScreens\(/);
});

test("rooms.js hands settings to the screensaver through the panel host, and replays them", () => {
  assert.match(rooms, /settingsChanged:\s*\(/);
  assert.match(rooms, /screensaver\??\.setSettings\(/);
  // Replayed when the screensaver module finishes loading after the first read.
  const init = rooms.slice(rooms.indexOf('import("./screensaver.js")'));
  assert.match(init.slice(0, 900), /setSettings\(/);
});

test("the panel's More screen asks for the Screen and Screensaver tiles", () => {
  const more = readFileSync(new URL("panel-more.js", site), "utf8");
  assert.match(more, /settings:/);
  assert.match(more, /screen:/);
  assert.match(more, /screensaver:/);
});

test("the page shows the settings pages only with a panel id, and previews through the local port", () => {
  const panel = readFileSync(new URL("panel.js", site), "utf8");
  assert.match(panel, /get\("pid"\)/);
  assert.match(panel, /get\("lp"\)/);
  assert.match(panel, /settings:\s*settings\.available\(\)/);
  assert.match(panel, /127\.0\.0\.1/);
});
