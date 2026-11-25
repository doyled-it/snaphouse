import { test } from "node:test";
import assert from "node:assert/strict";
import {
  CHANGE_CHOICES,
  SHIPPED_IDS,
  STYLES,
  checkStyles,
  nextPick,
  tappedPreviewId,
  shippedPicks,
  styleName,
  stylesLabel,
  togglePick,
} from "../../roles/room_picker/files/site/style-list.js";

const ORDER = ["aurora", "facts", "quotes", "covers", "clock", "sky", "planets", "moon", "flow", "contours", "life", "harmonograph"];

test("the registry order is the twelve fixed ids", () => {
  assert.deepEqual(STYLES.map((s) => s.id), ORDER);
  assert.equal(styleName("clock"), "Clock");
  assert.equal(styleName("aurora"), "Aurora");
  assert.equal(styleName("harmonograph"), "Harmonograph");
});

test("Aurora, Weird facts, Quotes and Clock are shipped in this PR", () => {
  assert.deepEqual([...SHIPPED_IDS], ["aurora", "facts", "quotes", "covers", "clock", "sky", "planets", "moon", "flow", "contours", "life", "harmonograph"]);
});

test("change choices are Off, 5, 15, 30, 60", () => {
  assert.deepEqual(CHANGE_CHOICES, [0, 5, 15, 30, 60]);
});

test("checkStyles mirrors the hub: non-empty, known ids, registry order, no duplicates", () => {
  assert.equal(checkStyles(["aurora"]), true);
  assert.equal(checkStyles(["clock", "life"]), true);
  assert.equal(checkStyles(ORDER), true);
  for (const bad of [[], ["nope"], ["clock", "aurora"], ["aurora", "aurora"], "aurora", null, [1], ["aurora", null], undefined]) {
    assert.equal(checkStyles(bad), false, JSON.stringify(bad));
  }
});

test("shippedPicks drops ids with no module and falls back to Aurora", () => {
  assert.deepEqual(shippedPicks(["aurora", "nope", "clock"]), ["aurora", "clock"]);
  assert.deepEqual(shippedPicks(["nope"]), ["aurora"]);
  assert.deepEqual(shippedPicks([]), ["aurora"]);
  assert.deepEqual(shippedPicks(undefined), ["aurora"]);
});

test("nextPick starts at the first pick and moves on after the last shown", () => {
  assert.equal(nextPick(["aurora", "clock"], null), "aurora");
  assert.equal(nextPick(["aurora", "clock"], "aurora"), "clock");
  assert.equal(nextPick(["aurora", "clock"], "clock"), "aurora");
  assert.equal(nextPick(["clock"], "clock"), "clock");
});

test("nextPick skips ids with no module and handles a last id that is no longer picked", () => {
  assert.equal(nextPick(["aurora", "nope", "clock"], "aurora"), "clock");
  assert.equal(nextPick(["nope"], null), "aurora");
  // clock was shown, then the owner unpicked it: continue after it in registry order
  assert.equal(nextPick(["aurora", "moon"], "clock"), "moon");
  assert.equal(nextPick(["aurora", "clock"], "aurora"), "clock");
  assert.equal(nextPick(["aurora", "flow"], "moon"), "flow");
  assert.equal(nextPick(["aurora", "clock"], "moon"), "aurora");
});

test("togglePick adds in registry order and removes", () => {
  assert.deepEqual(togglePick(["clock"], "aurora"), { ok: true, picks: ["aurora", "clock"] });
  assert.deepEqual(togglePick(["aurora", "clock"], "aurora"), { ok: true, picks: ["clock"] });
});

test("the last picked shipped tile cannot be turned off", () => {
  const got = togglePick(["aurora"], "aurora");
  assert.equal(got.ok, false);
  assert.deepEqual(got.picks, ["aurora"]);
  assert.equal(togglePick(["aurora", "nope"], "aurora").ok, false);
});

test("a style with no module cannot be toggled", () => {
  assert.equal(togglePick(["aurora"], "nope").ok, false);
});

test("stylesLabel names one pick and counts several", () => {
  assert.equal(stylesLabel(["aurora"]), "Aurora");
  assert.equal(stylesLabel(["clock"]), "Clock");
  assert.equal(stylesLabel(["aurora", "clock"]), "2 styles");
  assert.equal(stylesLabel(["nope"]), "Aurora");
});

test("Preview on the Styles screen shows the style last tapped, else the first pick", () => {
  assert.equal(tappedPreviewId(["aurora", "clock"], "clock"), "clock");
  assert.equal(tappedPreviewId(["aurora", "clock"], null), "aurora");
  assert.equal(tappedPreviewId(["clock"], null), "clock");
  assert.equal(tappedPreviewId(["aurora"], "nope"), "aurora");
  assert.equal(tappedPreviewId(["nope"], undefined), "aurora");
});
