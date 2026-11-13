// Tests for the frame-control helpers the screen-based room picker adds. Run
// with: node --test tests/js
//
// These cover the pure functions behind the new screens and the frame-control
// API, with no DOM and no network, as the rest of the room model is split:
//   - screenFromHash: the hash router (Listen default, unknown hash falls back).
//   - parseFrameControl: the hub's /frame-control.json, with "unavailable" for a
//     missing or malformed document (settings shown as unknown, never wrong).
//   - pinSummary: the one-line "Pinned: ... until ..." the Frame screen shows.
//   - validateFrameSettings: the Settings form validation that mirrors the hub.
//   - framePinBody / frameUnpinBody: the request bodies the new API takes.

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  screenFromHash,
  parseFrameControl,
  frameControlAfterFetch,
  pinSummary,
  validateFrameSettings,
  framePinBody,
  frameUnpinBody,
  weatherLocationLabel,
  weatherSaveBody,
  weatherClearBody,
} from "../../roles/room_picker/files/site/rooms-model.js";

// --------------------------------------------------------------------------- #
// screenFromHash: the hash router
// --------------------------------------------------------------------------- #

test("screenFromHash maps each known hash to its screen", () => {
  assert.equal(screenFromHash("#listen"), "listen");
  assert.equal(screenFromHash("#frame"), "frame");
  assert.equal(screenFromHash("#art"), "gallery");
  assert.equal(screenFromHash("#gallery"), "gallery");
  assert.equal(screenFromHash("#climate"), "climate");
  assert.equal(screenFromHash("#settings"), "settings");
});

test("screenFromHash reads a #frame/<id> hash as the Frame screen", () => {
  // The chosen frame is carried in the hash as "#frame/office"; the segment
  // after the screen name must not throw the router off the Frame screen.
  assert.equal(screenFromHash("#frame/office"), "frame");
  assert.equal(screenFromHash("#frame/nightstand"), "frame");
  assert.equal(screenFromHash("frame/office"), "frame");
});

test("screenFromHash defaults to listen for an empty or bare hash", () => {
  assert.equal(screenFromHash(""), "listen");
  assert.equal(screenFromHash("#"), "listen");
  assert.equal(screenFromHash(null), "listen");
  assert.equal(screenFromHash(undefined), "listen");
});

test("screenFromHash falls back to listen for an unknown hash", () => {
  assert.equal(screenFromHash("#rooms"), "listen");
  assert.equal(screenFromHash("#frame-control"), "listen");
  assert.equal(screenFromHash("#__proto__"), "listen");
});

test("screenFromHash accepts a hash without the leading # and ignores case", () => {
  assert.equal(screenFromHash("frame"), "frame");
  assert.equal(screenFromHash("#Settings"), "settings");
  assert.equal(screenFromHash("ART"), "gallery");
});

// --------------------------------------------------------------------------- #
// parseFrameControl: the hub's /frame-control.json
// --------------------------------------------------------------------------- #

test("parseFrameControl reports unavailable for a missing or malformed document", () => {
  for (const bad of [null, undefined, "", "not json", 42, [], true]) {
    const result = parseFrameControl(bad);
    assert.equal(result.available, false, JSON.stringify(bad));
    assert.equal(result.settings, null);
  }
});

test("parseFrameControl fills documented defaults for a present but empty settings object", () => {
  const result = parseFrameControl({ settings: {}, pin: null });
  assert.equal(result.available, true);
  assert.deepEqual(result.settings, {
    now_playing_kinds: ["music"],
    quiet_hours: { start: "22:00", end: "06:00" },
    morning_weather: {
      enabled: true,
      weekday: ["06:00", "08:30"],
      weekend: ["07:00", "09:00"],
    },
    weather_location: null,
  });
  // An old-contract document (no frames map) reads as the nightstand with no pin.
  assert.equal(result.frames.nightstand.pin, null);
});

test("parseFrameControl keeps present settings values", () => {
  const result = parseFrameControl({
    settings: {
      now_playing_kinds: ["music", "podcast"],
      quiet_hours: { start: "23:00", end: "07:00" },
      morning_weather: {
        enabled: false,
        weekday: ["06:30", "08:00"],
        weekend: ["08:00", "10:00"],
      },
    },
    pin: null,
  });
  assert.deepEqual(result.settings.now_playing_kinds, ["music", "podcast"]);
  assert.deepEqual(result.settings.quiet_hours, { start: "23:00", end: "07:00" });
  assert.equal(result.settings.morning_weather.enabled, false);
  assert.deepEqual(result.settings.morning_weather.weekday, ["06:30", "08:00"]);
});

test("parseFrameControl drops unknown kinds and a malformed time back to the default", () => {
  const result = parseFrameControl({
    settings: {
      now_playing_kinds: ["music", "video", "podcast"],
      quiet_hours: { start: "nope", end: "07:00" },
    },
    pin: null,
  });
  assert.deepEqual(result.settings.now_playing_kinds, ["music", "podcast"]);
  // A malformed start falls back to the whole default quiet-hours window.
  assert.deepEqual(result.settings.quiet_hours, { start: "22:00", end: "06:00" });
});

test("parseFrameControl parses an active pin and rejects a bad one", () => {
  const good = parseFrameControl({
    settings: {},
    pin: {
      what: "weather",
      piece: null,
      until: "2026-11-10T16:12:00-08:00",
      seq: 3,
      set_at: "2026-11-10T14:00:00-08:00",
    },
  });
  // The old single-frame document's top-level pin becomes the nightstand's pin.
  assert.deepEqual(good.frames.nightstand.pin, {
    what: "weather",
    piece: null,
    until: "2026-11-10T16:12:00-08:00",
    seq: 3,
    setAt: "2026-11-10T14:00:00-08:00",
  });
  // An unknown "what" is not a pin the page can show, so it reads as no pin.
  const bad = parseFrameControl({ settings: {}, pin: { what: "lamp", seq: 1 } });
  assert.equal(bad.frames.nightstand.pin, null);
});

// --------------------------------------------------------------------------- #
// frameControlAfterFetch: a failed read never falls back to the form defaults
// --------------------------------------------------------------------------- #

const GOOD_DOC = { settings: {}, pin: null };

test("frameControlAfterFetch reads a 404 (null json, no throw) as unavailable", () => {
  const result = frameControlAfterFetch(null, { threw: false, json: null });
  assert.equal(result.available, false);
});

test("frameControlAfterFetch reads a malformed body (null json, no throw) as unavailable", () => {
  // safeJsonParse hands a malformed body back as null, same as a 404.
  const result = frameControlAfterFetch(null, { threw: false, json: null });
  assert.equal(result.available, false);
});

test("frameControlAfterFetch reads a network throw with no prior state as unavailable", () => {
  const result = frameControlAfterFetch(null, { threw: true, json: null });
  assert.equal(result.available, false);
});

test("frameControlAfterFetch keeps the last-good document when a later fetch throws", () => {
  const good = frameControlAfterFetch(null, { threw: false, json: GOOD_DOC });
  assert.equal(good.available, true);
  const afterThrow = frameControlAfterFetch(good, { threw: true, json: null });
  assert.equal(afterThrow, good);
});

test("frameControlAfterFetch parses a good document into the available state", () => {
  const result = frameControlAfterFetch(null, { threw: false, json: GOOD_DOC });
  assert.equal(result.available, true);
  assert.ok(result.settings);
});

// --------------------------------------------------------------------------- #
// pinSummary: the Frame screen's one-line pin status
// --------------------------------------------------------------------------- #

const NOW = Date.parse("2026-11-10T14:00:00-08:00");

test("pinSummary names what is pinned and its end time", () => {
  assert.equal(
    pinSummary({ what: "weather", until: "2026-11-10T16:12:00-08:00" }, NOW),
    "Pinned: weather until 4:12"
  );
  assert.equal(
    pinSummary({ what: "art", until: "2026-11-10T16:12:00-08:00" }, NOW),
    "Pinned: this art piece until 4:12"
  );
  assert.equal(
    pinSummary({ what: "now_playing", until: "2026-11-10T16:12:00-08:00" }, NOW),
    "Pinned: now playing until 4:12"
  );
});

test("pinSummary says so plainly when pinned until unpinned", () => {
  assert.equal(
    pinSummary({ what: "weather", until: null }, NOW),
    "Pinned: weather until you unpin"
  );
});

test("pinSummary returns null for no pin or an expired pin", () => {
  assert.equal(pinSummary(null, NOW), null);
  assert.equal(pinSummary(undefined, NOW), null);
  // Already past: the hub returns to its schedule, so the page shows no pin
  // rather than a stale one (Review Focus 1: unknown, never wrong).
  assert.equal(pinSummary({ what: "weather", until: "2026-11-10T13:00:00-08:00" }, NOW), null);
});

test("pinSummary omits the time when a present end time will not parse", () => {
  assert.equal(pinSummary({ what: "weather", until: "nonsense" }, NOW), "Pinned: weather");
});

// --------------------------------------------------------------------------- #
// validateFrameSettings: the Settings form validation, mirroring the hub
// --------------------------------------------------------------------------- #

test("validateFrameSettings accepts a valid kinds subset", () => {
  assert.deepEqual(validateFrameSettings({ now_playing_kinds: ["music", "audiobook"] }), {
    ok: true,
    value: { now_playing_kinds: ["music", "audiobook"] },
  });
  // No kinds means nothing takes the frame; that is allowed.
  assert.deepEqual(validateFrameSettings({ now_playing_kinds: [] }), {
    ok: true,
    value: { now_playing_kinds: [] },
  });
});

test("validateFrameSettings rejects an unknown kind", () => {
  const result = validateFrameSettings({ now_playing_kinds: ["music", "video"] });
  assert.equal(result.ok, false);
  assert.match(result.error, /kind/i);
});

test("validateFrameSettings accepts valid quiet hours, including a window that wraps midnight", () => {
  assert.deepEqual(validateFrameSettings({ quiet_hours: { start: "22:00", end: "06:00" } }), {
    ok: true,
    value: { quiet_hours: { start: "22:00", end: "06:00" } },
  });
});

test("validateFrameSettings rejects a malformed time", () => {
  for (const bad of ["7:30", "24:00", "22:60", "22-00", "", "noon"]) {
    const result = validateFrameSettings({ quiet_hours: { start: bad, end: "06:00" } });
    assert.equal(result.ok, false, bad);
    assert.match(result.error, /time/i);
  }
});

test("validateFrameSettings accepts morning weather whose windows start before end", () => {
  const input = {
    morning_weather: {
      enabled: true,
      weekday: ["06:00", "08:30"],
      weekend: ["07:00", "09:00"],
    },
  };
  assert.deepEqual(validateFrameSettings(input), { ok: true, value: input });
});

test("validateFrameSettings rejects a morning window whose start is not before its end", () => {
  for (const window of [["08:30", "06:00"], ["08:00", "08:00"]]) {
    const result = validateFrameSettings({
      morning_weather: { enabled: true, weekday: window, weekend: ["07:00", "09:00"] },
    });
    assert.equal(result.ok, false, JSON.stringify(window));
    assert.match(result.error, /before/i);
  }
});

test("validateFrameSettings accepts an empty subset", () => {
  assert.deepEqual(validateFrameSettings({}), { ok: true, value: {} });
});

// --------------------------------------------------------------------------- #
// framePinBody / frameUnpinBody: the request bodies the new API takes
// --------------------------------------------------------------------------- #

test("framePinBody builds a body for a valid frame, what, and duration", () => {
  assert.deepEqual(framePinBody("nightstand", "art", "1h"), {
    frame: "nightstand",
    what: "art",
    duration: "1h",
  });
  assert.deepEqual(framePinBody("office", "weather", "4h"), {
    frame: "office",
    what: "weather",
    duration: "4h",
  });
  assert.deepEqual(framePinBody("nightstand", "now_playing", "morning"), {
    frame: "nightstand",
    what: "now_playing",
    duration: "morning",
  });
});

test("framePinBody returns null for an unknown what, duration, or frame", () => {
  assert.equal(framePinBody("office", "lamp", "1h"), null);
  assert.equal(framePinBody("office", "art", "2h"), null);
  assert.equal(framePinBody("office", "", "1h"), null);
  assert.equal(framePinBody("office", "art", ""), null);
  assert.equal(framePinBody("../etc", "art", "1h"), null);
  assert.equal(framePinBody(null, null, null), null);
});

test("frameUnpinBody carries the frame the guarded POST acts on", () => {
  assert.deepEqual(frameUnpinBody("office"), { frame: "office" });
});

// --------------------------------------------------------------------------- #
// Weather location: the Settings screen's "Weather city" field
// --------------------------------------------------------------------------- #

const SAN_DIEGO = {
  name: "San Diego, California, United States",
  latitude: 32.72,
  longitude: -117.16,
  timezone: "America/Los_Angeles",
};

test("parseFrameControl defaults weather_location to null", () => {
  const result = parseFrameControl({ settings: {}, pin: null });
  assert.equal(result.settings.weather_location, null);
});

test("parseFrameControl keeps a valid stored weather location", () => {
  const result = parseFrameControl({ settings: { weather_location: SAN_DIEGO }, pin: null });
  assert.deepEqual(result.settings.weather_location, SAN_DIEGO);
});

test("parseFrameControl reads a malformed weather location as null", () => {
  for (const bad of [{ name: "X" }, { name: "X", latitude: "nope", longitude: 1, timezone: "Z" }, 42, []]) {
    const result = parseFrameControl({ settings: { weather_location: bad }, pin: null });
    assert.equal(result.settings.weather_location, null, JSON.stringify(bad));
  }
});

test("weatherLocationLabel shows the stored name or Not set", () => {
  assert.equal(weatherLocationLabel(SAN_DIEGO), "San Diego, California, United States");
  assert.equal(weatherLocationLabel(null), "Not set");
  assert.equal(weatherLocationLabel(undefined), "Not set");
  assert.equal(weatherLocationLabel({ latitude: 1 }), "Not set");
});

test("weatherSaveBody wraps a trimmed query, or null when blank", () => {
  assert.deepEqual(weatherSaveBody("San Diego, CA"), {
    weather_location: { query: "San Diego, CA" },
  });
  assert.deepEqual(weatherSaveBody("  Portland, OR  "), {
    weather_location: { query: "Portland, OR" },
  });
  assert.equal(weatherSaveBody("   "), null);
  assert.equal(weatherSaveBody(""), null);
  assert.equal(weatherSaveBody(null), null);
  assert.equal(weatherSaveBody(42), null);
});

test("weatherClearBody clears the location", () => {
  assert.deepEqual(weatherClearBody(), { weather_location: null });
});
