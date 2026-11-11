// Tests for the multi-frame helpers the page adds in Task 4 of the "several
// frames and a control panel" plan. Run with: node --test tests/js
//
// The system went from one frame to several (nightstand, office). These pure
// functions back the Frame screen's frame switcher and the per-frame requests,
// with no DOM and no network, as the rest of the room model is split:
//   - selectedFrameFrom: which frame the "#frame/<id>" hash selects, with the
//     first frame as the default and an unknown id falling back.
//   - frameList: the switcher rows (id, label, offline from a missing mirror).
//   - frameUrls: the per-frame mirror and history URLs (new "/frames/<id>/"
//     paths, or the old single paths during rollout).
//   - artCommandBody / framePinBody / frameUnpinBody: request bodies that always
//     carry the chosen frame id.
//   - frameQuietHoursBody: the per-frame quiet-hours override the Settings screen
//     posts.
//   - parseFrameControl: the new "frames" map, and the old single-frame document
//     falling back to a nightstand-only view (Review Focus 3).

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  selectedFrameFrom,
  frameList,
  frameUrls,
  artCommandBody,
  framePinBody,
  frameUnpinBody,
  frameQuietHoursBody,
  parseFrameControl,
  frameIdsFrom,
} from "../../roles/room_picker/files/site/rooms-model.js";

// --------------------------------------------------------------------------- #
// selectedFrameFrom: the "#frame/<id>" hash selects a frame, first by default
// --------------------------------------------------------------------------- #

const IDS = ["nightstand", "office"];

test("selectedFrameFrom reads the frame id from a #frame/<id> hash", () => {
  assert.equal(selectedFrameFrom("#frame/office", IDS), "office");
  assert.equal(selectedFrameFrom("#frame/nightstand", IDS), "nightstand");
  // A leading # is optional and case is ignored, like the screen router.
  assert.equal(selectedFrameFrom("frame/office", IDS), "office");
  assert.equal(selectedFrameFrom("#frame/OFFICE", IDS), "office");
});

test("selectedFrameFrom defaults to the first frame when the hash names none", () => {
  assert.equal(selectedFrameFrom("#frame", IDS), "nightstand");
  assert.equal(selectedFrameFrom("#listen", IDS), "nightstand");
  assert.equal(selectedFrameFrom("", IDS), "nightstand");
  assert.equal(selectedFrameFrom(null, IDS), "nightstand");
});

test("selectedFrameFrom keeps the current selection when another screen's hash names no frame", () => {
  // Navigating to #art must not lose the office the owner chose on the Frame
  // screen: the chosen frame persists so a scoped command still reaches it.
  assert.equal(selectedFrameFrom("#art", IDS, "office"), "office");
  assert.equal(selectedFrameFrom("#settings", IDS, "office"), "office");
  // A fresh #frame/<id> still wins over the remembered one.
  assert.equal(selectedFrameFrom("#frame/nightstand", IDS, "office"), "nightstand");
});

test("selectedFrameFrom falls back for an unknown or malformed id (Review Focus 5)", () => {
  // An id not in the inventory list is never selected, so the page never builds
  // a request for an arbitrary frame.
  assert.equal(selectedFrameFrom("#frame/kitchen", IDS), "nightstand");
  assert.equal(selectedFrameFrom("#frame/../etc", IDS), "nightstand");
  assert.equal(selectedFrameFrom("#frame/Office%20", IDS), "nightstand");
  // An unknown id with a valid current keeps the current rather than jumping.
  assert.equal(selectedFrameFrom("#frame/kitchen", IDS, "office"), "office");
});

test("selectedFrameFrom returns null when there are no frames", () => {
  assert.equal(selectedFrameFrom("#frame/office", []), null);
  assert.equal(selectedFrameFrom("#frame", []), null);
});

// --------------------------------------------------------------------------- #
// frameList: the switcher rows, with an offline flag from a missing mirror
// --------------------------------------------------------------------------- #

test("frameList lists the frames in order with a human label", () => {
  const control = parseFrameControl({
    settings: {},
    frames: { nightstand: { quiet_hours: null, pin: null }, office: { quiet_hours: null, pin: null } },
  });
  const rows = frameList(control, {});
  assert.deepEqual(
    rows.map((r) => [r.id, r.label]),
    [["nightstand", "Nightstand"], ["office", "Office"]]
  );
});

test("frameList marks a frame offline when its mirror is missing", () => {
  const control = parseFrameControl({
    settings: {},
    frames: { nightstand: { quiet_hours: null, pin: null }, office: { quiet_hours: null, pin: null } },
  });
  // The nightstand has a mirror (a parsed frame.json); the office does not yet
  // (its Inky has not arrived), so it is shown offline and the other is not.
  const rows = frameList(control, {
    nightstand: { updated_at: "2026-11-11T09:00:00-08:00", screen: "art" },
    office: null,
  });
  const byId = Object.fromEntries(rows.map((r) => [r.id, r.offline]));
  assert.equal(byId.nightstand, false);
  assert.equal(byId.office, true);
});

test("frameList treats an absent or malformed mirror entry as offline", () => {
  const control = parseFrameControl({
    settings: {},
    frames: { office: { quiet_hours: null, pin: null } },
  });
  for (const mirrors of [{}, { office: undefined }, { office: 42 }, { office: [] }]) {
    assert.equal(frameList(control, mirrors)[0].offline, true, JSON.stringify(mirrors));
  }
});

test("frameList titlecases a hyphenated id", () => {
  const control = parseFrameControl({
    settings: {},
    frames: { "guest-room": { quiet_hours: null, pin: null } },
  });
  assert.equal(frameList(control, {})[0].label, "Guest room");
});

// --------------------------------------------------------------------------- #
// frameUrls: the per-frame mirror and history URLs
// --------------------------------------------------------------------------- #

test("frameUrls builds the per-frame paths under /frames/<id>/", () => {
  assert.deepEqual(frameUrls("office", false), {
    history: "frames/office/art-history.json",
    image: "frames/office/frame.png",
    meta: "frames/office/frame.json",
  });
});

test("frameUrls uses the old single paths in legacy mode (rollout, Review Focus 3)", () => {
  // On the old single-frame contract the hub only serves the nightstand's copies
  // at the old paths, so the fallback nightstand view reads those.
  assert.deepEqual(frameUrls("nightstand", true), {
    history: "art-history.json",
    image: "frame.png",
    meta: "frame.json",
  });
});

test("frameUrls returns null for a malformed id so no arbitrary path is built (Review Focus 5)", () => {
  for (const bad of ["../etc", "Office", "a/b", "", ".hidden", 42, null, "x".repeat(40)]) {
    assert.equal(frameUrls(bad, false), null, JSON.stringify(bad));
  }
});

// --------------------------------------------------------------------------- #
// Request bodies: every write carries the chosen frame id
// --------------------------------------------------------------------------- #

test("artCommandBody carries the frame and only a piece for show", () => {
  assert.deepEqual(artCommandBody("office", "next"), { action: "next", frame: "office" });
  assert.deepEqual(artCommandBody("office", "previous"), { action: "previous", frame: "office" });
  assert.deepEqual(artCommandBody("office", "show", "Collection/a.jpg"), {
    action: "show",
    piece: "Collection/a.jpg",
    frame: "office",
  });
});

test("artCommandBody returns null for an unknown action, a show with no piece, or a bad frame", () => {
  assert.equal(artCommandBody("office", "jump"), null);
  assert.equal(artCommandBody("office", "show"), null);
  assert.equal(artCommandBody("office", "show", ""), null);
  assert.equal(artCommandBody("../etc", "next"), null);
  assert.equal(artCommandBody("", "next"), null);
});

test("framePinBody carries the frame with a valid what and duration", () => {
  assert.deepEqual(framePinBody("office", "art", "1h"), {
    frame: "office",
    what: "art",
    duration: "1h",
  });
  assert.deepEqual(framePinBody("nightstand", "weather", "forever"), {
    frame: "nightstand",
    what: "weather",
    duration: "forever",
  });
});

test("framePinBody returns null for a bad frame, what, or duration", () => {
  assert.equal(framePinBody("office", "lamp", "1h"), null);
  assert.equal(framePinBody("office", "art", "2h"), null);
  assert.equal(framePinBody("../etc", "art", "1h"), null);
  assert.equal(framePinBody("", "art", "1h"), null);
});

test("frameUnpinBody carries the frame, or null for a bad one", () => {
  assert.deepEqual(frameUnpinBody("office"), { frame: "office" });
  assert.equal(frameUnpinBody("../etc"), null);
  assert.equal(frameUnpinBody(""), null);
  assert.equal(frameUnpinBody(null), null);
});

// --------------------------------------------------------------------------- #
// frameQuietHoursBody: the per-frame quiet-hours override the Settings posts
// --------------------------------------------------------------------------- #

test("frameQuietHoursBody posts null to fall back to the shared quiet hours", () => {
  assert.deepEqual(frameQuietHoursBody("office", null), {
    ok: true,
    value: { frame: "office", quiet_hours: null },
  });
});

test("frameQuietHoursBody posts {enabled:false} for a frame that keeps no quiet hours", () => {
  assert.deepEqual(frameQuietHoursBody("office", { enabled: false }), {
    ok: true,
    value: { frame: "office", quiet_hours: { enabled: false } },
  });
});

test("frameQuietHoursBody posts a validated custom window", () => {
  assert.deepEqual(frameQuietHoursBody("nightstand", { start: "23:00", end: "07:00" }), {
    ok: true,
    value: { frame: "nightstand", quiet_hours: { start: "23:00", end: "07:00" } },
  });
});

test("frameQuietHoursBody rejects a malformed custom time with a message", () => {
  const result = frameQuietHoursBody("office", { start: "7:30", end: "06:00" });
  assert.equal(result.ok, false);
  assert.match(result.error, /time/i);
});

test("frameQuietHoursBody rejects a bad frame id", () => {
  const result = frameQuietHoursBody("../etc", null);
  assert.equal(result.ok, false);
});

// --------------------------------------------------------------------------- #
// parseFrameControl: the new "frames" map and the old-contract fallback
// --------------------------------------------------------------------------- #

test("parseFrameControl reads the new frames map, with per-frame quiet hours and pins", () => {
  const result = parseFrameControl({
    settings: { now_playing_kinds: ["music"] },
    frames: {
      nightstand: { quiet_hours: null, pin: null },
      office: {
        quiet_hours: { enabled: false },
        pin: { what: "art", piece: "a.jpg", until: null, seq: 2, set_at: "2026-11-11T09:00:00-08:00" },
      },
    },
  });
  assert.equal(result.available, true);
  assert.equal(result.legacy, false);
  assert.deepEqual(frameIdsFrom(result), ["nightstand", "office"]);
  assert.equal(result.frames.nightstand.quietHours, null);
  assert.deepEqual(result.frames.office.quietHours, { enabled: false });
  assert.equal(result.frames.office.pin.what, "art");
  assert.equal(result.frames.nightstand.pin, null);
});

test("parseFrameControl reads a per-frame quiet-hours window", () => {
  const result = parseFrameControl({
    settings: {},
    frames: { office: { quiet_hours: { start: "22:30", end: "06:30" }, pin: null } },
  });
  assert.deepEqual(result.frames.office.quietHours, { start: "22:30", end: "06:30" });
});

test("parseFrameControl falls back to a nightstand-only view on the old contract (Review Focus 3)", () => {
  // An old single-frame document has a top-level pin and no frames map. The page
  // must not crash: it shows just the nightstand, keeping the old pin, and reads
  // the old single mirror paths (legacy).
  const result = parseFrameControl({
    settings: { now_playing_kinds: ["music"] },
    pin: { what: "weather", until: null, seq: 1 },
  });
  assert.equal(result.available, true);
  assert.equal(result.legacy, true);
  assert.deepEqual(frameIdsFrom(result), ["nightstand"]);
  assert.equal(result.frames.nightstand.pin.what, "weather");
  assert.equal(result.frames.nightstand.quietHours, null);
});

test("parseFrameControl reads a missing document as unavailable but still a nightstand-only view", () => {
  const result = parseFrameControl(null);
  assert.equal(result.available, false);
  assert.equal(result.legacy, true);
  assert.deepEqual(frameIdsFrom(result), ["nightstand"]);
  assert.equal(result.frames.nightstand.pin, null);
});

test("parseFrameControl drops a malformed frames map back to the nightstand fallback", () => {
  for (const bad of [{ settings: {}, frames: [] }, { settings: {}, frames: 42 }, { settings: {}, frames: "x" }]) {
    const result = parseFrameControl(bad);
    assert.deepEqual(frameIdsFrom(result), ["nightstand"], JSON.stringify(bad));
    assert.equal(result.legacy, true);
  }
});

test("parseFrameControl skips a malformed frame id in the map (Review Focus 5)", () => {
  const result = parseFrameControl({
    settings: {},
    frames: {
      office: { quiet_hours: null, pin: null },
      "../evil": { quiet_hours: null, pin: null },
      "": { quiet_hours: null, pin: null },
    },
  });
  assert.deepEqual(frameIdsFrom(result), ["office"]);
});
