// Source of the fixture: homebody tests/fixtures/garage_status_sample.json
// (keep the two byte-identical). The Garage tab's rules are in garage-model.js.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  parseGarageText,
  garageView,
  settingLimits,
  settingsBody,
  confirmState,
  errorLine,
  pollMs,
  afterCommandPoll,
} from "../../roles/room_picker/files/site/garage-model.js";

const NOW = Date.parse("2026-11-14T18:02:20Z");

function doc(over = {}) {
  const base = JSON.parse(readFileSync(new URL("./fixtures/homebody-garage.json", import.meta.url), "utf8"));
  const g = base.garage;
  const door = { ...g.door, ...(over.door || {}) };
  const link = { ...g.link, ...(over.link || {}) };
  const { door: _d, link: _l, ...rest } = over;
  return parseGarageText(JSON.stringify({ garage: { ...g, ...rest, door, link } }));
}

test("closed shows CLOSED, a duration and Open only", () => {
  const v = garageView(doc(), NOW);
  assert.equal(v.word, "CLOSED");
  assert.equal(v.duration, "for 22 min");
  assert.deepEqual(v.buttons, { open: true, close: false });
  assert.equal(v.line, null);
  assert.equal(v.lastSeen, null);
});

test("open shows OPEN and Close only", () => {
  const v = garageView(doc({ door: { state: "open" } }), NOW);
  assert.equal(v.word, "OPEN");
  assert.deepEqual(v.buttons, { open: false, close: true });
});

test("opening and closing show no buttons and no duration", () => {
  for (const [state, word] of [["opening", "OPENING"], ["closing", "CLOSING"]]) {
    const v = garageView(doc({ door: { state } }), NOW);
    assert.equal(v.word, word);
    assert.deepEqual(v.buttons, { open: false, close: false });
    assert.equal(v.duration, null);
  }
});

test("stopped shows both buttons", () => {
  const v = garageView(doc({ door: { state: "stopped" } }), NOW);
  assert.equal(v.word, "STOPPED");
  assert.deepEqual(v.buttons, { open: true, close: true });
  assert.deepEqual(v.disabled, { open: false, close: false });
});

test("duration reads hours and minutes, and says about when approximate", () => {
  const since = "2026-11-14T15:48:20Z";
  assert.equal(garageView(doc({ door: { since } }), NOW).duration, "for 2 h 14 min");
  assert.equal(
    garageView(doc({ door: { since, since_approximate: true } }), NOW).duration,
    "for about 2 h 14 min",
  );
});

test("a clock that stepped back gives a zero duration, not a negative one", () => {
  const v = garageView(doc({ door: { since: "2026-11-14T19:00:00Z" } }), NOW);
  assert.equal(v.duration, "for less than a minute");
});

test("unknown shows last seen and the old state", () => {
  const v = garageView(
    doc({ door: { state: "unknown", since: null, last_known_state: "closed" }, link: { connected: false, last_seen: "2026-11-14T17:50:20Z" } }),
    NOW,
  );
  assert.equal(v.word, "UNKNOWN");
  assert.equal(v.lastSeen, "Last seen 12 min ago (was closed)");
  assert.equal(v.duration, null);
});

test("unknown shows both buttons, disabled-style", () => {
  const v = garageView(doc({ door: { state: "unknown", last_known_state: null }, link: { connected: false } }), NOW);
  assert.deepEqual(v.buttons, { open: true, close: true });
  assert.deepEqual(v.disabled, { open: true, close: true });
});

test("unknown with no last known state leaves out the was clause", () => {
  const v = garageView(doc({ door: { state: "unknown", last_known_state: null }, link: { connected: false, last_seen: "2026-11-14T17:40:20Z" } }), NOW);
  assert.equal(v.lastSeen, "Last seen 22 min ago");
});

test("never closed when the link is down", () => {
  const v = garageView(doc({ link: { connected: false }, door: { state: "closed" } }), NOW);
  assert.equal(v.word, "UNKNOWN");
  assert.deepEqual(v.disabled, { open: true, close: true });
});

test("a state the page does not know renders UNKNOWN", () => {
  assert.equal(garageView(doc({ door: { state: "ajar" } }), NOW).word, "UNKNOWN");
});

test("obstruction and lockout lines disable Close", () => {
  const stuck = garageView(doc({ door: { state: "open" }, obstruction: true }), NOW);
  assert.equal(stuck.line, "The opener reports something in the way");
  assert.deepEqual(stuck.disabled, { open: false, close: true });
  const locked = garageView(doc({ door: { state: "open" }, lockout: true }), NOW);
  assert.equal(locked.line, "Remote lockout is on at the opener");
  assert.equal(locked.disabled.close, true);
  assert.equal(locked.disabled.open, false);
});

test("the link line", () => {
  assert.equal(garageView(doc(), NOW).linkLine, "Opener link: connected");
  assert.equal(garageView(doc({ link: { connected: false } }), NOW).linkLine, "Opener link: not connected");
});

test("hidden when garage is null or missing", () => {
  assert.equal(parseGarageText(JSON.stringify({ garage: null })), null);
  assert.equal(parseGarageText(JSON.stringify({})), null);
  assert.equal(parseGarageText("not json"), null);
  assert.equal(parseGarageText(JSON.stringify({ garage: [] })), null);
  assert.equal(parseGarageText(JSON.stringify({ garage: { door: {} } })), null);
  assert.equal(parseGarageText(null), null);
});

test("sample document from homebody parses", () => {
  const text = readFileSync(new URL("./fixtures/homebody-garage.json", import.meta.url), "utf8");
  const g = parseGarageText(text);
  assert.equal(g.connected, true);
  assert.equal(g.state, "closed");
  assert.equal(g.lastKnownState, "closed");
  assert.equal(g.obstruction, false);
  assert.deepEqual(g.settings, { left_open_minutes: 15, away_open_grace_minutes: 5, alerts: true, remote: true });
  assert.equal(g.recent.length, 1);
  assert.equal(g.recent[0].event, "closed");
});

test("setting limits", () => {
  assert.deepEqual(settingLimits, {
    left_open_minutes: { min: 0, max: 240 },
    away_open_grace_minutes: { min: 0, max: 15 },
  });
});

test("settingsBody sends only changed keys under the API names", () => {
  const written = { left_open_minutes: 15, away_open_grace_minutes: 5, alerts: true, remote: false };
  assert.deepEqual(settingsBody(written, { ...written }), {});
  assert.deepEqual(settingsBody(written, { ...written, left_open_minutes: 30, remote: true }), {
    garage_left_open_minutes: 30,
    garage_remote: true,
  });
  assert.deepEqual(settingsBody(written, { away_open_grace_minutes: 0, alerts: false }), {
    garage_away_open_grace_minutes: 0,
    garage_alerts: false,
  });
});

test("confirm only from the intent response", () => {
  assert.deepEqual(confirmState({ action: "open", confirm_required: true }), {
    needsConfirm: true,
    text: "Open the garage door?",
    note: "You are away. This moves the door.",
  });
  assert.equal(confirmState({ action: "close", confirm_required: true }).text, "Close the garage door?");
  for (const intent of [{ action: "open", confirm_required: false }, { action: "open" }, { action: "open", confirm_required: "yes" }, null]) {
    const s = confirmState(intent);
    assert.equal(s.needsConfirm, false);
    assert.equal(s.text, null);
  }
});

test("error lines for 403 409 423 429 503 504", () => {
  assert.equal(errorLine(403, null), "The garage can't be controlled from here right now.");
  assert.equal(errorLine(409, null), "The door can't do that right now.");
  assert.equal(errorLine(423, null), "Remote lockout is on at the opener.");
  assert.equal(errorLine(429, null), "Too many tries. Wait a moment and try again.");
  assert.equal(errorLine(503, null), "Can't reach the opener right now.");
  assert.equal(errorLine(504, null), "No movement seen yet. Check the card before trying again.");
  assert.equal(errorLine(500, null), "Something went wrong. Try again.");
});

test("a plain sentence from the server is used for the refusals that carry one", () => {
  assert.equal(errorLine(409, { error: "The opener reports something in the way." }), "The opener reports something in the way.");
  assert.equal(errorLine(403, { error: 42 }), "The garage can't be controlled from here right now.");
});

test("504 line is no movement seen yet, check the card", () => {
  assert.equal(errorLine(504, { error: "The garage did not answer in time. Check the card." }), "No movement seen yet. Check the card before trying again.");
});

test("poll intervals", () => {
  assert.equal(pollMs(true), 5000);
  assert.equal(pollMs(false), 60000);
  assert.deepEqual(afterCommandPoll(), { everyMs: 2000, forMs: 15000 });
});

test("page copy never names the parts and never promises an instant alert", () => {
  const all = [
    errorLine(403, null), errorLine(409, null), errorLine(423, null), errorLine(429, null),
    errorLine(503, null), errorLine(504, null), errorLine(500, null),
    confirmState({ action: "open", confirm_required: true }).note,
    garageView(doc({ link: { connected: false } }), NOW).linkLine,
  ].join(" ");
  assert.doesNotMatch(all, /esphome|board|ntfy|api/i);
  assert.equal(all.includes(String.fromCharCode(8212)), false);
});
