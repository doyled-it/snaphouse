import { test } from "node:test";
import assert from "node:assert/strict";
import {
  SETTINGS_DEFAULTS,
  WAIT_CHOICES,
  parsePanelSettings,
  settingsFromParams,
  checkPatch,
  stepPct,
  pctFromFraction,
  stepTime,
  timeLabel,
  waitLabel,
  diffPatch,
  tileLines,
  stylesView,
  changeView,
} from "../../roles/room_picker/files/site/panel-settings-model.js";
import { parsePanelParams } from "../../roles/room_picker/files/site/screensaver-model.js";

const doc = (entry) => ({ version: 1, panels: { panel: { ...SETTINGS_DEFAULTS, ...entry } } });

test("parsePanelSettings reads the pid entry", () => {
  const got = parsePanelSettings(doc({ brightness_pct: 70, screensaver_wait_s: 120 }), "panel");
  assert.equal(got.brightness_pct, 70);
  assert.equal(got.screensaver_wait_s, 120);
  assert.equal(got.night_start, "22:00");
});

test("parsePanelSettings returns null for a bad document or no entry", () => {
  for (const bad of [null, "x", [], {}, { version: 2, panels: {} }, { version: 1, panels: [] }, { version: 1, panels: { other: {} } }, { version: 1, panels: { panel: "x" } }]) {
    assert.equal(parsePanelSettings(bad, "panel"), null, JSON.stringify(bad));
  }
  assert.equal(parsePanelSettings(doc({}), ""), null);
  assert.equal(parsePanelSettings(doc({}), null), null);
});

test("parsePanelSettings falls back per key", () => {
  const fallback = { ...SETTINGS_DEFAULTS, brightness_pct: 60 };
  const got = parsePanelSettings(doc({ brightness_pct: 7, night_dim_pct: 30, screensaver_wait_s: "x", night_start: "22:15", screensaver_on: "no" }), "panel", fallback);
  assert.equal(got.brightness_pct, 60);
  assert.equal(got.night_dim_pct, 30);
  assert.equal(got.screensaver_wait_s, 60);
  assert.equal(got.night_start, "22:00");
  assert.equal(got.screensaver_on, true);
});

test("parsePanelSettings refuses equal night times as a pair", () => {
  const got = parsePanelSettings(doc({ night_start: "06:00", night_end: "06:00" }), "panel");
  assert.deepEqual([got.night_start, got.night_end], ["22:00", "06:00"]);
});

test("settingsFromParams takes the wait, quiet hours and on/off from the URL values", () => {
  const params = parsePanelParams("?panel=1&idle=120&qs=21:30&qe=07:00&ss=0");
  const got = settingsFromParams(params);
  assert.equal(got.screensaver_wait_s, 120);
  assert.equal(got.night_start, "21:30");
  assert.equal(got.night_end, "07:00");
  assert.equal(got.screensaver_on, false);
  assert.equal(got.brightness_pct, SETTINGS_DEFAULTS.brightness_pct);
});

test("settingsFromParams snaps an odd wait to the nearest choice", () => {
  assert.equal(settingsFromParams({ dayIdleMs: 45000, quietStart: "22:00", quietEnd: "06:00", enabled: true }).screensaver_wait_s, 30);
  assert.equal(settingsFromParams({ dayIdleMs: 250000, quietStart: "22:00", quietEnd: "06:00", enabled: true }).screensaver_wait_s, 300);
});

test("WAIT_CHOICES and waitLabel", () => {
  assert.deepEqual(WAIT_CHOICES, [30, 60, 120, 300, 600]);
  assert.deepEqual(WAIT_CHOICES.map(waitLabel), ["30 s", "1 min", "2 min", "5 min", "10 min"]);
});

test("stepPct steps by 5 and clamps at the floor and 100", () => {
  assert.equal(stepPct(100, 1, 20), 100);
  assert.equal(stepPct(25, -1, 20), 20);
  assert.equal(stepPct(20, -1, 20), 20);
  assert.equal(stepPct(15, -1, 0), 10);
  assert.equal(stepPct(0, -1, 0), 0);
  assert.equal(stepPct(5, -1, 5), 5);
  assert.equal(stepPct(40, 1, 5), 45);
});

test("pctFromFraction snaps a tap on the bar to steps of 5 with the floor", () => {
  assert.equal(pctFromFraction(0.5, 20), 50);
  assert.equal(pctFromFraction(0.01, 20), 20);
  assert.equal(pctFromFraction(0.01, 0), 0);
  assert.equal(pctFromFraction(0.01, 5), 5);
  assert.equal(pctFromFraction(1.4, 20), 100);
  assert.equal(pctFromFraction(-1, 0), 0);
  assert.equal(pctFromFraction(0.123, 0), 10);
});

test("stepTime moves 30 minutes, wraps midnight and skips the other time", () => {
  assert.equal(stepTime("22:00", 1, "06:00"), "22:30");
  assert.equal(stepTime("23:30", 1, "06:00"), "00:00");
  assert.equal(stepTime("00:00", -1, "06:00"), "23:30");
  assert.equal(stepTime("05:30", 1, "06:00"), "06:30");
  assert.equal(stepTime("06:30", -1, "06:00"), "05:30");
});

test("timeLabel formats 12-hour", () => {
  assert.equal(timeLabel("22:00"), "10:00 PM");
  assert.equal(timeLabel("06:30"), "6:30 AM");
  assert.equal(timeLabel("00:30"), "12:30 AM");
  assert.equal(timeLabel("12:00"), "12:00 PM");
  assert.equal(timeLabel("bad"), "");
});

test("checkPatch accepts valid changes and returns only them", () => {
  assert.deepEqual(checkPatch(SETTINGS_DEFAULTS, { brightness_pct: 70 }), { ok: true, patch: { brightness_pct: 70 }, error: null });
});

test("checkPatch refuses equal times and off-step or out-of-range values", () => {
  assert.equal(checkPatch(SETTINGS_DEFAULTS, { night_start: "06:00" }).ok, false);
  for (const patch of [{ brightness_pct: 15 }, { brightness_pct: 22 }, { night_dim_pct: 7 }, { screensaver_pct: 0 }, { screensaver_wait_s: 45 }, { night_start: "22:15" }, { screensaver_style: "aurora" }, { screensaver_styles: [] }, { screensaver_styles: ["clock", "aurora"] }, { screensaver_change_min: 10 }, { screensaver_on: 1 }, { brightness_pct: "70" }, { colour: 1 }, {}]) {
    const got = checkPatch(SETTINGS_DEFAULTS, patch);
    assert.equal(got.ok, false, JSON.stringify(patch));
    assert.ok(got.error);
  }
});

test("diffPatch lists only the keys that differ", () => {
  assert.deepEqual(diffPatch(SETTINGS_DEFAULTS, { ...SETTINGS_DEFAULTS, brightness_pct: 70 }), { brightness_pct: 70 });
  assert.deepEqual(diffPatch(SETTINGS_DEFAULTS, SETTINGS_DEFAULTS), {});
});

test("tileLines reads Brightness, night and the screensaver line", () => {
  assert.deepEqual(tileLines(SETTINGS_DEFAULTS), { screen: "Brightness, night", saver: "Aurora after 1 min" });
  assert.equal(tileLines({ ...SETTINGS_DEFAULTS, screensaver_on: false }).saver, "Off");
  assert.equal(tileLines({ ...SETTINGS_DEFAULTS, screensaver_wait_s: 600 }).saver, "Aurora after 10 min");
});

import { barView, saverView } from "../../roles/room_picker/files/site/panel-settings-model.js";

test("barView gives the fill, label and which steppers are at their limit", () => {
  assert.deepEqual(barView(100, 20), { fill: 100, label: "100%", low: false, minusDisabled: false, plusDisabled: true });
  assert.deepEqual(barView(20, 20), { fill: 20, label: "20%", low: true, minusDisabled: true, plusDisabled: false });
  assert.deepEqual(barView(0, 0), { fill: 0, label: "0%", low: true, minusDisabled: true, plusDisabled: false });
});

test("saverView dims the other rows and disables Preview when the screensaver is off", () => {
  assert.deepEqual(saverView({ ...SETTINGS_DEFAULTS, screensaver_on: false }), { rowsDimmed: true, previewDisabled: true });
  assert.deepEqual(saverView(SETTINGS_DEFAULTS), { rowsDimmed: false, previewDisabled: false });
});

test("defaults carry the style list and the change interval, not the old style", () => {
  assert.deepEqual(SETTINGS_DEFAULTS.screensaver_styles, ["aurora"]);
  assert.equal(SETTINGS_DEFAULTS.screensaver_change_min, 15);
  assert.equal("screensaver_style" in SETTINGS_DEFAULTS, false);
});

test("checkPatch mirrors the hub for styles and change interval", () => {
  assert.equal(checkPatch(SETTINGS_DEFAULTS, { screensaver_styles: ["aurora", "clock"] }).ok, true);
  assert.equal(checkPatch(SETTINGS_DEFAULTS, { screensaver_styles: ["life", "harmonograph"] }).ok, true);
  for (const v of [0, 5, 15, 30, 60]) assert.equal(checkPatch(SETTINGS_DEFAULTS, { screensaver_change_min: v }).ok, true);
  for (const v of [1, 10, 15.5, "15", true, null]) assert.equal(checkPatch(SETTINGS_DEFAULTS, { screensaver_change_min: v }).ok, false);
  for (const v of [[], ["aurora", "aurora"], ["clock", "aurora"], ["nope"], "aurora", [null]]) {
    assert.equal(checkPatch(SETTINGS_DEFAULTS, { screensaver_styles: v }).ok, false, JSON.stringify(v));
  }
});

test("parsePanelSettings reads an old single style as a one-item list", () => {
  const old = { version: 1, panels: { panel: { screensaver_style: "aurora", brightness_pct: 70 } } };
  const got = parsePanelSettings(old, "panel");
  assert.deepEqual(got.screensaver_styles, ["aurora"]);
  assert.equal(got.brightness_pct, 70);
  assert.equal("screensaver_style" in got, false);
  assert.deepEqual(parsePanelSettings(doc({ screensaver_styles: ["clock"] }), "panel").screensaver_styles, ["clock"]);
  assert.deepEqual(parsePanelSettings(doc({ screensaver_styles: ["clock", "aurora"] }), "panel").screensaver_styles, ["aurora"]);
  assert.equal(parsePanelSettings(doc({ screensaver_change_min: 30 }), "panel").screensaver_change_min, 30);
  assert.equal(parsePanelSettings(doc({ screensaver_change_min: 7 }), "panel").screensaver_change_min, 15);
});

test("the More tile line names one style or counts several", () => {
  assert.equal(tileLines({ ...SETTINGS_DEFAULTS, screensaver_styles: ["clock"] }).saver, "Clock after 1 min");
  assert.equal(tileLines({ ...SETTINGS_DEFAULTS, screensaver_styles: ["aurora", "clock"], screensaver_wait_s: 300 }).saver, "2 styles after 5 min");
  assert.equal(tileLines({ ...SETTINGS_DEFAULTS, screensaver_on: false }).saver, "Off");
});

test("stylesView lists the shipped styles in registry order with checks", () => {
  const view = stylesView({ ...SETTINGS_DEFAULTS, screensaver_styles: ["aurora", "clock"] });
  assert.deepEqual(view.tiles.map((t) => t.id), ["aurora", "facts", "quotes", "covers", "clock", "sky", "planets", "moon", "flow", "contours", "life", "harmonograph"]);
  assert.deepEqual(view.tiles.map((t) => t.name), ["Aurora", "Weird facts", "Quotes", "Album covers", "Clock", "Night sky", "Planets", "Moon", "Flow field", "Contours", "Game of Life", "Harmonograph"]);
  assert.deepEqual(view.tiles.map((t) => t.on), [true, false, false, false, true, false, false, false, false, false, false, false]);
  assert.equal(view.columns, 4);
});

test("stylesView locks the last picked tile", () => {
  const one = stylesView({ ...SETTINGS_DEFAULTS, screensaver_styles: ["clock"] });
  assert.deepEqual(one.tiles.map((t) => [t.id, t.on, t.locked]), [["aurora", false, false], ["facts", false, false], ["quotes", false, false], ["covers", false, false], ["clock", true, true], ["sky", false, false], ["planets", false, false], ["moon", false, false], ["flow", false, false], ["contours", false, false], ["life", false, false], ["harmonograph", false, false]]);
  const two = stylesView({ ...SETTINGS_DEFAULTS, screensaver_styles: ["aurora", "clock"] });
  assert.deepEqual(two.tiles.map((t) => t.locked), Array(12).fill(false));
});

test("changeView hides or disables Change every with one pick and marks the choice", () => {
  const one = changeView({ ...SETTINGS_DEFAULTS, screensaver_styles: ["aurora"] });
  assert.equal(one.visible, false);
  const many = changeView({ ...SETTINGS_DEFAULTS, screensaver_styles: ["aurora", "clock"], screensaver_change_min: 0 });
  assert.equal(many.visible, true);
  assert.deepEqual(many.choices.map((c) => [c.label, c.value]), [["Off", 0], ["5 min", 5], ["15 min", 15], ["30 min", 30], ["60 min", 60]]);
});
