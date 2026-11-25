import { test } from "node:test";
import assert from "node:assert/strict";
import {
  DESKTOP_MIN_WIDTH,
  DESKTOP_QUERY,
  isDesktop,
  everythingOffHidden,
  climatePollShown,
  garagePollShown,
} from "../../roles/room_picker/files/site/desktop-model.js";

test("the breakpoint is 1100 px and the query says so", () => {
  assert.equal(DESKTOP_MIN_WIDTH, 1100);
  assert.equal(DESKTOP_QUERY, "(min-width: 1100px)");
});

test("desktop needs a wide window and not the panel", () => {
  assert.equal(isDesktop({ matches: true, panel: false }), true);
  assert.equal(isDesktop({ matches: false, panel: false }), false);
  assert.equal(isDesktop({ matches: true, panel: true }), false);
  assert.equal(isDesktop({ matches: false, panel: true }), false);
});

test("Everything off shows on every screen at desktop width", () => {
  for (const screen of ["listen", "frame", "gallery", "climate", "garage", "settings"]) {
    assert.equal(everythingOffHidden(screen, true), false, screen);
  }
});

test("the phone keeps Everything off on Listen only", () => {
  assert.equal(everythingOffHidden("listen", false), false);
  for (const screen of ["frame", "gallery", "climate", "garage", "settings"]) {
    assert.equal(everythingOffHidden(screen, false), true, screen);
  }
});

test("climate polls at the shown rate on its tab, and on Listen at desktop width", () => {
  assert.equal(climatePollShown("climate", false), true);
  assert.equal(climatePollShown("climate", true), true);
  assert.equal(climatePollShown("listen", true), true);
  assert.equal(climatePollShown("listen", false), false);
  assert.equal(climatePollShown("settings", true), false);
  assert.equal(climatePollShown("gallery", false), false);
});

test("garage polls at the shown rate on its tab, and on Listen at desktop width", () => {
  assert.equal(garagePollShown("garage", false), true);
  assert.equal(garagePollShown("garage", true), true);
  assert.equal(garagePollShown("listen", true), true);
  assert.equal(garagePollShown("listen", false), false);
  assert.equal(garagePollShown("frame", true), false);
});
