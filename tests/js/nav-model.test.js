import { test } from "node:test";
import assert from "node:assert/strict";
import { resolveScreen, visibleTabs, TAB_LABELS } from "../../roles/room_picker/files/site/nav-model.js";

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
  assert.deepEqual(TAB_LABELS, { listen: "Listen", frame: "Frame", gallery: "Gallery", climate: "Climate", garage: "Garage", settings: "Settings" });
});
test("climate resolves to itself once it is visible", () => {
  assert.deepEqual(resolveScreen("#climate", { frame: false, climate: true }), { screen: "climate", hash: "#climate" });
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

import { climateTabVisible, garageTabVisible } from "../../roles/room_picker/files/site/nav-model.js";

test("climate tab shows until the first read finishes", () => {
  assert.equal(climateTabVisible(null, false), true);
});
test("climate tab shows when homebody.json parsed", () => {
  assert.equal(climateTabVisible({ enabled: true }, true), true);
});
test("climate tab hides when homebody.json is missing or malformed", () => {
  assert.equal(climateTabVisible(null, true), false);
  assert.equal(climateTabVisible(undefined, true), false);
});

test("six tabs in bar order when everything is visible", () => {
  assert.deepEqual(visibleTabs({ frame: true, climate: true, garage: true }), [
    "listen", "frame", "gallery", "climate", "garage", "settings",
  ]);
});
test("garage is hidden unless visible.garage is true", () => {
  assert.deepEqual(visibleTabs({ frame: false, climate: true }), ["listen", "gallery", "climate", "settings"]);
  assert.equal(visibleTabs({ garage: false }).includes("garage"), false);
});
test("hidden garage falls back to listen", () => {
  assert.deepEqual(resolveScreen("#garage", none), { screen: "listen", hash: "#listen" });
});
test("garage resolves to itself once it is visible", () => {
  assert.deepEqual(resolveScreen("#garage", { garage: true }), { screen: "garage", hash: "#garage" });
});
test("garage tab shows until the first read finishes", () => {
  assert.equal(garageTabVisible(null, false), true);
});
test("garage tab shows when the garage block parsed", () => {
  assert.equal(garageTabVisible({ state: "closed" }, true), true);
});
test("garage tab hides when the block is null or missing", () => {
  assert.equal(garageTabVisible(null, true), false);
  assert.equal(garageTabVisible(undefined, true), false);
});
