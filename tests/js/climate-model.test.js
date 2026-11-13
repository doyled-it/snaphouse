import test from "node:test";
import assert from "node:assert/strict";
import {
  EMPTY_PEOPLE_TEXT,
  SETTING_LIMITS,
  checkSettings,
  lastChangeText,
  overrideText,
  parseClimate,
  parseClimateText,
  parseWhole,
  peopleLines,
  relativeSeen,
  settingsBody,
  thermostatView,
} from "../../roles/room_picker/files/site/climate-model.js";

const NOW = Date.parse("2026-11-13T15:10:00Z");

function doc(over = {}) {
  return {
    version: 1,
    updated: "2026-11-13T15:09:58Z",
    enabled: true,
    occupied: true,
    people: [{ name: "Person A", state: "home", last_seen: "2026-11-13T15:07:00Z", via: "wifi" }],
    thermostat: { reachable: true, mode: "cool", setpoint_f: 72.0, temperature_f: 74.3 },
    override: false,
    last_change: { at: "2026-11-13T14:14:00Z", setpoint_f: 82.0, reason: "away" },
    settings: { comfort_f: 72, away_f: 82, away_minutes: 20 },
    ...over,
  };
}

test("parseClimate accepts the full document", () => {
  const parsed = parseClimate(doc());
  assert.equal(parsed.enabled, true);
  assert.equal(parsed.occupied, true);
  assert.equal(parsed.people.length, 1);
  assert.equal(parsed.people[0].name, "Person A");
  assert.equal(parsed.people[0].lastSeenMs, Date.parse("2026-11-13T15:07:00Z"));
  assert.deepEqual(parsed.thermostat, { reachable: true, mode: "cool", setpointF: 72, temperatureF: 74.3 });
  assert.equal(parsed.override, false);
  assert.deepEqual(parsed.lastChange, { atMs: Date.parse("2026-11-13T14:14:00Z"), setpointF: 82, reason: "away" });
  assert.deepEqual(parsed.settings, { comfort_f: 72, away_f: 82, away_minutes: 20 });
});

test("parseClimate rejects documents that are not usable", () => {
  for (const bad of [null, undefined, 3, "x", [], {}, doc({ version: 2 }), doc({ settings: null }),
    doc({ settings: { comfort_f: "72", away_f: 82, away_minutes: 20 } }),
    doc({ settings: { comfort_f: 72, away_f: 82 } }),
    doc({ people: "nobody" }), doc({ thermostat: null }), doc({ enabled: "yes" }), doc({ override: null })]) {
    assert.equal(parseClimate(bad), null, JSON.stringify(bad));
  }
});

test("parseClimate keeps every null case", () => {
  const parsed = parseClimate(doc({
    occupied: null,
    people: [],
    thermostat: { reachable: null, mode: null, setpoint_f: null, temperature_f: null },
    last_change: null,
  }));
  assert.equal(parsed.occupied, null);
  assert.deepEqual(parsed.people, []);
  assert.deepEqual(parsed.thermostat, { reachable: null, mode: null, setpointF: null, temperatureF: null });
  assert.equal(parsed.lastChange, null);
});

test("parseClimate drops malformed people and tolerates odd fields", () => {
  const parsed = parseClimate(doc({
    people: [
      { name: "Person A", state: "away", last_seen: null, via: null },
      { name: "", state: "home" },
      { state: "home" },
      "x",
      { name: "Person B", state: "weird", last_seen: "nonsense" },
    ],
    last_change: { at: "garbage", setpoint_f: 80, reason: "away" },
  }));
  assert.deepEqual(parsed.people.map((p) => [p.name, p.state, p.lastSeenMs]), [
    ["Person A", "away", null],
    ["Person B", "unknown", null],
  ]);
  assert.equal(parsed.lastChange, null);
});

test("parseClimate treats missing thermostat fields as null", () => {
  const parsed = parseClimate(doc({ thermostat: { reachable: false } }));
  assert.deepEqual(parsed.thermostat, { reachable: false, mode: null, setpointF: null, temperatureF: null });
});

test("parseClimateText parses JSON text and rejects garbage", () => {
  assert.equal(parseClimateText(JSON.stringify(doc())).enabled, true);
  assert.equal(parseClimateText("<html>"), null);
  assert.equal(parseClimateText(""), null);
  assert.equal(parseClimateText(null), null);
});

test("relativeSeen words", () => {
  assert.equal(relativeSeen(NOW - 20_000, NOW), "seen just now");
  assert.equal(relativeSeen(NOW + 5_000, NOW), "seen just now");
  assert.equal(relativeSeen(NOW - 60_000, NOW), "seen 1 min ago");
  assert.equal(relativeSeen(NOW - 3 * 60_000, NOW), "seen 3 min ago");
  assert.equal(relativeSeen(NOW - 59 * 60_000, NOW), "seen 59 min ago");
  assert.equal(relativeSeen(NOW - 60 * 60_000, NOW), "seen 1 hr ago");
  assert.equal(relativeSeen(NOW - 5 * 3600_000, NOW), "seen 5 hr ago");
  assert.equal(relativeSeen(NOW - 24 * 3600_000, NOW), "seen yesterday");
  assert.equal(relativeSeen(NOW - 4 * 24 * 3600_000, NOW), "seen 4 days ago");
  assert.equal(relativeSeen(null, NOW), "");
});

test("peopleLines labels and last seen", () => {
  const parsed = parseClimate(doc({
    people: [
      { name: "Person A", state: "home", last_seen: "2026-11-13T15:07:00Z", via: "wifi" },
      { name: "Person B", state: "away", last_seen: "2026-11-13T12:10:00Z", via: "wifi" },
      { name: "Person C", state: "unknown", last_seen: null, via: null },
    ],
  }));
  assert.deepEqual(peopleLines(parsed, NOW), [
    { name: "Person A", state: "home", label: "Home", seen: "seen 3 min ago" },
    { name: "Person B", state: "away", label: "Away", seen: "seen 3 hr ago" },
    { name: "Person C", state: "unknown", label: "Not sure yet", seen: "" },
  ]);
});

test("peopleLines is empty with nobody listed, and the empty text is plain", () => {
  assert.deepEqual(peopleLines(parseClimate(doc({ people: [] })), NOW), []);
  assert.match(EMPTY_PEOPLE_TEXT, /no phones/i);
  assert.match(EMPTY_PEOPLE_TEXT, /climate/i);
});

test("thermostatView reads, unreachable and checking", () => {
  assert.deepEqual(thermostatView(parseClimate(doc())), {
    status: "ok", message: "", temperature: "74°", setpoint: "72°", mode: "Cooling",
  });
  const off = thermostatView(parseClimate(doc({ thermostat: { reachable: true, mode: "off", setpoint_f: 72, temperature_f: 71.5 } })));
  assert.equal(off.mode, "Off");
  assert.equal(off.temperature, "72°");
  const heat = thermostatView(parseClimate(doc({ thermostat: { reachable: true, mode: "heat", setpoint_f: 68, temperature_f: 66 } })));
  assert.equal(heat.mode, "Heating");
  const auto = thermostatView(parseClimate(doc({ thermostat: { reachable: true, mode: "auto", setpoint_f: 68, temperature_f: 66 } })));
  assert.equal(auto.mode, "Auto");
  const odd = thermostatView(parseClimate(doc({ thermostat: { reachable: true, mode: "emergency_heat", setpoint_f: 68, temperature_f: 66 } })));
  assert.equal(odd.mode, "Emergency heat");
  const nulls = thermostatView(parseClimate(doc({ thermostat: { reachable: true, mode: null, setpoint_f: null, temperature_f: null } })));
  assert.deepEqual(nulls, { status: "ok", message: "", temperature: "--", setpoint: "--", mode: "" });

  const down = thermostatView(parseClimate(doc({ thermostat: { reachable: false, mode: "cool", setpoint_f: 72, temperature_f: 74 } })));
  assert.equal(down.status, "unreachable");
  assert.equal(down.message, "Can't reach the thermostat");
  assert.equal(down.temperature, "74°");
  const unknown = thermostatView(parseClimate(doc({ thermostat: { reachable: null, mode: null, setpoint_f: null, temperature_f: null } })));
  assert.equal(unknown.status, "checking");
  assert.equal(unknown.message, "Checking the thermostat");
  assert.equal(unknown.temperature, "--");
});

test("lastChangeText says what and why", () => {
  const parsed = parseClimate(doc({ last_change: { at: "2026-11-13T14:14:00Z", setpoint_f: 82.0, reason: "away" } }));
  assert.equal(lastChangeText(parsed, NOW, "UTC"), "Set to 82 at 2:14 PM, everyone left");
  const home = parseClimate(doc({ last_change: { at: "2026-11-13T09:05:00Z", setpoint_f: 71.6, reason: "home" } }));
  assert.equal(lastChangeText(home, NOW, "UTC"), "Set to 72 at 9:05 AM, someone came home");
  const yday = parseClimate(doc({ last_change: { at: "2026-11-12T22:00:00Z", setpoint_f: 72, reason: "home" } }));
  assert.equal(lastChangeText(yday, NOW, "UTC"), "Set to 72 yesterday at 10:00 PM, someone came home");
  const older = parseClimate(doc({ last_change: { at: "2026-11-10T22:00:00Z", setpoint_f: 72, reason: "home" } }));
  assert.equal(lastChangeText(older, NOW, "UTC"), "Set to 72 on Nov 10 at 10:00 PM, someone came home");
  const unk = parseClimate(doc({ last_change: { at: "2026-11-13T14:14:00Z", setpoint_f: 82, reason: "other" } }));
  assert.equal(lastChangeText(unk, NOW, "UTC"), "Set to 82 at 2:14 PM");
  assert.equal(lastChangeText(parseClimate(doc({ last_change: null })), NOW, "UTC"), "No automatic change yet");
});

test("overrideText only when override is on", () => {
  assert.equal(overrideText(parseClimate(doc({ override: false }))), "");
  assert.equal(
    overrideText(parseClimate(doc({ override: true }))),
    "You changed the thermostat by hand. Automatic changes resume when someone leaves or comes home.",
  );
});

test("parseWhole reads whole numbers only", () => {
  assert.equal(parseWhole("72"), 72);
  assert.equal(parseWhole(" 72 "), 72);
  for (const bad of ["", "7.5", "abc", "-", "1e2", null, undefined]) {
    assert.equal(parseWhole(bad), null, String(bad));
  }
});

test("checkSettings enforces the contract ranges and ordering", () => {
  assert.deepEqual(SETTING_LIMITS, {
    comfort_f: { min: 60, max: 85 },
    away_f: { min: 65, max: 90 },
    away_minutes: { min: 5, max: 120 },
  });
  assert.equal(checkSettings({ comfort_f: 72, away_f: 82, away_minutes: 20 }), "");
  assert.equal(checkSettings({ comfort_f: 60, away_f: 65, away_minutes: 5 }), "");
  assert.equal(checkSettings({ comfort_f: 85, away_f: 90, away_minutes: 120 }), "");
  assert.match(checkSettings({ comfort_f: 82, away_f: 82, away_minutes: 20 }), /comfort.*lower.*away/i);
  assert.match(checkSettings({ comfort_f: 84, away_f: 80, away_minutes: 20 }), /comfort.*lower.*away/i);
  assert.match(checkSettings({ comfort_f: 59, away_f: 82, away_minutes: 20 }), /60 and 85/);
  assert.match(checkSettings({ comfort_f: 86, away_f: 90, away_minutes: 20 }), /60 and 85/);
  assert.match(checkSettings({ comfort_f: 72, away_f: 64, away_minutes: 20 }), /65 and 90/);
  assert.match(checkSettings({ comfort_f: 72, away_f: 91, away_minutes: 20 }), /65 and 90/);
  assert.match(checkSettings({ comfort_f: 72, away_f: 82, away_minutes: 4 }), /5 and 120/);
  assert.match(checkSettings({ comfort_f: 72, away_f: 82, away_minutes: 121 }), /5 and 120/);
  assert.match(checkSettings({ comfort_f: null, away_f: 82, away_minutes: 20 }), /comfort/i);
});

test("settingsBody sends only the keys that differ", () => {
  const held = { enabled: true, comfort_f: 72, away_f: 82, away_minutes: 20 };
  assert.deepEqual(settingsBody(held, { ...held, enabled: false }), { enabled: false });
  assert.deepEqual(settingsBody(held, { ...held, comfort_f: 70 }), { comfort_f: 70 });
  assert.deepEqual(settingsBody(held, { ...held, comfort_f: 70, away_minutes: 30 }), { comfort_f: 70, away_minutes: 30 });
  assert.deepEqual(settingsBody(held, { ...held }), {});
  assert.deepEqual(settingsBody(held, { ...held, extra: 1 }), {});
});
