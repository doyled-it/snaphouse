import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { parseClimate } from "../../roles/room_picker/files/site/climate-model.js";
import { parseGarageText } from "../../roles/room_picker/files/site/garage-model.js";
import { statusChips } from "../../roles/room_picker/files/site/desktop-status-model.js";

const NOW = Date.parse("2026-11-14T18:02:20Z");

function climate(over = {}) {
  return parseClimate({
    version: 1,
    updated: new Date(NOW - 5000).toISOString(),
    enabled: true,
    occupied: true,
    people: [{ name: "Alex", state: "home", last_seen: new Date(NOW - 60000).toISOString(), via: "wifi" }],
    thermostat: { reachable: true, mode: "cool", setpoint_f: 74.0, temperature_f: 68.2 },
    override: false,
    last_change: null,
    settings: { comfort_f: 72, away_f: 82, away_minutes: 20 },
    ...over,
  });
}

function garage(over = {}) {
  const base = JSON.parse(readFileSync(new URL("./fixtures/homebody-garage.json", import.meta.url), "utf8")).garage;
  const door = { ...base.door, ...(over.door || {}) };
  const link = { ...base.link, ...(over.link || {}) };
  const { door: _d, link: _l, ...rest } = over;
  return parseGarageText(JSON.stringify({ garage: { ...base, ...rest, door, link } }));
}

const chips = (over = {}) =>
  statusChips({ climate: climate(), garage: garage(), climateVisible: true, garageVisible: true, now: NOW, ...over });
const byId = (list, id) => list.find((c) => c.id === id);

test("a calm house gives three calm chips that open their screens", () => {
  const list = chips();
  assert.deepEqual(list.map((c) => c.id), ["people", "thermostat", "garage"]);
  assert.deepEqual(list.map((c) => c.state), ["calm", "calm", "calm"]);
  assert.equal(byId(list, "people").text, "Alex");
  assert.equal(byId(list, "people").target, "#climate");
  assert.equal(byId(list, "thermostat").text, "68°, cooling to 74°");
  assert.equal(byId(list, "garage").target, "#garage");
});

test("people: names of who is home, joined; Away and not sure read plainly", () => {
  const two = climate({ people: [
    { name: "Alex", state: "home", last_seen: null, via: "wifi" },
    { name: "Sam", state: "home", last_seen: null, via: "wifi" },
    { name: "Kim", state: "away", last_seen: null, via: "wifi" },
  ] });
  assert.equal(byId(chips({ climate: two }), "people").text, "Alex, Sam");
  const away = climate({ people: [{ name: "Alex", state: "away", last_seen: null, via: "wifi" }] });
  const a = byId(chips({ climate: away }), "people");
  assert.equal(a.text, "Away");
  assert.equal(a.state, "calm");
});

test("people: every person Not sure yet is yellow; one unsure among others is calm", () => {
  const unsure = climate({ people: [
    { name: "Alex", state: "unknown", last_seen: null, via: "wifi" },
    { name: "Sam", state: "unknown", last_seen: null, via: "wifi" },
  ] });
  const c = byId(chips({ climate: unsure }), "people");
  assert.equal(c.text, "Not sure who is home");
  assert.equal(c.state, "warn");
  const mixed = climate({ people: [
    { name: "Alex", state: "unknown", last_seen: null, via: "wifi" },
    { name: "Sam", state: "away", last_seen: null, via: "wifi" },
  ] });
  assert.equal(byId(chips({ climate: mixed }), "people").state, "calm");
});

test("people: nobody listed gives no people chip", () => {
  assert.equal(byId(chips({ climate: climate({ people: [] }) }), "people"), undefined);
});

test("thermostat: unreachable, checking, held, off and missing setpoint", () => {
  const t = (thermostat, extra = {}) => byId(chips({ climate: climate({ thermostat, ...extra }) }), "thermostat");
  assert.equal(t({ reachable: false, mode: "cool", setpoint_f: 74, temperature_f: 68 }).text, "Can't reach the thermostat");
  assert.equal(t({ reachable: false, mode: "cool", setpoint_f: 74, temperature_f: 68 }).state, "calm");
  assert.equal(t({ reachable: null, mode: null, setpoint_f: null, temperature_f: null }).text, "Checking the thermostat");
  assert.equal(t({ reachable: true, mode: "cool", setpoint_f: 74, temperature_f: 68 }, { override: true }).text, "68°, cooling to 74°, held");
  assert.equal(t({ reachable: true, mode: "heat", setpoint_f: 70, temperature_f: 66 }).text, "66°, heating to 70°");
  assert.equal(t({ reachable: true, mode: "off", setpoint_f: 70, temperature_f: 66 }).text, "66°, off");
  assert.equal(t({ reachable: true, mode: null, setpoint_f: null, temperature_f: 66 }).text, "66°");
});

test("garage: closed shows how long; calm", () => {
  const g = byId(chips({ garage: garage({ door: { state: "closed", since: new Date(NOW - 12 * 3600e3 - 3 * 60e3).toISOString() } }) }), "garage");
  assert.equal(g.text, "Closed for 12 h 3 min");
  assert.equal(g.state, "calm");
});

test("garage: open, opening and closing are yellow", () => {
  for (const [state, text] of [["open", /^Open for /], ["opening", /^Opening$/], ["closing", /^Closing$/]]) {
    const g = byId(chips({ garage: garage({ door: { state, since: new Date(NOW - 5 * 60e3).toISOString() } }) }), "garage");
    assert.match(g.text, text, state);
    assert.equal(g.state, "warn", state);
  }
});

test("garage: obstruction or lockout is red", () => {
  const o = byId(chips({ garage: garage({ obstruction: true }) }), "garage");
  assert.equal(o.state, "alert");
  assert.match(o.text, /something in the way/);
  const l = byId(chips({ garage: garage({ lockout: true }) }), "garage");
  assert.equal(l.state, "alert");
  assert.match(l.text, /remote lockout/i);
});

test("garage: a down link is red and never says closed", () => {
  const g = byId(chips({ garage: garage({ link: { connected: false }, door: { state: "closed" } }) }), "garage");
  assert.equal(g.state, "alert");
  assert.doesNotMatch(g.text, /closed/i);
  assert.equal(g.text, "Can't reach the garage door");
});

test("nothing else colors a chip: a calm thermostat problem stays calm", () => {
  const list = chips({ climate: climate({ thermostat: { reachable: false, mode: null, setpoint_f: null, temperature_f: null } }) });
  assert.equal(byId(list, "thermostat").state, "calm");
});

test("a chip is absent when its module is unavailable", () => {
  assert.deepEqual(chips({ garageVisible: false }).map((c) => c.id), ["people", "thermostat"]);
  assert.deepEqual(chips({ climateVisible: false }).map((c) => c.id), ["garage"]);
  assert.deepEqual(chips({ climate: null, garage: null }).map((c) => c.id), []);
});

test("chip copy never names the machinery and has no dashes", () => {
  for (const c of chips({ garage: garage({ obstruction: true }) })) {
    assert.doesNotMatch(c.text, /homebody|matter|api|esphome|board|hub|[–—]/i);
  }
});
