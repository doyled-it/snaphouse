import { test } from "node:test";
import assert from "node:assert/strict";
import {
  onlyHerePlan,
  everythingOffPlan,
  roomState,
  howToText,
  loadBluetoothName,
} from "../../roles/room_picker/files/site/listen-model.js";

const room = (id, on, reachable = true, volume = 54) => ({ id, on, reachable, volume });
const four = () => [room("a", true), room("b", true), room("c", false), room("d", false)];

test("onlyHerePlan turns the target on and every other reachable room off", () => {
  assert.deepEqual(onlyHerePlan(four(), "c"), [
    { id: "a", on: false },
    { id: "b", on: false },
    { id: "c", on: true },
  ]);
});

test("onlyHerePlan sends nothing for rooms already right", () => {
  assert.deepEqual(onlyHerePlan(four(), "a"), [{ id: "b", on: false }]);
});

test("onlyHerePlan is empty when the target is already the only room on", () => {
  assert.deepEqual(onlyHerePlan([room("a", true), room("b", false)], "a"), []);
});

test("onlyHerePlan skips unreachable rooms, including an unreachable target", () => {
  const rooms = [room("a", true), room("b", true, false), room("c", false)];
  assert.deepEqual(onlyHerePlan(rooms, "c"), [
    { id: "a", on: false },
    { id: "c", on: true },
  ]);
  assert.deepEqual(onlyHerePlan(rooms, "b"), []);
});

test("everythingOffPlan turns off only rooms that are on and reachable", () => {
  const rooms = [room("a", true), room("b", false), room("c", true, false), room("d", true)];
  assert.deepEqual(everythingOffPlan(rooms), [
    { id: "a", on: false },
    { id: "d", on: false },
  ]);
});

test("everythingOffPlan is empty when nothing is on", () => {
  assert.deepEqual(everythingOffPlan([room("a", false), room("b", false)]), []);
});

test("roomState labels the three states", () => {
  assert.equal(roomState(room("a", true, true, 54)).label, "54%");
  assert.equal(roomState(room("a", false)).label, "Off");
  const lost = roomState(room("a", true, false));
  assert.equal(lost.label, "Not responding");
  assert.equal(lost.disabled, true);
  assert.equal(roomState(room("a", true)).disabled, false);
});

test("roomState rounds the volume to a whole percent", () => {
  assert.equal(roomState(room("a", true, true, 53.6)).label, "54%");
});

test("roomState hides Only here for an unreachable room and for the only room on", () => {
  const rooms = [room("a", true), room("b", false), room("c", true, false)];
  assert.equal(roomState(rooms[0], rooms).showOnlyHere, false);
  assert.equal(roomState(rooms[1], rooms).showOnlyHere, true);
  assert.equal(roomState(rooms[2], rooms).showOnlyHere, false);
});

test("roomState shows Only here for an on room while another is on", () => {
  const rooms = [room("a", true), room("b", true)];
  assert.equal(roomState(rooms[0], rooms).showOnlyHere, true);
});

test("howToText names the Bluetooth speaker when it has a name", () => {
  assert.match(howToText("Kitchen Box"), /Kitchen Box/);
});

test("howToText falls back to wording without a name", () => {
  const text = howToText(null);
  assert.ok(text.length > 0);
  assert.doesNotMatch(text, /null|undefined|""/);
});

test("howToText has no em dash", () => {
  assert.ok(!howToText("X").includes(String.fromCharCode(0x2014)));
  assert.ok(!howToText(null).includes(String.fromCharCode(0x2014)));
});

test("loadBluetoothName returns the name", async () => {
  assert.equal(await loadBluetoothName(async () => ({ name: "Speaker" })), "Speaker");
});

test("loadBluetoothName returns null for a failed fetch", async () => {
  assert.equal(await loadBluetoothName(async () => { throw new Error("x"); }), null);
});

test("loadBluetoothName returns null for bad documents", async () => {
  for (const doc of [null, "text", 5, [], {}, { name: "" }, { name: "   " }, { name: 7 }, { name: "x".repeat(65) }]) {
    assert.equal(await loadBluetoothName(async () => doc), null, JSON.stringify(doc));
  }
});

test("loadBluetoothName accepts a name of exactly 64 characters", async () => {
  const name = "x".repeat(64);
  assert.equal(await loadBluetoothName(async () => ({ name })), name);
});

test("applyPlan overlays the sent plan on the rooms, keeping reachability", async () => {
  const { applyPlan } = await import("../../roles/room_picker/files/site/listen-model.js");
  const rooms = [room("a", true), room("b", false), room("c", true, false)];
  const out = applyPlan(rooms, [{ id: "a", on: false }, { id: "c", on: false }]);
  assert.deepEqual(out.map((r) => [r.id, r.on, r.reachable]), [["a", false, true], ["b", false, true], ["c", false, false]]);
  assert.equal(rooms[0].on, true);
});

test("Undo pressed before any status echo still reverts what was sent", async () => {
  const { applyPlan, onlyHerePlan } = await import("../../roles/room_picker/files/site/listen-model.js");
  const { snapshotRooms, undoPlan } = await import("../../roles/room_picker/files/site/undo.js");
  const rooms = four();
  const snapshot = snapshotRooms(rooms);
  const plan = onlyHerePlan(rooms, "c");
  // No echo: the status still says the old state, so overlay the plan.
  const undo = undoPlan(snapshot, applyPlan(rooms, plan));
  assert.deepEqual(undo, [{ id: "a", on: true }, { id: "b", on: true }, { id: "c", on: false }]);
});

test("playbackControls shows only for a bluetooth source", async () => {
  const { playbackControls } = await import("../../roles/room_picker/files/site/listen-model.js");
  assert.deepEqual(playbackControls(null), { visible: false });
  assert.deepEqual(playbackControls({ source: null, status: "playing" }), { visible: false });
  assert.deepEqual(playbackControls({ source: "airplay", status: "playing" }), { visible: false });
  assert.deepEqual(playbackControls({ source: "bluetooth", status: "playing" }), { visible: true, playing: true });
  assert.deepEqual(playbackControls({ source: "bluetooth", status: "paused" }), { visible: true, playing: false });
});

test("playbackRequest builds the Stream.Control body and rejects unknown commands", async () => {
  const { playbackRequest } = await import("../../roles/room_picker/files/site/listen-model.js");
  for (const command of ["play", "pause", "playPause", "next", "previous"]) {
    assert.deepEqual(playbackRequest(command), {
      method: "Stream.Control",
      params: { id: "Bluetooth", command },
    });
  }
  assert.throws(() => playbackRequest("seek"));
});
