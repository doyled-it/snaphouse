// Tests for the "Pair a phone" model helpers the Settings screen uses. Run with:
//   node --test tests/js
//
// These cover the pure functions behind the pairing window, with no DOM and no
// network, the same split as the rest of the room model:
//   - parsePairState: read GET /api/pair/state, tolerating malformed or missing
//     fields and never trusting a passkey outside the confirm state.
//   - pairStartRequest / pairConfirmRequest / pairStopRequest: the POST requests,
//     each carrying the X-Snaphouse guard header and the right JSON body.
//   - interpretPairResponse: map an HTTP status and body to a result, including
//     403 (guard) and 409 (window already open, or not in confirm).
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
  pairConfirmRequest,
  pairStopRequest,
  interpretPairResponse,
  pairCountdown,
  formatCountdown,
  pairPhase,
  pairInstruction,
  shouldPollPair,
  confirmView,
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

test("parsePairState keeps a 6-digit passkey only in confirm", () => {
  const confirm = parsePairState(
    JSON.stringify({ state: "confirm", until: "2026-11-12T10:01:00Z", passkey: "012345", device: "Pixel 8", message: null }),
  );
  assert.equal(confirm.passkey, "012345");
  assert.equal(confirm.device, "Pixel 8");

  // A passkey leaking in any other state is dropped: the UI shows a code only in
  // confirm, and the service only sends one then.
  const open = parsePairState(JSON.stringify({ state: "open", passkey: "123456" }));
  assert.equal(open.passkey, null);
});

test("parsePairState rejects a passkey that is not six digits", () => {
  for (const bad of ["12345", "1234567", "12a456", "", "  ", 123456, null]) {
    const got = parsePairState(JSON.stringify({ state: "confirm", passkey: bad }));
    assert.equal(got.passkey, null, `passkey ${JSON.stringify(bad)} should be dropped`);
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

test("pairConfirmRequest posts the accept flag as a boolean", () => {
  const yes = pairConfirmRequest(true);
  assert.equal(yes.url, "/api/pair/confirm");
  assert.equal(yes.init.headers["X-Snaphouse"], "1");
  assert.deepEqual(JSON.parse(yes.init.body), { accept: true });

  const no = pairConfirmRequest(false);
  assert.deepEqual(JSON.parse(no.init.body), { accept: false });

  // A truthy or falsy non-boolean is coerced, so the body is always a real
  // boolean the service's 400 check accepts.
  assert.deepEqual(JSON.parse(pairConfirmRequest(1).init.body), { accept: true });
  assert.deepEqual(JSON.parse(pairConfirmRequest(undefined).init.body), { accept: false });
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

test("interpretPairResponse gives a 409 on confirm its own copy (I5)", () => {
  // The contract defines the 409 body only for start, so a 409 on confirm (the
  // window timed out or another answer won the race) must not read as if start
  // failed. It says the code is no longer waiting and that the page is checking.
  const got = interpretPairResponse(409, "", "confirm");
  assert.equal(got.ok, false);
  assert.equal(got.conflict, true);
  assert.match(got.error, /no longer waiting for an answer/);
  assert.doesNotMatch(got.error, /already open/);
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
  for (const state of ["open", "confirm", "paired", "declined", "failed"]) {
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

test("pairInstruction names the speaker, falling back to the house speaker", () => {
  assert.equal(
    pairInstruction("Living Room"),
    "On your phone, open Bluetooth, Pair new device, and tap Living Room.",
  );
  for (const none of ["", "  ", null, undefined, 5]) {
    assert.equal(
      pairInstruction(none),
      "On your phone, open Bluetooth, Pair new device, and tap the house speaker.",
    );
  }
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
// confirmView: never offer accept without a valid code on screen (I1)
// --------------------------------------------------------------------------- #

test("confirmView offers accept only with a valid six-digit code", () => {
  const good = confirmView({ state: "confirm", passkey: "012345", device: "Pixel 8" });
  assert.equal(good.code, "012345");
  assert.equal(good.canAccept, true);
  assert.equal(good.note, null);
});

test("confirmView hides the code and accept when the passkey is missing or malformed", () => {
  // parsePairState drops a bad passkey to null but keeps state "confirm", so the
  // confirm view can be reached with no code. Accept must not be offered then:
  // the user could otherwise approve a pairing they never compared.
  for (const bad of [null, undefined, "", "12345", "1234567", "12a456"]) {
    const view = confirmView({ state: "confirm", passkey: bad });
    assert.equal(view.code, null, `passkey ${JSON.stringify(bad)} should show no code`);
    assert.equal(view.canAccept, false, `passkey ${JSON.stringify(bad)} must not allow accept`);
    assert.ok(view.note && view.note.length > 0, "a bad code gives a plain note");
  }
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
