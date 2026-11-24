import { test } from "node:test";
import assert from "node:assert/strict";
import {
  AMBIENT_PAUSE_MS,
  ROOMS_RETURN_MS,
  ambientWanted,
  nowView,
  pausedClock,
  returnDue,
  roomsButtonLabel,
  trackKey,
} from "../../roles/room_picker/files/site/now-model.js";

const bt = (over = {}) => ({ title: "Song", artist: "Band", album: "LP", source: "bluetooth", status: "playing", controllable: true, artUrl: "/cover/a.jpg", duration: 200, ...over });
const air = (over = {}) => ({ title: "Tune", artist: "Duo", album: null, source: "airplay", status: "playing", controllable: false, artUrl: null, duration: null, ...over });

test("constants match the spec", () => {
  assert.equal(AMBIENT_PAUSE_MS, 300000);
  assert.equal(ROOMS_RETURN_MS, 30000);
});

test("trackKey identifies title, artist and album", () => {
  assert.equal(trackKey(null), null);
  assert.notEqual(trackKey(bt()), trackKey(bt({ title: "Other" })));
  assert.equal(trackKey(bt({ status: "paused" })), trackKey(bt()));
});

test("pausedClock starts when a track is first seen paused and keeps that moment", () => {
  let c = pausedClock(null, bt(), 1000);
  assert.equal(c.since, null);
  c = pausedClock(c, bt({ status: "paused" }), 2000);
  assert.equal(c.since, 2000);
  c = pausedClock(c, bt({ status: "paused" }), 9000);
  assert.equal(c.since, 2000);
});

test("pausedClock resets on play, on a different track and on no track", () => {
  let c = pausedClock(null, bt({ status: "paused" }), 100);
  assert.equal(pausedClock(c, bt(), 200).since, null);
  assert.equal(pausedClock(c, bt({ title: "B", status: "paused" }), 300).since, 300);
  assert.deepEqual(pausedClock(c, null, 400), { key: null, since: null });
});

test("ambientWanted: playing, or paused under 5 minutes on the device's own clock", () => {
  assert.equal(ambientWanted(null, { since: null }, 0), false);
  assert.equal(ambientWanted(bt(), { since: null }, 5), true);
  const p = bt({ status: "paused" });
  assert.equal(ambientWanted(p, { since: 1000 }, 1000 + 299999), true);
  assert.equal(ambientWanted(p, { since: 1000 }, 1000 + 300000), false);
  assert.equal(ambientWanted(p, { since: null }, 5), true);
});

test("ambientWanted counts a held track the same way", () => {
  const held = bt({ status: "paused", held: true });
  assert.equal(ambientWanted(held, { since: 0 }, 299999), true);
  assert.equal(ambientWanted(held, { since: 0 }, 300000), false);
});

test("nowView for a playing Bluetooth track", () => {
  const v = nowView(bt(), { phoneName: "Pixel" });
  assert.equal(v.mode, "track");
  assert.equal(v.title, "Song");
  assert.equal(v.artist, "Band");
  assert.equal(v.paused, false);
  assert.equal(v.pausedLine, "");
  assert.equal(v.transport, "buttons");
  assert.equal(v.playing, true);
  assert.equal(v.art, "/cover/a.jpg");
});

test("nowView paused names the phone, or just Paused", () => {
  assert.equal(nowView(bt({ status: "paused" }), { phoneName: "Pixel" }).pausedLine, "Paused on Pixel");
  assert.equal(nowView(bt({ status: "paused" }), { phoneName: null }).pausedLine, "Paused");
  assert.equal(nowView(bt({ status: "paused" }), { phoneName: "" }).pausedLine, "Paused");
  assert.equal(nowView(bt({ status: "paused" }), {}).playing, false);
});

test("nowView transport: AirPlay says control from the phone, no player shows none", () => {
  const a = nowView(air(), {});
  assert.equal(a.transport, "airplay");
  assert.equal(a.airplayText, "Control from the phone");
  assert.equal(nowView(air({ status: "paused" }), {}).transport, "airplay");
  assert.equal(nowView(bt({ controllable: false, status: "paused" }), {}).transport, "none");
});

test("nowView copes with a title-only track", () => {
  const v = nowView({ title: "Radio", source: "bluetooth", status: "playing", controllable: true }, {});
  assert.equal(v.artist, "");
  assert.equal(v.art, null);
});

test("nowView with no track, and with the speaker banner", () => {
  assert.equal(nowView(null, {}).mode, "none");
  const b = nowView(bt(), { speakersDown: true });
  assert.equal(b.mode, "none");
});

test("roomsButtonLabel names the rooms that are on", () => {
  const rooms = [
    { label: "Office", on: true, reachable: true },
    { label: "Bedroom", on: true, reachable: true },
    { label: "Kitchen", on: false, reachable: true },
    { label: "Den", on: true, reachable: false },
  ];
  assert.equal(roomsButtonLabel(rooms), "Office, Bedroom");
  assert.equal(roomsButtonLabel([{ label: "Kitchen", on: false, reachable: true }]), "All rooms off");
  assert.equal(roomsButtonLabel([]), "All rooms off");
});

test("returnDue after 30 s without a touch", () => {
  assert.equal(returnDue(1000, 1000 + 29999), false);
  assert.equal(returnDue(1000, 1000 + 30000), true);
});
