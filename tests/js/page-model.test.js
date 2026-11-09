// Tests for the page-model helpers the room-picker redesign adds. Run with:
// node --test tests/js
//
// These cover the pure functions the rebuilt page uses, with no DOM and no
// network, exactly as the rest of the room model is split:
//   - pageCount / pageSlice: the collapsed, paged library (24 per page).
//   - historyDeleteTarget: which id the art-history Delete button targets.
//   - mirrorState: whether the frame mirror shows and its "Updated" caption.
//   - openInLinks: the "Open in" sheet's per-kind app links.
// It also pins the two additive fields the redesign reads: an art-history
// entry's optional `id` (falls back to `file`) and a play's `kind`.

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  pageCount,
  pageSlice,
  historyDeleteTarget,
  mirrorState,
  mirrorCaption,
  openInLinks,
  artFromHistory,
  parsePlayHistory,
} from "../../roles/room_picker/files/site/rooms-model.js";

// --------------------------------------------------------------------------- #
// pageCount / pageSlice: the paged library
// --------------------------------------------------------------------------- #

test("pageCount returns the number of pages, at least one", () => {
  assert.equal(pageCount(0), 1);
  assert.equal(pageCount(1), 1);
  assert.equal(pageCount(24), 1);
  assert.equal(pageCount(25), 2);
  assert.equal(pageCount(48), 2);
  assert.equal(pageCount(49), 3);
});

test("pageCount honours a custom page size", () => {
  assert.equal(pageCount(50, 10), 5);
  assert.equal(pageCount(51, 10), 6);
});

test("pageCount treats a negative or non-numeric total as empty (one page)", () => {
  assert.equal(pageCount(-5), 1);
  assert.equal(pageCount("x"), 1);
  assert.equal(pageCount(NaN), 1);
});

test("pageSlice returns the pieces on a zero-indexed page", () => {
  const pieces = Array.from({ length: 30 }, (_, i) => i);
  assert.deepEqual(pageSlice(pieces, 0), pieces.slice(0, 24));
  assert.deepEqual(pageSlice(pieces, 1), pieces.slice(24, 30));
});

test("pageSlice clamps an out-of-range page into the valid range", () => {
  const pieces = Array.from({ length: 30 }, (_, i) => i);
  // Page 5 does not exist (only 0 and 1); clamp to the last page.
  assert.deepEqual(pageSlice(pieces, 5), pieces.slice(24, 30));
  // A negative page clamps to the first.
  assert.deepEqual(pageSlice(pieces, -2), pieces.slice(0, 24));
});

test("pageSlice honours a custom page size", () => {
  const pieces = Array.from({ length: 30 }, (_, i) => i);
  assert.deepEqual(pageSlice(pieces, 1, 10), pieces.slice(10, 20));
});

test("pageSlice returns an empty array for a missing or empty list", () => {
  assert.deepEqual(pageSlice([], 0), []);
  assert.deepEqual(pageSlice(null, 0), []);
  assert.deepEqual(pageSlice("nope", 0), []);
});

// --------------------------------------------------------------------------- #
// historyDeleteTarget: which id the art-history Delete button deletes by
// --------------------------------------------------------------------------- #

test("historyDeleteTarget prefers the entry id", () => {
  assert.equal(historyDeleteTarget({ id: "Collection/wave.jpg", file: "wave.jpg" }), "Collection/wave.jpg");
  // The id wins even if a file is also present and differs.
  assert.equal(historyDeleteTarget({ id: "a.jpg", file: "wrong.jpg" }), "a.jpg");
  // A folder-deep id is kept verbatim (delete-by-id targets the right piece).
  assert.equal(historyDeleteTarget({ id: "Photos/beach.jpg" }), "Photos/beach.jpg");
});

test("historyDeleteTarget falls back to a bare file name when there is no id", () => {
  assert.equal(historyDeleteTarget({ file: "wave.jpg" }), "wave.jpg");
  assert.equal(historyDeleteTarget({ id: "", file: "wave.jpg" }), "wave.jpg");
});

test("historyDeleteTarget returns null when a file name holds a separator and there is no id", () => {
  // An older entry without an id can only be targeted by its bare file name at
  // the library root; a name that holds a path separator must not be used, so a
  // piece in a folder is never mis-targeted (Review Focus 3). The button hides.
  assert.equal(historyDeleteTarget({ file: "Photos/beach.jpg" }), null);
  assert.equal(historyDeleteTarget({ file: "sub\\beach.jpg" }), null);
});

test("historyDeleteTarget returns null when there is nothing to target", () => {
  assert.equal(historyDeleteTarget({}), null);
  assert.equal(historyDeleteTarget({ file: "" }), null);
  assert.equal(historyDeleteTarget(null), null);
  assert.equal(historyDeleteTarget("nope"), null);
});

// --------------------------------------------------------------------------- #
// mirrorState: the frame mirror's visibility and "Updated" caption
// --------------------------------------------------------------------------- #

const NOW = Date.parse("2026-11-09T14:40:00-08:00");

test("mirrorState hides the mirror for a missing or malformed frame.json", () => {
  for (const bad of [null, undefined, "", 0, "not an object", [], 42]) {
    assert.deepEqual(mirrorState(bad, NOW), { visible: false, updatedLabel: "" });
  }
});

test("mirrorState shows the mirror with an Updated clock from the timestamp", () => {
  assert.deepEqual(
    mirrorState({ updated_at: "2026-11-09T14:38:00-08:00", screen: "art" }, NOW),
    { visible: true, updatedLabel: "Updated 2:38" }
  );
  assert.deepEqual(
    mirrorState({ updated_at: "2026-11-09T09:05:00-08:00", screen: "now_playing" }, NOW),
    { visible: true, updatedLabel: "Updated 9:05" }
  );
  // Midnight renders as 12, not 0.
  assert.equal(
    mirrorState({ updated_at: "2026-11-09T00:15:00-08:00", screen: "art" }, NOW).updatedLabel,
    "Updated 12:15"
  );
});

test("mirrorState shows the mirror but no clock when the timestamp is unusable", () => {
  // The file is present (so the mirror is real), but the time is missing or
  // unparseable: show the image honestly without a misleading "Updated" time.
  assert.deepEqual(mirrorState({ screen: "art" }, NOW), { visible: true, updatedLabel: "" });
  assert.deepEqual(mirrorState({ updated_at: "nonsense", screen: "art" }, NOW), {
    visible: true,
    updatedLabel: "",
  });
});

test("mirrorState drops a future timestamp (clock skew) rather than show a wrong time", () => {
  assert.deepEqual(
    mirrorState({ updated_at: "2026-11-09T15:00:00-08:00", screen: "art" }, NOW),
    { visible: true, updatedLabel: "" }
  );
});

// --------------------------------------------------------------------------- #
// mirrorCaption: what the frame is showing, and how to frame the art piece
// --------------------------------------------------------------------------- #

test("mirrorCaption keeps today's framing when the frame is showing art", () => {
  // Art is on the panel: no plain caption, and the piece is what is showing, so
  // no "next in rotation" label.
  assert.deepEqual(mirrorCaption({ updated_at: "x", screen: "art" }), {
    isArt: true,
    caption: "",
    nextLabel: null,
  });
});

test("mirrorCaption defaults to art framing when the screen is missing or malformed", () => {
  // Without a usable screen name, keep today's behavior rather than mislabel the
  // piece or invent a caption.
  for (const bad of [null, undefined, "", 0, [], 42, { updated_at: "x" }, { screen: "" }, { screen: 7 }]) {
    assert.deepEqual(mirrorCaption(bad), { isArt: true, caption: "", nextLabel: null });
  }
});

test("mirrorCaption captions a now-playing frame plainly and reframes the piece", () => {
  assert.deepEqual(mirrorCaption({ screen: "now_playing" }), {
    isArt: false,
    caption: "Showing now playing. Art resumes when the audio stops.",
    nextLabel: "Next in rotation",
  });
});

test("mirrorCaption has matching text for weather and a game", () => {
  assert.equal(
    mirrorCaption({ screen: "weather" }).caption,
    "Showing the weather. Art resumes shortly."
  );
  assert.equal(
    mirrorCaption({ screen: "game" }).caption,
    "Showing a game. Art resumes shortly."
  );
});

test("mirrorCaption falls back to a generic line for any other screen", () => {
  assert.deepEqual(mirrorCaption({ screen: "calendar" }), {
    isArt: false,
    caption: "Showing another screen. Art resumes shortly.",
    nextLabel: "Next in rotation",
  });
});

// --------------------------------------------------------------------------- #
// openInLinks: the "Open in" sheet's per-kind app links
// --------------------------------------------------------------------------- #

test("openInLinks offers song apps for a music play, searching title and artist", () => {
  const links = openInLinks({ title: "Song A", artist: "Artist B", album: "Album C", kind: "music" });
  assert.deepEqual(
    links.map((l) => l.label),
    ["Apple Music", "Spotify", "YouTube Music", "Web search"]
  );
  const q = "Song%20A%20Artist%20B";
  assert.equal(links[0].url, `https://music.apple.com/search?term=${q}`);
  assert.equal(links[1].url, `https://open.spotify.com/search/${q}`);
  assert.equal(links[2].url, `https://music.youtube.com/search?q=${q}`);
  assert.equal(links[3].url, `https://duckduckgo.com/?q=${q}`);
});

test("openInLinks offers podcast apps for a long-form play, searching title and show", () => {
  const links = openInLinks({
    title: "Episode 412",
    artist: "A Host",
    album: "The Long Version",
    kind: "long_form",
  });
  assert.deepEqual(
    links.map((l) => l.label),
    ["Apple Podcasts", "Spotify", "Web search"]
  );
  // The show (album) is the second term, not the host.
  const q = "Episode%20412%20The%20Long%20Version";
  assert.equal(links[0].url, `https://podcasts.apple.com/search?term=${q}`);
  assert.equal(links[1].url, `https://open.spotify.com/search/${q}`);
  assert.equal(links[2].url, `https://duckduckgo.com/?q=${q}`);
});

test("openInLinks searches the title alone when there is no artist or show", () => {
  const links = openInLinks({ title: "Some Station", artist: null, album: null, kind: "music" });
  assert.equal(links[0].url, "https://music.apple.com/search?term=Some%20Station");
});

test("openInLinks falls back to a web search for an unknown or missing kind", () => {
  for (const kind of [null, undefined, "weird"]) {
    const links = openInLinks({ title: "A Thing", artist: "Someone", kind });
    assert.deepEqual(links.map((l) => l.label), ["Web search"]);
    assert.equal(links[0].url, "https://duckduckgo.com/?q=A%20Thing%20Someone");
  }
});

test("openInLinks url-encodes reserved characters in the query", () => {
  const links = openInLinks({ title: "AC/DC & Co", artist: "Them", kind: "music" });
  assert.equal(links[0].url, "https://music.apple.com/search?term=AC%2FDC%20%26%20Co%20Them");
});

test("openInLinks returns nothing when there is no title to search", () => {
  assert.deepEqual(openInLinks({ kind: "music" }), []);
  assert.deepEqual(openInLinks(null), []);
  assert.deepEqual(openInLinks("nope"), []);
});

// --------------------------------------------------------------------------- #
// Additive fields the redesign reads
// --------------------------------------------------------------------------- #

test("artFromHistory keeps an entry's id when present, null when absent", () => {
  const art = artFromHistory({
    current: {
      id: "Collection/still.jpg",
      file: "still.jpg",
      title: "A Still Life",
      artist: "A Painter",
      shown_at: "2026-11-06T21:14:00-08:00",
    },
    history: [
      {
        id: "Collection/still.jpg",
        file: "still.jpg",
        title: "A Still Life",
        shown_at: "2026-11-06T21:14:00-08:00",
      },
      {
        // An older entry with no id: id is null, the page falls back to file.
        file: "older.jpg",
        title: "An Older One",
        shown_at: "2026-11-05T09:00:00-08:00",
      },
    ],
  });
  assert.equal(art.current.id, "Collection/still.jpg");
  assert.equal(art.history[0].id, "Collection/still.jpg");
  assert.equal(art.history[1].id, null);
  // historyDeleteTarget reads these straight off the validated entry.
  assert.equal(historyDeleteTarget(art.history[0]), "Collection/still.jpg");
  assert.equal(historyDeleteTarget(art.history[1]), "older.jpg");
});

test("parsePlayHistory keeps each play's kind", () => {
  const plays = parsePlayHistory({
    plays: [
      { title: "A Song", artist: "An Artist", kind: "music", started_at: "2026-11-08T21:01:10-08:00" },
      { title: "Episode 1", album: "A Show", kind: "long_form", started_at: "2026-11-08T20:00:00-08:00" },
      { title: "No kind", started_at: "2026-11-08T19:00:00-08:00" },
    ],
  });
  assert.equal(plays[0].kind, "music");
  assert.equal(plays[1].kind, "long_form");
  assert.equal(plays[2].kind, null);
});
