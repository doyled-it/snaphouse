// Tests for the "Pair a phone" model helpers the Settings screen uses. Run with:
//   node --test tests/js
//
// These cover the pure functions behind the pairing window, with no DOM and no
// network, the same split as the rest of the room model:
//   - parsePairState: read GET /api/pair/state, tolerating malformed or missing
//     fields. The page never shows a code (the hub answers the first phone
//     itself), so a passkey is always dropped.
//   - pairStartRequest / pairStopRequest: the POST requests,
//     each carrying the X-Snaphouse guard header and the right JSON body.
//   - interpretPairResponse: map an HTTP status and body to a result, including
//     403 (guard) and 409 (window already open).
//   - pairCountdown / formatCountdown: seconds left until a deadline, and mm:ss.
//   - pairPhase: reduce a parsed state plus "did we start a window" to a phase,
//     so a window that times out back to idle reads as closed, not as the start.
//   - pairInstruction / shouldPollPair: the phone instruction (with its speaker
//     fallback) and when the page is allowed to poll.

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  parsePairState,
  pairStartRequest,
  pairStopRequest,
  interpretPairResponse,
  pairCountdown,
  formatCountdown,
  pairPhase,
  pairInstruction,
  pairProgress,
  pairOutcomeView,
  shouldPollPair,
  pairLostContact,
  MAX_PAIR_POLL_FAILURES,
  pairControlsDisabled,
  pairCountdownCue,
} from "../../roles/room_picker/files/site/rooms-model.js";

// --------------------------------------------------------------------------- #
// parsePairState: read the state document
// --------------------------------------------------------------------------- #

const IDLE = { state: "idle", until: null, passkey: null, device: null, message: null };

test("parsePairState reads an open window", () => {
  const got = parsePairState(
    JSON.stringify({ state: "open", until: "2026-11-12T10:02:00Z", passkey: null, device: null, message: null }),
  );
  assert.deepEqual(got, {
    state: "open",
    until: "2026-11-12T10:02:00Z",
    passkey: null,
    device: null,
    message: null,
  });
});

test("parsePairState never keeps a passkey: the page shows no code", () => {
  // The service may still report one, but the hub answers the first phone itself,
  // so the page has nothing to compare and must not display it.
  for (const state of ["open", "paired", "failed"]) {
    const got = parsePairState(JSON.stringify({ state, passkey: "123456", device: "Pixel 8" }));
    assert.equal(got.passkey, null, `${state} must drop the passkey`);
  }
});

test("parsePairState reads the removed confirm and declined states as idle", () => {
  for (const state of ["confirm", "declined"]) {
    assert.deepEqual(parsePairState(JSON.stringify({ state, passkey: "123456" })), IDLE);
  }
});

test("parsePairState carries the device and message as plain strings", () => {
  const paired = parsePairState(JSON.stringify({ state: "paired", device: "Pixel 8", message: null }));
  assert.equal(paired.device, "Pixel 8");
  const failed = parsePairState(JSON.stringify({ state: "failed", message: "The adapter went away." }));
  assert.equal(failed.message, "The adapter went away.");
  // Non-string or empty device/message collapse to null.
  const odd = parsePairState(JSON.stringify({ state: "paired", device: "", message: 7 }));
  assert.equal(odd.device, null);
  assert.equal(odd.message, null);
});

test("parsePairState falls back to idle on an unknown or missing state", () => {
  assert.deepEqual(parsePairState(JSON.stringify({ state: "weird" })), IDLE);
  assert.deepEqual(parsePairState(JSON.stringify({})), IDLE);
  assert.deepEqual(parsePairState(JSON.stringify({ state: 5 })), IDLE);
});

test("parsePairState falls back to idle on malformed or empty input", () => {
  for (const bad of ["", "not json", "[1,2,3]", "null", "42", undefined, null, 12]) {
    assert.deepEqual(parsePairState(bad), IDLE, `input ${JSON.stringify(bad)} should be idle`);
  }
});

test("parsePairState drops a non-string until", () => {
  assert.equal(parsePairState(JSON.stringify({ state: "open", until: 12345 })).until, null);
  assert.equal(parsePairState(JSON.stringify({ state: "open", until: "" })).until, null);
});

// --------------------------------------------------------------------------- #
// The POST requests: each carries the guard header and the right body
// --------------------------------------------------------------------------- #

test("pairStartRequest posts to start with the guard header and an empty body", () => {
  const { url, init } = pairStartRequest();
  assert.equal(url, "/api/pair/start");
  assert.equal(init.method, "POST");
  assert.equal(init.headers["X-Snaphouse"], "1");
  assert.equal(init.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(init.body), {});
});

test("pairStopRequest posts to stop with the guard header", () => {
  const { url, init } = pairStopRequest();
  assert.equal(url, "/api/pair/stop");
  assert.equal(init.method, "POST");
  assert.equal(init.headers["X-Snaphouse"], "1");
  assert.deepEqual(JSON.parse(init.body), {});
});

// --------------------------------------------------------------------------- #
// interpretPairResponse: status and body to a result, including 403 and 409
// --------------------------------------------------------------------------- #

test("interpretPairResponse reads a new state on 200 and 202", () => {
  const started = interpretPairResponse(202, JSON.stringify({ state: "open", until: "2026-11-12T10:02:00Z" }));
  assert.equal(started.ok, true);
  assert.equal(started.state.state, "open");
  assert.equal(started.state.until, "2026-11-12T10:02:00Z");

  const stopped = interpretPairResponse(200, JSON.stringify({ state: "idle" }));
  assert.equal(stopped.ok, true);
  assert.equal(stopped.state.state, "idle");
});

test("interpretPairResponse flags a 409 on start as the window already being open", () => {
  // The contract gives a start 409 only for "already open"; the raw lowercase
  // service string is mapped to plain page copy rather than shown verbatim (M1).
  const got = interpretPairResponse(409, JSON.stringify({ error: "already open" }), "start");
  assert.equal(got.ok, false);
  assert.equal(got.conflict, true);
  assert.equal(got.error, "The pairing window is already open.");

  // No endpoint given defaults to the start copy.
  const bare = interpretPairResponse(409, "");
  assert.equal(bare.conflict, true);
  assert.equal(bare.error, "The pairing window is already open.");
});

test("interpretPairResponse flags a 403 from the guard", () => {
  const got = interpretPairResponse(403, "");
  assert.equal(got.ok, false);
  assert.equal(got.forbidden, true);
  assert.ok(got.error && got.error.length > 0);
});

test("interpretPairResponse reports a 400 and other failures with a fallback message", () => {
  const bad = interpretPairResponse(400, JSON.stringify({ error: "malformed body" }));
  assert.equal(bad.ok, false);
  assert.equal(bad.error, "malformed body");

  const server = interpretPairResponse(500, "");
  assert.equal(server.ok, false);
  assert.ok(server.error && server.error.length > 0);

  // A body that will not parse still yields a usable error string.
  const junk = interpretPairResponse(500, "<html>502</html>");
  assert.equal(junk.ok, false);
  assert.ok(junk.error && junk.error.length > 0);
});

// --------------------------------------------------------------------------- #
// pairCountdown / formatCountdown: the countdown math
// --------------------------------------------------------------------------- #

test("pairCountdown returns whole seconds left until the deadline", () => {
  const now = Date.parse("2026-11-12T10:00:00Z");
  assert.equal(pairCountdown("2026-11-12T10:02:00Z", now), 120);
  assert.equal(pairCountdown("2026-11-12T10:00:30Z", now), 30);
  // A fractional second left rounds up, so the last tick still shows 1, not 0.
  assert.equal(pairCountdown("2026-11-12T10:00:00.400Z", now), 1);
});

test("pairCountdown clamps a passed deadline to zero", () => {
  const now = Date.parse("2026-11-12T10:00:00Z");
  assert.equal(pairCountdown("2026-11-12T09:59:59Z", now), 0);
  assert.equal(pairCountdown("2026-11-12T10:00:00Z", now), 0);
});

test("pairCountdown returns null when there is no usable deadline", () => {
  const now = Date.parse("2026-11-12T10:00:00Z");
  assert.equal(pairCountdown(null, now), null);
  assert.equal(pairCountdown("", now), null);
  assert.equal(pairCountdown("not a date", now), null);
  assert.equal(pairCountdown(undefined, now), null);
});

test("formatCountdown renders m:ss with a zero-padded seconds field", () => {
  assert.equal(formatCountdown(120), "2:00");
  assert.equal(formatCountdown(65), "1:05");
  assert.equal(formatCountdown(9), "0:09");
  assert.equal(formatCountdown(0), "0:00");
});

test("formatCountdown treats a missing or negative value as zero", () => {
  assert.equal(formatCountdown(null), "0:00");
  assert.equal(formatCountdown(-5), "0:00");
  assert.equal(formatCountdown(NaN), "0:00");
});

// --------------------------------------------------------------------------- #
// pairPhase: a parsed state plus whether we started reduces to a phase
// --------------------------------------------------------------------------- #

test("pairPhase passes through the live and terminal states", () => {
  for (const state of ["open", "paired", "failed"]) {
    assert.equal(pairPhase({ state }, { started: true }), state);
    assert.equal(pairPhase({ state }, { started: false }), state);
  }
});

test("pairPhase reads idle as the start before a window, and closed after one", () => {
  assert.equal(pairPhase({ state: "idle" }, { started: false }), "idle");
  assert.equal(pairPhase({ state: "idle" }, { started: true }), "closed");
});

// --------------------------------------------------------------------------- #
// pairInstruction / shouldPollPair
// --------------------------------------------------------------------------- #

test("pairInstruction tells the person to finish on the phone and come back", () => {
  assert.equal(
    pairInstruction("Living Room"),
    "Now open Bluetooth on this phone, tap Pair new device, choose Living Room, and tap Pair. You can stay there; come back here when it's done.",
  );
  for (const none of ["", "  ", null, undefined, 5]) {
    assert.equal(
      pairInstruction(none),
      "Now open Bluetooth on this phone, tap Pair new device, choose the house speaker, and tap Pair. You can stay there; come back here when it's done.",
    );
  }
});

test("pairProgress names the phone once it is being paired, and is silent before", () => {
  assert.equal(pairProgress({ state: "open", device: null }), null);
  assert.equal(pairProgress({ state: "open", device: "Pixel 8" }), "Pairing with Pixel 8.");
  assert.equal(pairProgress({ state: "paired", device: "Pixel 8" }), null);
});

test("pairOutcomeView reads paired, closed with nobody, and failed", () => {
  assert.deepEqual(pairOutcomeView("paired", { device: "Pixel 8", message: null }), {
    text: "Paired with Pixel 8.",
    detail: "It will reconnect on its own.",
    tone: "ok",
  });
  assert.equal(pairOutcomeView("paired", { device: null }).text, "Phone paired.");
  assert.deepEqual(pairOutcomeView("closed", {}), {
    text: "Nobody paired before the window closed.",
    detail: null,
    tone: null,
  });
  assert.deepEqual(pairOutcomeView("failed", { message: "Pairing did not complete." }), {
    text: "Pairing failed.",
    detail: "Pairing did not complete.",
    tone: "bad",
  });
  assert.equal(pairOutcomeView("failed", { message: null }).detail, null);
});

test("shouldPollPair polls only while visible, and only with the screen or a live window", () => {
  // Visible and on Settings: poll.
  assert.equal(shouldPollPair({ documentVisible: true, settingsVisible: true, windowActive: false }), true);
  // Visible, off Settings, but a window is live: poll.
  assert.equal(shouldPollPair({ documentVisible: true, settingsVisible: false, windowActive: true }), true);
  // Visible, off Settings, no window: do not poll.
  assert.equal(shouldPollPair({ documentVisible: true, settingsVisible: false, windowActive: false }), false);
  // Hidden tab never polls, even with a live window.
  assert.equal(shouldPollPair({ documentVisible: false, settingsVisible: true, windowActive: true }), false);
});

// --------------------------------------------------------------------------- #
// pairLostContact: a bounded run of poll failures is a dead service (I2)
// --------------------------------------------------------------------------- #

test("pairLostContact flips only once the failures reach the bound", () => {
  assert.ok(MAX_PAIR_POLL_FAILURES >= 1);
  for (let n = 0; n < MAX_PAIR_POLL_FAILURES; n += 1) {
    assert.equal(pairLostContact(n), false, `${n} failures is not yet lost`);
  }
  assert.equal(pairLostContact(MAX_PAIR_POLL_FAILURES), true);
  assert.equal(pairLostContact(MAX_PAIR_POLL_FAILURES + 3), true);
});

test("pairLostContact treats a non-number as not lost", () => {
  for (const bad of [null, undefined, NaN, "5", Infinity]) {
    assert.equal(pairLostContact(bad), false, `${JSON.stringify(bad)} is not lost`);
  }
});

// --------------------------------------------------------------------------- #
// pairControlsDisabled: a request in flight disables the action buttons (I3)
// --------------------------------------------------------------------------- #

test("pairControlsDisabled is true only while a request is pending", () => {
  assert.equal(pairControlsDisabled(true), true);
  assert.equal(pairControlsDisabled(false), false);
  assert.equal(pairControlsDisabled(undefined), false);
});

// --------------------------------------------------------------------------- #
// pairCountdownCue: a screen reader hears a coarse cue, not every second (I4)
// --------------------------------------------------------------------------- #

test("pairCountdownCue announces only at the coarse marks", () => {
  assert.match(pairCountdownCue(30), /30 seconds/);
  assert.match(pairCountdownCue(10), /10 seconds/);
  // Every other second is silent, so the reader is not flooded.
  for (const quiet of [120, 60, 31, 29, 11, 9, 1, 0, null, undefined, NaN]) {
    assert.equal(pairCountdownCue(quiet), null, `${JSON.stringify(quiet)} is silent`);
  }
});
