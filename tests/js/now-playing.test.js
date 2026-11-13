// Tests for the now-playing model. Run with: node --test tests/js
//
// These cover the four pure functions the room-picker page uses to read the
// hub's now-playing.json and play-history.json (shapes pinned in
// docs/cross-repo-features.md): parsing a now-playing document, extrapolating a
// progress position from its anchor, formatting a duration, and parsing the
// play history. No DOM, no network: the view glue in rooms.js is not tested
// here, exactly as the rooms model is split today.

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  parseNowPlayingDoc,
  progressAt,
  formatDuration,
  parsePlayHistory,
  nowPlayingFromBody,
  historyFromBody,
} from "../../roles/room_picker/files/site/rooms-model.js";

// A full now-playing.json body: a long-form track playing in two rooms, with a
// position anchor and a resolved cover.
function nowPlayingDoc(overrides = {}) {
  return {
    updated_at: "2026-11-08T21:14:03-08:00",
    track: {
      title: "Episode 412",
      artist: "The Long Version",
      album: "The Long Version",
      kind: "long_form",
      source: "bluetooth",
      status: "playing",
      duration: 3120,
      position: 1390,
      position_at: "2026-11-08T21:13:58-08:00",
      started_at: "2026-11-08T20:50:48-08:00",
      art_url: "/cover/3f2a.jpg",
      ...(overrides.track || {}),
    },
    rooms: overrides.rooms ?? ["Bedroom", "Office"],
    ...(overrides.top || {}),
  };
}

// --------------------------------------------------------------------------- #
// parseNowPlayingDoc
// --------------------------------------------------------------------------- #

test("parseNowPlayingDoc reads a full track into the model shape", () => {
  const np = parseNowPlayingDoc(nowPlayingDoc());
  assert.equal(np.title, "Episode 412");
  assert.equal(np.artist, "The Long Version");
  assert.equal(np.album, "The Long Version");
  assert.equal(np.kind, "long_form");
  assert.equal(np.status, "playing");
  assert.equal(np.duration, 3120);
  assert.equal(np.position, 1390);
  assert.equal(np.positionAt, "2026-11-08T21:13:58-08:00");
  assert.equal(np.startedAt, "2026-11-08T20:50:48-08:00");
  assert.equal(np.artUrl, "/cover/3f2a.jpg");
  assert.deepEqual(np.rooms, ["Bedroom", "Office"]);
});

test("parseNowPlayingDoc validates the track's podcast_id into podcastId", () => {
  const good = parseNowPlayingDoc(nowPlayingDoc({ track: { podcast_id: 1200361736 } }));
  assert.equal(good.podcastId, 1200361736);
  // Absent on older data, and anything not a positive integer is rejected.
  assert.equal(parseNowPlayingDoc(nowPlayingDoc()).podcastId, null);
  for (const bad of ["1200361736", 0, -1, 2.5, true]) {
    assert.equal(
      parseNowPlayingDoc(nowPlayingDoc({ track: { podcast_id: bad } })).podcastId,
      null,
      JSON.stringify(bad)
    );
  }
});

test("parseNowPlayingDoc returns null for a missing or non-object body", () => {
  for (const bad of [null, undefined, "", 0, "not an object", []]) {
    assert.equal(parseNowPlayingDoc(bad), null);
  }
});

test("parseNowPlayingDoc returns null when nothing is playing (track null)", () => {
  assert.equal(parseNowPlayingDoc({ updated_at: "x", track: null, rooms: [] }), null);
  assert.equal(parseNowPlayingDoc({ updated_at: "x", rooms: [] }), null);
});

test("parseNowPlayingDoc returns null when the track has no title (malformed)", () => {
  // Review Focus 3: a partial or malformed write must hide the card, not crash.
  assert.equal(parseNowPlayingDoc(nowPlayingDoc({ track: { title: undefined } })), null);
  assert.equal(parseNowPlayingDoc(nowPlayingDoc({ track: { title: "" } })), null);
  assert.equal(parseNowPlayingDoc({ track: 42 }), null);
  assert.equal(parseNowPlayingDoc({ track: "a string" }), null);
});

test("parseNowPlayingDoc keeps a title-only track, nulling the empty fields", () => {
  // Review Focus 2: internet radio with a title but no artist, album, duration,
  // or art must render without empty lines. The model nulls what is missing.
  const np = parseNowPlayingDoc(
    nowPlayingDoc({
      track: {
        title: "Some Station",
        artist: null,
        album: null,
        duration: null,
        position: null,
        position_at: null,
        art_url: null,
      },
    })
  );
  assert.equal(np.title, "Some Station");
  assert.equal(np.artist, null);
  assert.equal(np.album, null);
  assert.equal(np.duration, null);
  assert.equal(np.position, null);
  assert.equal(np.positionAt, null);
  assert.equal(np.artUrl, null);
});

test("parseNowPlayingDoc defaults an unknown status to playing and keeps paused", () => {
  assert.equal(parseNowPlayingDoc(nowPlayingDoc({ track: { status: "paused" } })).status, "paused");
  assert.equal(parseNowPlayingDoc(nowPlayingDoc({ track: { status: "weird" } })).status, "playing");
  assert.equal(parseNowPlayingDoc(nowPlayingDoc({ track: { status: undefined } })).status, "playing");
});

test("parseNowPlayingDoc returns rooms as a clean string list", () => {
  assert.deepEqual(parseNowPlayingDoc(nowPlayingDoc({ rooms: [] })).rooms, []);
  assert.deepEqual(parseNowPlayingDoc(nowPlayingDoc({ top: { rooms: "not a list" } })).rooms, []);
  assert.deepEqual(
    parseNowPlayingDoc(nowPlayingDoc({ rooms: ["Bedroom", "", 7, null, "Office"] })).rooms,
    ["Bedroom", "Office"]
  );
});

test("parseNowPlayingDoc rejects an off-origin or unsafe art_url", () => {
  // art_url must be a same-origin /cover/ path; anything else becomes null so a
  // hostile or malformed file never sets a foreign <img src>.
  assert.equal(parseNowPlayingDoc(nowPlayingDoc({ track: { art_url: "https://evil.example/x.jpg" } })).artUrl, null);
  assert.equal(parseNowPlayingDoc(nowPlayingDoc({ track: { art_url: "//evil.example/x.jpg" } })).artUrl, null);
  assert.equal(parseNowPlayingDoc(nowPlayingDoc({ track: { art_url: "/art/x.jpg" } })).artUrl, null);
  assert.equal(parseNowPlayingDoc(nowPlayingDoc({ track: { art_url: "/cover/../etc/passwd" } })).artUrl, null);
  assert.equal(parseNowPlayingDoc(nowPlayingDoc({ track: { art_url: "/cover/lookup/ok.jpg" } })).artUrl, "/cover/lookup/ok.jpg");
});

test("parseNowPlayingDoc nulls non-finite or negative numeric fields", () => {
  const np = parseNowPlayingDoc(
    nowPlayingDoc({ track: { duration: -3, position: "nope" } })
  );
  assert.equal(np.duration, null);
  assert.equal(np.position, null);
});

// --------------------------------------------------------------------------- #
// progressAt
// --------------------------------------------------------------------------- #

// Build a parsed track quickly for progress tests.
function track(fields) {
  return {
    title: "T",
    status: "playing",
    duration: null,
    position: null,
    positionAt: null,
    startedAt: null,
    ...fields,
  };
}

const AT = "2026-11-08T21:00:00-08:00";
const AT_MS = Date.parse(AT);

test("progressAt is null without a usable duration", () => {
  assert.equal(progressAt(track({ duration: null }), AT_MS), null);
  assert.equal(progressAt(track({ duration: 0 }), AT_MS), null);
});

test("progressAt extrapolates from the position anchor while playing", () => {
  const p = progressAt(
    track({ status: "playing", duration: 300, position: 100, positionAt: AT }),
    AT_MS + 10000
  );
  assert.equal(p.elapsed, 110);
  assert.equal(p.total, 300);
  assert.ok(Math.abs(p.fraction - 110 / 300) < 1e-9);
  // Ends at the anchor's wall-clock finish, stable as now advances.
  assert.equal(p.endsAtMs, AT_MS + 10000 + (300 - 110) * 1000);
});

test("progressAt freezes the elapsed time while paused", () => {
  const paused = track({ status: "paused", duration: 300, position: 100, positionAt: AT });
  assert.equal(progressAt(paused, AT_MS + 10000).elapsed, 100);
  assert.equal(progressAt(paused, AT_MS + 90000).elapsed, 100);
});

test("progressAt clamps elapsed to the duration", () => {
  const p = progressAt(
    track({ status: "playing", duration: 300, position: 290, positionAt: AT }),
    AT_MS + 60000
  );
  assert.equal(p.elapsed, 300);
  assert.equal(p.fraction, 1);
});

test("progressAt falls back to startedAt when there is no position", () => {
  const p = progressAt(
    track({ status: "playing", duration: 300, position: null, positionAt: null, startedAt: AT }),
    AT_MS + 50000
  );
  assert.equal(p.elapsed, 50);
});

test("progressAt is null with a duration but no anchor at all", () => {
  assert.equal(
    progressAt(track({ status: "playing", duration: 300, position: null, positionAt: null, startedAt: null }), AT_MS),
    null
  );
});

test("progressAt never runs backward as time advances", () => {
  // Pins the extrapolation direction (Review Focus 1 at the model boundary):
  // for a fixed anchor, a later now yields a greater-or-equal elapsed.
  const t = track({ status: "playing", duration: 10000, position: 100, positionAt: AT });
  let prev = -1;
  for (const dt of [0, 1000, 2000, 5000, 60000]) {
    const e = progressAt(t, AT_MS + dt).elapsed;
    assert.ok(e >= prev);
    prev = e;
  }
});

// --------------------------------------------------------------------------- #
// formatDuration
// --------------------------------------------------------------------------- #

test("formatDuration renders minutes and seconds", () => {
  assert.equal(formatDuration(322), "5:22");
  assert.equal(formatDuration(62), "1:02");
  assert.equal(formatDuration(5), "0:05");
  assert.equal(formatDuration(0), "0:00");
});

test("formatDuration adds an hours field past an hour", () => {
  assert.equal(formatDuration(3723), "1:02:03");
  assert.equal(formatDuration(3600), "1:00:00");
});

test("formatDuration floors fractional seconds", () => {
  assert.equal(formatDuration(322.9), "5:22");
});

test("formatDuration treats invalid input as zero", () => {
  assert.equal(formatDuration(-5), "0:00");
  assert.equal(formatDuration(NaN), "0:00");
  assert.equal(formatDuration(null), "0:00");
  assert.equal(formatDuration(undefined), "0:00");
});

// --------------------------------------------------------------------------- #
// parsePlayHistory
// --------------------------------------------------------------------------- #

function play(overrides = {}) {
  return {
    title: "A Song",
    artist: "An Artist",
    album: "An Album",
    kind: "music",
    source: "airplay",
    duration: 322,
    started_at: "2026-11-08T21:01:10-08:00",
    art_url: "/cover/history/a.jpg",
    ...overrides,
  };
}

test("parsePlayHistory reads each play into the model shape, newest first", () => {
  const plays = parsePlayHistory({
    plays: [play(), play({ title: "Older", started_at: "2026-11-08T20:00:00-08:00" })],
  });
  assert.equal(plays.length, 2);
  assert.equal(plays[0].title, "A Song");
  assert.equal(plays[0].artist, "An Artist");
  assert.equal(plays[0].album, "An Album");
  assert.equal(plays[0].duration, 322);
  assert.equal(plays[0].startedAt, "2026-11-08T21:01:10-08:00");
  assert.equal(plays[0].artUrl, "/cover/history/a.jpg");
  assert.equal(plays[1].title, "Older");
});

test("parsePlayHistory returns an empty list for a missing or malformed body", () => {
  for (const bad of [null, undefined, "", 0, "nope", [], { plays: "not a list" }, { plays: 7 }]) {
    assert.deepEqual(parsePlayHistory(bad), []);
  }
});

test("parsePlayHistory drops invalid entries and keeps order", () => {
  const plays = parsePlayHistory({
    plays: [
      play({ title: "First" }),
      { artist: "No title" },
      42,
      null,
      play({ title: "Second" }),
    ],
  });
  assert.deepEqual(plays.map((p) => p.title), ["First", "Second"]);
});

test("parsePlayHistory caps the list at 50", () => {
  const raw = { plays: [] };
  for (let i = 0; i < 70; i += 1) {
    raw.plays.push(play({ title: `Song ${i}` }));
  }
  const plays = parsePlayHistory(raw);
  assert.equal(plays.length, 50);
  assert.equal(plays[0].title, "Song 0");
});

test("parsePlayHistory nulls the optional fields when they are missing", () => {
  const [p] = parsePlayHistory({
    plays: [play({ artist: null, album: undefined, duration: "nope", art_url: null })],
  });
  assert.equal(p.artist, null);
  assert.equal(p.album, null);
  assert.equal(p.duration, null);
  assert.equal(p.artUrl, null);
});

test("parsePlayHistory rejects an off-origin art_url", () => {
  const [p] = parsePlayHistory({ plays: [play({ art_url: "https://evil.example/x.jpg" })] });
  assert.equal(p.artUrl, null);
});

test("parsePlayHistory keeps a clean genre list and nulls a malformed one", () => {
  // A list of strings is kept; the page routes on it.
  const [good] = parsePlayHistory({ plays: [play({ genre: ["Audiobook", "Fiction"] })] });
  assert.deepEqual(good.genre, ["Audiobook", "Fiction"]);
  // Anything that is not a list of non-empty strings becomes null, so a hostile
  // or legacy value cannot reach the router as a half-shape.
  for (const bad of ["Audiobook", 7, { g: "x" }, [1, 2], ["", "   "], [], null, undefined]) {
    const [p] = parsePlayHistory({ plays: [play({ genre: bad })] });
    assert.equal(p.genre, null, JSON.stringify(bad));
  }
});

test("parsePlayHistory drops only blank items from a mixed genre list", () => {
  const [p] = parsePlayHistory({ plays: [play({ genre: ["Jazz", 7, "", "Vocal"] })] });
  assert.deepEqual(p.genre, ["Jazz", "Vocal"]);
});

test("parsePlayHistory reads an older entry with no genre key, nulling genre", () => {
  const older = play();
  delete older.genre;
  const [p] = parsePlayHistory({ plays: [older] });
  assert.equal(p.title, "A Song");
  assert.equal(p.genre, null);
});

// --------------------------------------------------------------------------- #
// nowPlayingFromBody / historyFromBody: parse a response body without throwing
// --------------------------------------------------------------------------- #
//
// The page reads the response body as text and turns it into the model here, so
// a 200 response with an empty or garbled body (a partial or in-progress write,
// Review Focus 3) resolves to "nothing playing" rather than throwing. That lets
// the view glue keep the last render only on a real network failure, and hide
// on any unparseable body, exactly like a 404.

test("nowPlayingFromBody parses a good body into the track model", () => {
  const np = nowPlayingFromBody(JSON.stringify(nowPlayingDoc()));
  assert.equal(np.title, "Episode 412");
  assert.deepEqual(np.rooms, ["Bedroom", "Office"]);
});

test("nowPlayingFromBody returns null for an empty body", () => {
  assert.equal(nowPlayingFromBody(""), null);
});

test("nowPlayingFromBody returns null for a garbled body", () => {
  assert.equal(nowPlayingFromBody('{"track": {"title": "cut off'), null);
});

test("nowPlayingFromBody returns null for a valid body with nothing playing", () => {
  assert.equal(nowPlayingFromBody('{"track": null, "rooms": []}'), null);
});

test("nowPlayingFromBody returns null for a non-string body", () => {
  assert.equal(nowPlayingFromBody(undefined), null);
});

test("historyFromBody parses a good body into the play list", () => {
  const plays = historyFromBody(JSON.stringify({ plays: [play(), play({ title: "Older" })] }));
  assert.deepEqual(plays.map((p) => p.title), ["A Song", "Older"]);
});

test("historyFromBody returns an empty list for an empty body", () => {
  assert.deepEqual(historyFromBody(""), []);
});

test("historyFromBody returns an empty list for a garbled body", () => {
  assert.deepEqual(historyFromBody('{"plays": [{"title": "cut'), []);
});

test("historyFromBody returns an empty list for a valid empty history", () => {
  assert.deepEqual(historyFromBody('{"plays": []}'), []);
});

test("parseNowPlayingDoc keeps source as a string or null", () => {
  const doc = nowPlayingDoc();
  assert.equal(parseNowPlayingDoc({ ...doc, track: { ...doc.track, source: "bluetooth" } }).source, "bluetooth");
  assert.equal(parseNowPlayingDoc({ ...doc, track: { ...doc.track, source: 5 } }).source, null);
  const { source, ...rest } = doc.track;
  assert.equal(parseNowPlayingDoc({ ...doc, track: rest }).source, null);
});
