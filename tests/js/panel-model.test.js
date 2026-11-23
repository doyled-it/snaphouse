import { test } from "node:test";
import assert from "node:assert/strict";
import { isPanel } from "../../roles/room_picker/files/site/panel-model.js";

test("isPanel is true only for panel=1", () => {
  assert.equal(isPanel("?panel=1&idle=60"), true);
  assert.equal(isPanel(""), false);
  assert.equal(isPanel("?panel=0"), false);
  assert.equal(isPanel("?panel=true"), false);
  assert.equal(isPanel(undefined), false);
});

import {
  resolvePanelScreen,
  railItems,
  railActive,
  isSubScreen,
  BACK_HASH,
} from "../../roles/room_picker/files/site/panel-model.js";

const ALL = { frame: true, climate: true, garage: true };

test("resolvePanelScreen keeps the panel screens and deep links", () => {
  assert.deepEqual(resolvePanelScreen("#listen", ALL), { screen: "listen", hash: "#listen" });
  assert.deepEqual(resolvePanelScreen("#more", ALL), { screen: "more", hash: "#more" });
  assert.deepEqual(resolvePanelScreen("#health", ALL), { screen: "health", hash: "#health" });
  assert.deepEqual(resolvePanelScreen("#settings", ALL), { screen: "settings", hash: "#settings" });
  assert.deepEqual(resolvePanelScreen("#frame/nightstand", ALL), { screen: "frame", hash: "#frame/nightstand" });
  assert.deepEqual(resolvePanelScreen("#gallery/abc", ALL), { screen: "gallery", hash: "#gallery/abc" });
  assert.deepEqual(resolvePanelScreen("#art/abc", ALL), { screen: "gallery", hash: "#gallery/abc" });
  assert.deepEqual(resolvePanelScreen("#art", ALL), { screen: "gallery", hash: "#gallery" });
});

test("resolvePanelScreen falls back to Listen for hidden or unknown hashes", () => {
  assert.deepEqual(resolvePanelScreen("#garage", { ...ALL, garage: false }), { screen: "listen", hash: "#listen" });
  assert.deepEqual(resolvePanelScreen("#climate", { ...ALL, climate: false }), { screen: "listen", hash: "#listen" });
  assert.deepEqual(resolvePanelScreen("#frame", { ...ALL, frame: false }), { screen: "listen", hash: "#listen" });
  assert.deepEqual(resolvePanelScreen("#nonsense", ALL), { screen: "listen", hash: "#listen" });
  assert.deepEqual(resolvePanelScreen("", ALL), { screen: "listen", hash: "#listen" });
  assert.deepEqual(resolvePanelScreen(undefined, ALL), { screen: "listen", hash: "#listen" });
});

test("railItems lists Music, Climate, Garage, More and hides what is not configured", () => {
  assert.deepEqual(railItems(ALL).map((i) => i.label), ["Music", "Climate", "Garage", "More"]);
  assert.deepEqual(railItems(ALL).map((i) => i.screen), ["listen", "climate", "garage", "more"]);
  assert.deepEqual(railItems({ frame: true, climate: false, garage: false }).map((i) => i.label), ["Music", "More"]);
});

test("railActive maps More's sub-screens to More", () => {
  assert.equal(railActive("listen"), "listen");
  assert.equal(railActive("garage"), "garage");
  for (const s of ["more", "frame", "gallery", "settings", "health"]) assert.equal(railActive(s), "more");
});

test("isSubScreen is true for More's sub-screens only, and back goes to #more", () => {
  for (const s of ["frame", "gallery", "settings", "health"]) assert.equal(isSubScreen(s), true);
  for (const s of ["listen", "climate", "garage", "more"]) assert.equal(isSubScreen(s), false);
  assert.equal(BACK_HASH, "#more");
});

import { stripParts, stripBack } from "../../roles/room_picker/files/site/panel-model.js";

const T = (over) => ({ reachable: true, mode: "cool", setpointF: 74, temperatureF: 72.4, ...over });
const NOW = new Date(2026, 10, 23, 14, 5);

test("stripParts shows temperature, mode and target, garage and time", () => {
  const s = stripParts({ thermostat: T(), garage: { word: "CLOSED" }, now: NOW, locale: "en-US" });
  assert.equal(s.temp, "72");
  assert.equal(s.mode, "cooling to 74");
  assert.equal(s.garage, "Garage closed");
  assert.equal(s.time, "2:05 PM");
});

test("stripParts says heating, and holding when the thermostat is not working a mode", () => {
  assert.equal(stripParts({ thermostat: T({ mode: "heat", setpointF: 68 }), now: NOW }).mode, "heating to 68");
  assert.equal(stripParts({ thermostat: T({ mode: "off", setpointF: 74 }), now: NOW }).mode, "holding 74");
  assert.equal(stripParts({ thermostat: T({ mode: "auto", setpointF: 74 }), now: NOW }).mode, "holding 74");
  assert.equal(stripParts({ thermostat: T({ mode: "cool", setpointF: null }), now: NOW }).mode, null);
});

test("stripParts says Thermostat offline when unreachable and hides the temperature", () => {
  const s = stripParts({ thermostat: T({ reachable: false }), now: NOW });
  assert.equal(s.temp, null);
  assert.equal(s.mode, "Thermostat offline");
});

test("stripParts hides the temperature part with no reading or no climate feature", () => {
  assert.deepEqual(
    [stripParts({ thermostat: null, now: NOW }).temp, stripParts({ thermostat: null, now: NOW }).mode],
    [null, null],
  );
  const s = stripParts({ thermostat: T({ temperatureF: null, reachable: null }), now: NOW });
  assert.equal(s.temp, null);
  assert.equal(s.mode, null);
});

test("stripParts reads Garage unknown when the link is down, and nothing when the feature is off", () => {
  assert.equal(stripParts({ garage: { word: "UNKNOWN" }, now: NOW }).garage, "Garage unknown");
  assert.equal(stripParts({ garage: { word: "OPENING" }, now: NOW }).garage, "Garage opening");
  assert.equal(stripParts({ garage: null, now: NOW }).garage, null);
});

test("stripBack is true on More's sub-screens, which also hide the time", () => {
  assert.equal(stripBack("frame"), true);
  assert.equal(stripBack("listen"), false);
  assert.equal(stripBack("more"), false);
});
