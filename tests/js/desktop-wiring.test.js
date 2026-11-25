import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const site = new URL("../../roles/room_picker/files/site/", import.meta.url);
const rooms = readFileSync(new URL("rooms.js", site), "utf8");
const html = readFileSync(new URL("index.html", site), "utf8");
const css = readFileSync(new URL("desktop.css", site), "utf8");

test("the page links desktop.css under the desktop media query only", () => {
  assert.match(html, /<link rel="stylesheet" href="desktop\.css" media="\(min-width: 1100px\)" \/>/);
});

test("rooms.js takes the desktop rules from desktop-model.js and never listens under panel=1", () => {
  assert.match(rooms, /from "\.\/desktop-model\.js"/);
  assert.match(rooms, /everythingOffHidden\(state\.screen, desktopMode\)/);
  assert.match(rooms, /climatePollShown\(state\.screen, desktopMode\)/);
  assert.match(rooms, /garagePollShown\(state\.screen, desktopMode\)/);
  const fn = rooms.slice(rooms.indexOf("function wireDesktop"));
  assert.match(fn.slice(0, 200), /if \(PANEL_MODE\)\s*\{\s*return;/);
});

test("every desktop.css rule is inside the 1100 px query (Home fit aside) and scoped away from the panel", () => {
  const body = css.replace(/\/\*[\s\S]*?\*\//g, "");
  // The 1100 px query, plus Home's one nested height query for the one-viewport fit.
  assert.deepEqual(body.match(/@media[^{]+/g).map((m) => m.trim()), [
    "@media (min-width: 1100px)",
    "@media (min-height: 760px)",
  ]);
  const selectors = [...body.matchAll(/([^{}]+)\{/g)].map((m) => m[1].trim()).filter((s) => !s.startsWith("@media"));
  for (const sel of selectors) {
    for (const part of sel.split(",")) {
      assert.match(part.trim(), /^html:not\(\[data-panel\]\)/, part);
    }
  }
});

test("desktop.css has no em or en dashes", () => {
  assert.doesNotMatch(css, /[–—]/);
});

test("Home: the strip is drawn from the polled climate and garage documents", () => {
  assert.match(html, /<div id="home-strip" class="home-strip" role="group" aria-label="House status" hidden><\/div>/);
  assert.match(rooms, /from "\.\/desktop-status-model\.js"/);
  assert.match(rooms, /from "\.\/desktop-home\.js"/);
  const fn = rooms.slice(rooms.indexOf("function renderHomeStrip"));
  assert.match(fn.slice(0, 700), /statusChips\(\{[\s\S]*climate: state\.climate[\s\S]*garage: state\.garage/);
  for (const name of ["function applyClimate", "function applyGarage"]) {
    const body = rooms.slice(rooms.indexOf(name), rooms.indexOf(name) + 900);
    assert.match(body, /renderHomeStrip\(\)/, name);
  }
});

test("Home: slider orientation follows the desktop state, and the strip is hidden on the phone", () => {
  assert.match(rooms, /setOrientation\(desktopMode \? "vertical" : "horizontal"\)/);
  const style = readFileSync(new URL("style.css", site), "utf8");
  assert.match(style, /\.home-strip,[^{]*\{\s*display:\s*none;/);
});
