import { test } from "node:test";
import assert from "node:assert/strict";
import { resolveScreen, visibleTabs, TAB_LABELS, CLIMATE_ENABLED } from "../../roles/room_picker/files/site/nav-model.js";

const none = { frame: false, climate: false };
const withFrame = { frame: true, climate: false };

test("#art aliases to gallery", () => {
  assert.deepEqual(resolveScreen("#art", none), { screen: "gallery", hash: "#gallery" });
});
test("#art/x aliases to gallery", () => {
  assert.equal(resolveScreen("#art/x", none).screen, "gallery");
});
test("hidden climate falls back to listen", () => {
  assert.deepEqual(resolveScreen("#climate", none), { screen: "listen", hash: "#listen" });
});
test("hidden frame falls back to listen", () => {
  assert.equal(resolveScreen("#frame", none).screen, "listen");
  assert.equal(resolveScreen("#frame/office", none).screen, "listen");
});
test("frame id hash is kept when frame is visible", () => {
  assert.deepEqual(resolveScreen("#frame/office", withFrame), { screen: "frame", hash: "#frame/office" });
});
test("empty and unknown hashes give listen", () => {
  assert.deepEqual(resolveScreen("", none), { screen: "listen", hash: "#listen" });
  assert.deepEqual(resolveScreen("#bogus", none), { screen: "listen", hash: "#listen" });
});
test("visible tabs in bar order", () => {
  assert.deepEqual(visibleTabs(none), ["listen", "gallery", "settings"]);
  assert.deepEqual(visibleTabs(withFrame), ["listen", "frame", "gallery", "settings"]);
  assert.deepEqual(visibleTabs({ frame: true, climate: true }), ["listen", "frame", "gallery", "climate", "settings"]);
});
test("every tab has a label", () => {
  assert.deepEqual(TAB_LABELS, { listen: "Listen", frame: "Frame", gallery: "Gallery", climate: "Climate", settings: "Settings" });
});
test("climate stays hidden until the homebody page exists", () => {
  assert.equal(CLIMATE_ENABLED, false);
});

import { frameTabVisible } from "../../roles/room_picker/files/site/nav-model.js";

test("frame tab shows until the first probe finishes", () => {
  assert.equal(frameTabVisible(null, {}, false), true);
});
test("frame tab shows when the control document lists frames", () => {
  assert.equal(frameTabVisible({ available: true, legacy: false, frames: { office: {} } }, {}, true), true);
});
test("frame tab shows when a legacy mirror answers", () => {
  const legacy = { available: false, legacy: true, frames: { nightstand: {} } };
  assert.equal(frameTabVisible(legacy, { nightstand: { screen: "art" } }, true), true);
});
test("frame tab hides when nothing lists a frame and no mirror answers", () => {
  const legacy = { available: false, legacy: true, frames: { nightstand: {} } };
  assert.equal(frameTabVisible(legacy, { nightstand: null }, true), false);
  assert.equal(frameTabVisible(null, {}, true), false);
});

test("#gallery/<id> keeps the piece, and #art/<id> aliases to it", () => {
  assert.deepEqual(resolveScreen("#gallery/Trip%2Fa.jpg", none), { screen: "gallery", hash: "#gallery/Trip%2Fa.jpg" });
  assert.deepEqual(resolveScreen("#art/a.jpg", none), { screen: "gallery", hash: "#gallery/a.jpg" });
  assert.deepEqual(resolveScreen("#gallery/", none), { screen: "gallery", hash: "#gallery" });
});
