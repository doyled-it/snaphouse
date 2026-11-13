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
  toggleRequest,
  volumeRequest,
  groupUnmuteRequest,
  artFromHistory,
  safeJsonParse,
  interpretRpcResponse,
  interpretControlResponse,
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
    assert.equal(room.groupId, null);
    assert.equal(room.groupMuted, false);
  }
});

test("roomsFromStatus exposes each room's group id and group mute", () => {
  const rooms = roomsFromStatus(statusFixture(), LABELS);
  const living = rooms.find((r) => r.id === "living");
  assert.equal(living.groupId, "g1");
  assert.equal(living.groupMuted, false);
  const bedroom = rooms.find((r) => r.id === "bedroom");
  assert.equal(bedroom.groupId, "g2");
  assert.equal(bedroom.groupMuted, true);
});

test("groupUnmuteRequest unmutes a group so a room in it can be turned on", () => {
  assert.deepEqual(groupUnmuteRequest("g2"), {
    method: "Group.SetMute",
    params: { id: "g2", mute: false },
  });
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

// artFromHistory takes the parsed art-history.json the hub republishes (shape in
// docs/cross-repo-features.md) and returns { current, history } for the page. It
// validates every field, so a malformed, missing, oversized, or hostile body
// never reaches the DOM. history stays newest first, as the producer writes it.
function piece(overrides = {}) {
  return {
    file: "aic-12345.jpg",
    title: "A Still Life",
    artist: "A Painter",
    source_url: "https://example.org/artworks/12345",
    shown_at: "2026-11-06T21:14:00-08:00",
    ...overrides,
  };
}

test("artFromHistory returns the current piece and the history, newest first", () => {
  const current = piece();
  const older = piece({ file: "aic-9.jpg", title: "An Older One", shown_at: "2026-11-05T09:00:00-08:00" });
  const art = artFromHistory({ current, history: [current, older] });
  assert.equal(art.current.file, "aic-12345.jpg");
  assert.equal(art.current.title, "A Still Life");
  assert.equal(art.current.artist, "A Painter");
  assert.equal(art.current.source_url, "https://example.org/artworks/12345");
  assert.deepEqual(
    art.history.map((p) => p.file),
    ["aic-12345.jpg", "aic-9.jpg"]
  );
});

test("artFromHistory yields an empty, hideable result when the body is missing", () => {
  for (const missing of [null, undefined, "", 0, "not an object", []]) {
    const art = artFromHistory(missing);
    assert.equal(art.current, null);
    assert.deepEqual(art.history, []);
  }
});

test("artFromHistory drops malformed pieces and a malformed current", () => {
  const good = piece();
  const art = artFromHistory({
    current: { title: "No file here" },
    history: [
      good,
      { file: "x.jpg" }, // no title
      { file: "y.jpg", title: "No stamp" }, // no shown_at
      { file: "", title: "Empty file", shown_at: "2026-11-01T00:00:00-08:00" },
      { file: "../secret.jpg", title: "Path escape", shown_at: "2026-11-01T00:00:00-08:00" },
      42,
    ],
  });
  // The malformed current is rejected to null.
  assert.equal(art.current, null);
  // Only the one well-formed, safe piece survives.
  assert.deepEqual(
    art.history.map((p) => p.file),
    ["aic-12345.jpg"]
  );
});

test("artFromHistory caps an oversized history at 50 entries", () => {
  const history = [];
  for (let i = 0; i < 80; i += 1) {
    history.push(piece({ file: `aic-${i}.jpg`, shown_at: `2026-11-06T21:${String(i % 60).padStart(2, "0")}:00-08:00` }));
  }
  const art = artFromHistory({ current: history[0], history });
  assert.equal(art.history.length, 50);
  assert.equal(art.history[0].file, "aic-0.jpg");
});

test("artFromHistory drops a non-https source_url but keeps the piece", () => {
  const art = artFromHistory({
    current: piece({ source_url: "http://insecure.example/obj" }),
    history: [
      piece({ source_url: "http://insecure.example/obj" }),
      piece({ file: "j.jpg", source_url: "javascript:alert(1)" }),
      piece({ file: "n.jpg", source_url: null }),
    ],
  });
  assert.equal(art.current.source_url, null);
  assert.equal(art.current.file, "aic-12345.jpg");
  assert.deepEqual(
    art.history.map((p) => p.source_url),
    [null, null, null]
  );
});

test("artFromHistory coerces a missing or non-string artist to null", () => {
  const art = artFromHistory({
    current: piece({ artist: undefined }),
    history: [piece({ artist: 7 }), piece({ file: "b.jpg", artist: "" })],
  });
  assert.equal(art.current.artist, null);
  assert.equal(art.history[0].artist, null);
  assert.equal(art.history[1].artist, null);
});

// --------------------------------------------------------------------------- #
// safeJsonParse: never throw on a malformed Server.GetStatus body
// --------------------------------------------------------------------------- #

test("safeJsonParse returns the parsed value for valid JSON", () => {
  const parsed = safeJsonParse('{"result":{"server":{"groups":[]}}}');
  assert.deepEqual(parsed, { result: { server: { groups: [] } } });
});

test("safeJsonParse returns null for syntactically invalid JSON", () => {
  assert.equal(safeJsonParse("{not json"), null);
});

test("safeJsonParse returns null when a string holds a raw control character", () => {
  // snapserver has emitted unescaped control characters inside metadata, which a
  // strict JSON.parse rejects. The page must keep working, so this yields null
  // rather than throwing.
  const body = '{"result":{"server":{}},"x":"' + String.fromCharCode(1) + '"}';
  assert.equal(safeJsonParse(body), null);
});

test("safeJsonParse returns null for a non-string input", () => {
  assert.equal(safeJsonParse(undefined), null);
});

// --------------------------------------------------------------------------- #
// interpretRpcResponse: a down server is unreachable, not a parse failure
// --------------------------------------------------------------------------- #

test("interpretRpcResponse returns the result for an ok, valid reply", () => {
  const result = interpretRpcResponse(true, '{"result":{"server":{"groups":[]}}}');
  assert.deepEqual(result, { server: { groups: [] } });
});

test("interpretRpcResponse treats a non-ok reply as unreachable, not parseFailed", () => {
  // A proxy 502 page when snapserver is down: not a bad payload. The error must
  // not carry parseFailed, so the page says "not reachable", not "status
  // unavailable".
  let thrown;
  try {
    interpretRpcResponse(false, "<html>502 Bad Gateway</html>");
  } catch (err) {
    thrown = err;
  }
  assert.ok(thrown instanceof Error);
  assert.equal(thrown.parseFailed, undefined);
});

test("interpretRpcResponse tags an ok but unparseable reply as parseFailed", () => {
  let thrown;
  try {
    interpretRpcResponse(true, "{not json");
  } catch (err) {
    thrown = err;
  }
  assert.ok(thrown instanceof Error);
  assert.equal(thrown.parseFailed, true);
});

test("interpretRpcResponse throws a JSON-RPC error message", () => {
  assert.throws(
    () => interpretRpcResponse(true, '{"error":{"code":-32601,"message":"nope"}}'),
    /nope/
  );
});

// interpretControlResponse: snapserver answers a good Stream.Control with an empty body

test("interpretControlResponse treats an ok empty body as success", () => {
  assert.equal(interpretControlResponse(true, ""), null);
});

test("interpretControlResponse treats an ok whitespace-only body as success", () => {
  assert.equal(interpretControlResponse(true, " \n\t "), null);
});

test("interpretControlResponse throws a JSON-RPC error body's message", () => {
  assert.throws(
    () => interpretControlResponse(true, '{"error":{"code":-32602,"message":"bad command"}}'),
    /bad command/
  );
});

test("interpretControlResponse throws on a non-ok reply, even an empty one", () => {
  assert.throws(() => interpretControlResponse(false, ""), /not reachable/);
});

test("interpretControlResponse returns a normal result", () => {
  assert.deepEqual(interpretControlResponse(true, '{"result":{"ok":1}}'), { ok: 1 });
});
