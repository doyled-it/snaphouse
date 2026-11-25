import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const site = new URL("../../roles/room_picker/files/site/", import.meta.url);
const rooms = readFileSync(new URL("rooms.js", site), "utf8");
const css = readFileSync(new URL("style.css", site), "utf8");

test("rooms.js loads the screensaver only by dynamic import, guarded by panel=1", () => {
  assert.doesNotMatch(rooms, /^import[^;]*screensaver\.js/m);
  assert.match(rooms, /import\(\s*"\.\/screensaver\.js"\s*\)/);
  const at = rooms.indexOf('import("./screensaver.js")');
  const guard = rooms.lastIndexOf('get("panel") === "1"', at);
  assert.ok(guard > -1 && at - guard < 200, "dynamic import must sit right under the panel=1 guard");
});

test("followCover is the cover follower and hands the last cover's hues to the screensaver", () => {
  const start = rooms.indexOf("const followCover = createCoverFollower(");
  assert.ok(start > -1);
  const body = rooms.slice(start, rooms.indexOf("\n});\n", start));
  assert.equal(body.split("screensaver?.setHues").length - 1, 1);
  assert.match(body, /setPage:[^\n]*applyCoverColor/);
});

test("the overlay css covers the page and is inert until up", () => {
  const base = /\.screensaver\s*\{([^}]*)\}/.exec(css);
  assert.ok(base, ".screensaver rule");
  assert.match(base[1], /position:\s*fixed/);
  assert.match(base[1], /inset:\s*0/);
  assert.match(base[1], /background:\s*#000/);
  assert.match(base[1], /opacity:\s*0/);
  assert.match(base[1], /pointer-events:\s*none/);
  const z = Number(/z-index:\s*(\d+)/.exec(base[1])?.[1]);
  const toast = Number(/\.toast\s*\{[^}]*z-index:\s*(\d+)/.exec(css)?.[1]);
  assert.ok(z > toast, "above toasts");
  const up = /\.screensaver\.is-up\s*\{([^}]*)\}/.exec(css);
  assert.ok(up, ".is-up rule");
  assert.match(up[1], /opacity:\s*1/);
  assert.match(up[1], /pointer-events:\s*auto/);
  assert.match(css, /prefers-reduced-motion[^{]*\{[\s\S]*?\.screensaver/);
});

test("the page feeds the track to the screensaver and lands on Now playing after a dismiss", () => {
  assert.match(rooms, /screensaver\?\.setTrack\(track\)/);
  assert.match(rooms, /screensaver\?\.setTrack\(state\.nowTrack\)/);
  assert.match(rooms, /onDismiss:/);
  assert.match(rooms, /panelLayout\?\.showNow\?\.\(\)/);
});
