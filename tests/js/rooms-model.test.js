// Tests for the pure room-picker model. Run with: node --test tests/js
//
// rooms-model.js is a plain ES module imported by both the browser page and
// these tests. It holds every bit of logic that does not touch the DOM or the
// network, so the mapping, clamping, and health rules are checked here without a
// browser.

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  roomsFromStatus,
  nowPlaying,
  toggleRequest,
  volumeRequest,
  healthBanner,
} from "../../roles/room_picker/files/site/rooms-model.js";

// Ordered labels, the shape the page loads from rooms.json.
const LABELS = [
  { id: "living", label: "Living room" },
  { id: "office", label: "Office" },
  { id: "bedroom", label: "Bedroom" },
];

// A Server.GetStatus result with one playing group and one muted group.
function statusFixture() {
  return {
    server: {
      groups: [
        {
          id: "g1",
          muted: false,
          stream_id: "House",
          clients: [
            {
              id: "living",
              connected: true,
              config: { name: "Living room", volume: { muted: false, percent: 80 } },
            },
            {
              id: "office",
              connected: true,
              config: { name: "Office", volume: { muted: true, percent: 55 } },
            },
          ],
        },
        {
          id: "g2",
          muted: true,
          stream_id: "House",
          clients: [
            {
              id: "bedroom",
              connected: false,
              config: { name: "Bedroom", volume: { muted: false, percent: 40 } },
            },
          ],
        },
      ],
      streams: [
        {
          id: "House",
          status: "playing",
          properties: {
            playbackStatus: "playing",
            metadata: { title: "A Song", artist: ["Artist One", "Artist Two"] },
          },
        },
      ],
    },
  };
}

test("roomsFromStatus maps every room in label order", () => {
  const rooms = roomsFromStatus(statusFixture(), LABELS);
  assert.deepEqual(
    rooms.map((r) => r.id),
    ["living", "office", "bedroom"]
  );
  assert.deepEqual(
    rooms.map((r) => r.label),
    ["Living room", "Office", "Bedroom"]
  );
});

test("roomsFromStatus reports connected, on, and volume per room", () => {
  const rooms = roomsFromStatus(statusFixture(), LABELS);
  const living = rooms.find((r) => r.id === "living");
  assert.equal(living.connected, true);
  assert.equal(living.on, true);
  assert.equal(living.volume, 80);
});

test("a client's own mute turns that room off", () => {
  const office = roomsFromStatus(statusFixture(), LABELS).find((r) => r.id === "office");
  assert.equal(office.on, false);
  assert.equal(office.volume, 55);
});

test("a muted group turns every room in it off", () => {
  const bedroom = roomsFromStatus(statusFixture(), LABELS).find((r) => r.id === "bedroom");
  // The bedroom client is not muted itself, but its group is.
  assert.equal(bedroom.on, false);
  assert.equal(bedroom.connected, false);
});

test("roomsFromStatus keeps a room snapserver has never seen, marked off and disconnected", () => {
  const status = { server: { groups: [], streams: [] } };
  const rooms = roomsFromStatus(status, LABELS);
  assert.equal(rooms.length, 3);
  for (const room of rooms) {
    assert.equal(room.connected, false);
    assert.equal(room.on, false);
    assert.equal(room.volume, 0);
  }
});

test("nowPlaying returns title and joined artist for the playing stream", () => {
  assert.deepEqual(nowPlaying(statusFixture()), {
    title: "A Song",
    artist: "Artist One, Artist Two",
  });
});

test("nowPlaying is null when nothing is playing", () => {
  const status = statusFixture();
  status.server.streams[0].status = "idle";
  status.server.streams[0].properties.playbackStatus = "stopped";
  assert.equal(nowPlaying(status), null);
});

test("nowPlaying is null when the playing stream carries no title", () => {
  const status = statusFixture();
  delete status.server.streams[0].properties.metadata;
  assert.equal(nowPlaying(status), null);
});

test("toggleRequest mutes when turning a room off and unmutes when on", () => {
  assert.deepEqual(toggleRequest("living", false), {
    method: "Client.SetVolume",
    params: { id: "living", volume: { muted: true } },
  });
  assert.deepEqual(toggleRequest("living", true), {
    method: "Client.SetVolume",
    params: { id: "living", volume: { muted: false } },
  });
});

test("volumeRequest clamps the percent to 0..100", () => {
  assert.equal(volumeRequest("office", 150).params.volume.percent, 100);
  assert.equal(volumeRequest("office", -5).params.volume.percent, 0);
  assert.equal(volumeRequest("office", 42).params.volume.percent, 42);
});

test("volumeRequest rounds a fractional percent and keeps the method and id", () => {
  const req = volumeRequest("bedroom", 33.6);
  assert.equal(req.method, "Client.SetVolume");
  assert.equal(req.params.id, "bedroom");
  assert.equal(req.params.volume.percent, 34);
});

// healthBanner takes one entry per host: { host, status } where status is the
// parsed status.json object, or null when the host could not be reached. A bare
// null cannot name its own host, so the host name travels alongside it.
function healthy(host, commit = "abc123") {
  return { host, status: { verify: "pass", commit, main_commit: commit } };
}

test("healthBanner returns null when every host passes and is up to date", () => {
  const banner = healthBanner([
    healthy("zimablade"),
    healthy("pi-office"),
    healthy("pi-bedroom"),
    healthy("nightstand"),
  ]);
  assert.equal(banner, null);
});

test("healthBanner names a host whose verify failed", () => {
  const banner = healthBanner([
    healthy("zimablade"),
    { host: "pi-office", status: { verify: "fail", commit: "abc", main_commit: "abc" } },
    healthy("pi-bedroom"),
  ]);
  assert.notEqual(banner, null);
  assert.match(banner, /pi-office/);
  assert.doesNotMatch(banner, /zimablade/);
});

test("healthBanner names a host that is behind main", () => {
  const banner = healthBanner([
    healthy("zimablade"),
    { host: "pi-bedroom", status: { verify: "pass", commit: "old", main_commit: "new" } },
  ]);
  assert.match(banner, /pi-bedroom/);
});

test("healthBanner names an unreachable host", () => {
  const banner = healthBanner([
    healthy("zimablade"),
    { host: "nightstand", status: null },
  ]);
  assert.match(banner, /nightstand/);
});

test("healthBanner names several troubled hosts together", () => {
  const banner = healthBanner([
    { host: "pi-office", status: { verify: "fail", commit: "a", main_commit: "a" } },
    { host: "nightstand", status: null },
  ]);
  assert.match(banner, /pi-office/);
  assert.match(banner, /nightstand/);
});
