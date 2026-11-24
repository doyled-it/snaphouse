import { test } from "node:test";
import assert from "node:assert/strict";
import { isPanel } from "../../roles/room_picker/files/site/panel-model.js";
import { howToText } from "../../roles/room_picker/files/site/listen-model.js";

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

import {
  stepVolume,
  snapVolume,
  nowPlayingBar,
  panelHowTo,
  roomColumn,
  columnFit,
  progressFraction,
} from "../../roles/room_picker/files/site/panel-model.js";

test("stepVolume moves by 5 on the 5 grid and clamps", () => {
  assert.equal(stepVolume(52, 1), 55);
  assert.equal(stepVolume(98, 1), 100);
  assert.equal(stepVolume(2, -1), 0);
  assert.equal(stepVolume(55, -1), 50);
  assert.equal(stepVolume(50, 1), 55);
  assert.equal(stepVolume(0, -1), 0);
});

test("snapVolume rounds a tapped height to steps of 5", () => {
  assert.equal(snapVolume(52), 50);
  assert.equal(snapVolume(53), 55);
  assert.equal(snapVolume(100), 100);
  assert.equal(snapVolume(-4), 0);
});

test("nowPlayingBar with a track shows title, artist, and transport only for Bluetooth", () => {
  const bt = nowPlayingBar({ title: "T", artist: "A", source: "bluetooth", status: "playing", controllable: true }, {});
  assert.deepEqual([bt.mode, bt.title, bt.artist, bt.showTransport, bt.playing], ["track", "T", "A", true, true]);
  const ap = nowPlayingBar({ title: "T", artist: null, source: "airplay", status: "paused" }, {});
  assert.deepEqual([ap.showTransport, ap.playing, ap.artist], [false, false, ""]);
});

test("nowPlayingBar idle follows howToText and shows no transport", () => {
  const bar = nowPlayingBar(null, { btName: "House" });
  assert.equal(bar.mode, "idle");
  assert.equal(bar.title, "Nothing playing");
  assert.match(bar.howTo, /"House"/);
  assert.equal(bar.showTransport, false);
});

test("nowPlayingBar becomes the speaker banner while the audio server is down", () => {
  const bar = nowPlayingBar({ title: "T", source: "bluetooth" }, { speakersDown: true });
  assert.equal(bar.mode, "banner");
  assert.equal(bar.title, "Can't reach the speakers right now. Retrying.");
  assert.equal(bar.showTransport, false);
});

test("progressFraction is elapsed over total, clamped, 0 when unknown", () => {
  assert.equal(progressFraction({ elapsed: 30, total: 120 }), 0.25);
  assert.equal(progressFraction({ elapsed: 500, total: 120 }), 1);
  assert.equal(progressFraction(null), 0);
  assert.equal(progressFraction({ elapsed: 5, total: 0 }), 0);
});

const ROOM = { id: "a", label: "Office", on: true, reachable: true, volume: 54 };

test("roomColumn for an on room shows the number and the fill", () => {
  const c = roomColumn(ROOM, [ROOM], {});
  assert.deepEqual(
    [c.name, c.valueText, c.fillPct, c.greyed, c.disabled, c.power, c.status],
    ["Office", "54", 54, false, false, true, ""],
  );
});

test("roomColumn for an off room keeps its remembered volume, greyed", () => {
  const c = roomColumn({ ...ROOM, on: false }, [ROOM], {});
  assert.deepEqual([c.valueText, c.fillPct, c.greyed, c.disabled, c.power], ["54", 54, true, false, false]);
});

test("roomColumn for a room that is not responding greys and disables everything", () => {
  const c = roomColumn({ ...ROOM, reachable: false }, [ROOM], {});
  assert.deepEqual([c.status, c.greyed, c.disabled], ["Not responding", true, true]);
});

test("roomColumn disables every control while the speakers are down", () => {
  const c = roomColumn(ROOM, [ROOM], { speakersDown: true });
  assert.equal(c.disabled, true);
});

test("columnFit keeps 60px of fill for up to four rooms and flags a fifth", () => {
  assert.equal(columnFit(3).ok, true);
  assert.equal(columnFit(4).ok, true);
  assert.equal(columnFit(5).ok, false);
  assert.ok(columnFit(3).fillPx >= 60);
});

import { climateCards, comfortStep, whoLine } from "../../roles/room_picker/files/site/panel-model.js";

const person = (name, state) => ({ name, state, label: "", id: name, removable: false, seen: "", lastKnown: false });

test("whoLine names who is home on one line", () => {
  assert.equal(whoLine([person("Sam", "home"), person("Alex", "home")]), "Home: Sam and Alex");
  assert.equal(whoLine([person("Sam", "home"), person("Alex", "away"), person("Jo", "home")]), "Home: Sam and Jo");
  assert.equal(whoLine([person("Sam", "away")]), "Everyone is away");
  assert.equal(whoLine([person("Sam", "unknown")]), "Not sure yet");
  assert.equal(whoLine([]), "No phones are set up for climate yet.");
});

const CDOC = {
  enabled: true,
  settings: { comfort_f: 74, away_f: 82, away_minutes: 30 },
  thermostat: { reachable: true, mode: "cool", setpointF: 74, temperatureF: 75.6 },
  people: [{ name: "Sam", state: "home", id: "a", removable: false, lastSeenMs: 0 }],
  lastChange: null,
  override: false,
};

test("climateCards builds the inside and comfort cards", () => {
  const c = climateCards(CDOC, { now: 0, comfort: 74 });
  assert.equal(c.inside.temp, "76°");
  assert.equal(c.inside.modeText, "Cooling to 74°");
  assert.equal(c.inside.modeAccent, true);
  assert.equal(c.inside.who, "Home: Sam");
  assert.equal(c.comfort.value, "74°");
  assert.equal(c.comfort.minusDisabled, false);
  assert.equal(c.comfort.plusDisabled, false);
});

test("climateCards says so when the thermostat cannot be reached or is being checked", () => {
  const down = climateCards({ ...CDOC, thermostat: { ...CDOC.thermostat, reachable: false } }, { now: 0, comfort: 74 });
  assert.equal(down.inside.modeText, "Can't reach the thermostat");
  assert.equal(down.inside.modeAccent, false);
  const unsure = climateCards({ ...CDOC, thermostat: { ...CDOC.thermostat, reachable: null } }, { now: 0, comfort: 74 });
  assert.equal(unsure.inside.modeText, "Checking the thermostat");
});

test("comfortStep moves one degree and refuses the limits and away", () => {
  assert.equal(comfortStep(74, 1, 82), 75);
  assert.equal(comfortStep(74, -1, 82), 73);
  assert.equal(comfortStep(60, -1, 82), null);
  assert.equal(comfortStep(85, 1, 90), null);
  assert.equal(comfortStep(81, 1, 82), null);
});

test("climateCards disables the buttons at the limits", () => {
  assert.equal(climateCards(CDOC, { now: 0, comfort: 60 }).comfort.minusDisabled, true);
  assert.equal(climateCards(CDOC, { now: 0, comfort: 81 }).comfort.plusDisabled, true);
});

import { pairInstructionPanel } from "../../roles/room_picker/files/site/panel-model.js";
import { pairInstruction } from "../../roles/room_picker/files/site/rooms-model.js";

test("pairInstructionPanel says your phone, names the speaker, and leaves the phone copy alone", () => {
  assert.equal(
    pairInstructionPanel("Living Room"),
    "Now open Bluetooth on your phone, tap Pair new device, choose Living Room, and tap Pair. You can stay there; come back here when it's done.",
  );
  assert.match(pairInstructionPanel(null), /choose the house speaker,/);
  assert.match(pairInstruction("X"), /on this phone/);
  assert.doesNotMatch(pairInstructionPanel("X"), /this phone/);
});

test("nowPlayingBar shows no transport for a Bluetooth track whose player is gone", () => {
  const bar = nowPlayingBar({ title: "T", source: "bluetooth", status: "paused", controllable: false }, {});
  assert.equal(bar.showTransport, false);
});

test("panelHowTo stops after connecting over Bluetooth; phones keep their copy", () => {
  assert.equal(panelHowTo("House"), 'Nothing is playing. Connect your phone to "House" over Bluetooth.');
  assert.equal(panelHowTo(null), "Nothing is playing. Connect your phone over Bluetooth.");
  assert.match(howToText("House"), /then play something/);
  assert.equal(nowPlayingBar(null, { btName: "House" }).howTo, panelHowTo("House"));
});
