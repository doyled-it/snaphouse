// Tests for the art-manager model. Run with: node --test tests/js
//
// These cover the pure functions the room-picker page uses to read the hub's
// art-index.json and art-control.json (shapes pinned in
// docs/cross-repo-features.md) and to build the write requests the manager
// sends to /api/art/. No DOM, no network: the view glue in rooms.js is not
// tested here, exactly as the rest of the room model is split today.

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  parseArtIndex,
  parseArtControl,
  piecesInFolder,
  rotationSummary,
  intervalOptions,
  apiRequest,
  ALLOWED_INTERVALS,
} from "../../roles/room_picker/files/site/rooms-model.js";

// A full art-index.json body: the built-in Collection folder, a user folder, a
// landscape piece and a portrait piece, and one trash entry.
function indexDoc(overrides = {}) {
  return {
    generated_at: "2026-11-09T07:00:00-08:00",
    folders: [
      { name: "Collection", count: 2, enabled: true, builtin: true },
      { name: "Photos", count: 1, enabled: false, builtin: false },
    ],
    pieces: [
      {
        id: "Collection/wave.jpg",
        folder: "Collection",
        file: "wave.jpg",
        title: "The Great Wave",
        artist: "Hokusai",
        source_url: "https://example.org/wave",
        width: 4000,
        height: 2800,
        landscape: true,
        size: 2481234,
        mtime: 1794125000,
        thumb: "/art-thumbs/aaa.jpg",
        url: "/art/Collection/wave.jpg",
      },
      {
        id: "Collection/tower.jpg",
        folder: "Collection",
        file: "tower.jpg",
        title: "A Tall Tower",
        artist: null,
        source_url: null,
        width: 2000,
        height: 3600,
        landscape: false,
        size: 1200000,
        mtime: 1794125500,
        thumb: "/art-thumbs/bbb.jpg",
        url: "/art/Collection/tower.jpg",
      },
      {
        id: "Photos/beach.jpg",
        folder: "Photos",
        file: "beach.jpg",
        title: "Beach",
        artist: null,
        source_url: null,
        width: 4032,
        height: 3024,
        landscape: true,
        size: 3000000,
        mtime: 1794126000,
        thumb: "/art-thumbs/ccc.jpg",
        url: "/art/Photos/beach.jpg",
      },
    ],
    trash: [
      {
        id: ".trash/2026-11-09T06-58-00/old.jpg",
        original: "old.jpg",
        deleted_at: "2026-11-09T06:58:00-08:00",
        thumb: "/art-thumbs/ddd.jpg",
      },
    ],
    ...overrides,
  };
}

function controlDoc(overrides = {}) {
  return {
    settings: { rotate_minutes: 120, folders_enabled: ["Collection"] },
    command: { seq: 7, action: "next", piece: null, issued_at: "2026-11-09T06:59:10-08:00" },
    ...overrides,
  };
}

test("parseArtIndex reads folders, pieces, and trash from a valid document", () => {
  const index = parseArtIndex(indexDoc());
  assert.equal(index.generatedAt, "2026-11-09T07:00:00-08:00");
  assert.equal(index.folders.length, 2);
  assert.deepEqual(index.folders[0], {
    name: "Collection",
    count: 2,
    enabled: true,
    builtin: true,
  });
  assert.equal(index.pieces.length, 3);
  const wave = index.pieces[0];
  assert.equal(wave.id, "Collection/wave.jpg");
  assert.equal(wave.folder, "Collection");
  assert.equal(wave.title, "The Great Wave");
  assert.equal(wave.artist, "Hokusai");
  assert.equal(wave.sourceUrl, "https://example.org/wave");
  assert.equal(wave.landscape, true);
  assert.equal(wave.thumb, "/art-thumbs/aaa.jpg");
  assert.equal(wave.url, "/art/Collection/wave.jpg");
  assert.equal(index.trash.length, 1);
  assert.equal(index.trash[0].id, ".trash/2026-11-09T06-58-00/old.jpg");
  assert.equal(index.trash[0].original, "old.jpg");
  assert.equal(index.trash[0].thumb, "/art-thumbs/ddd.jpg");
});

test("parseArtIndex returns an empty library for a missing or malformed document", () => {
  for (const bad of [null, undefined, 42, "nope", [], { pieces: "x" }]) {
    const index = parseArtIndex(bad);
    assert.deepEqual(index.folders, []);
    assert.deepEqual(index.pieces, []);
    assert.deepEqual(index.trash, []);
    assert.equal(index.generatedAt, null);
  }
});

test("parseArtIndex drops a piece with a missing id, folder, or title", () => {
  const doc = indexDoc({
    pieces: [
      { id: "Collection/ok.jpg", folder: "Collection", file: "ok.jpg", title: "OK", landscape: true, thumb: "/art-thumbs/x.jpg", url: "/art/Collection/ok.jpg" },
      { folder: "Collection", file: "noid.jpg", title: "No id", landscape: true, thumb: "/art-thumbs/y.jpg", url: "/art/Collection/noid.jpg" },
      { id: "Collection/notitle.jpg", folder: "Collection", file: "notitle.jpg", title: "", landscape: true, thumb: "/art-thumbs/z.jpg", url: "/art/Collection/z.jpg" },
    ],
  });
  const index = parseArtIndex(doc);
  assert.equal(index.pieces.length, 1);
  assert.equal(index.pieces[0].id, "Collection/ok.jpg");
});

test("parseArtIndex rejects a piece whose url or thumb is not a same-origin art path", () => {
  const doc = indexDoc({
    pieces: [
      { id: "Collection/evil.jpg", folder: "Collection", file: "evil.jpg", title: "Evil", landscape: true, thumb: "/art-thumbs/ok.jpg", url: "https://evil.example/x.jpg" },
      { id: "Collection/evil2.jpg", folder: "Collection", file: "evil2.jpg", title: "Evil2", landscape: true, thumb: "http://evil.example/t.jpg", url: "/art/Collection/evil2.jpg" },
      { id: "Collection/evil3.jpg", folder: "Collection", file: "evil3.jpg", title: "Evil3", landscape: true, thumb: "/art-thumbs/../../etc/passwd", url: "/art/Collection/evil3.jpg" },
      { id: "Collection/good.jpg", folder: "Collection", file: "good.jpg", title: "Good", landscape: true, thumb: "/art-thumbs/good.jpg", url: "/art/Collection/good.jpg" },
    ],
  });
  const index = parseArtIndex(doc);
  assert.equal(index.pieces.length, 1);
  assert.equal(index.pieces[0].id, "Collection/good.jpg");
});

test("parseArtIndex keeps the landscape flag and nulls a blank artist or source", () => {
  const index = parseArtIndex(indexDoc());
  const tower = index.pieces[1];
  assert.equal(tower.landscape, false);
  assert.equal(tower.artist, null);
  assert.equal(tower.sourceUrl, null);
});

test("parseArtIndex rejects a non-https source_url", () => {
  const doc = indexDoc({
    pieces: [
      { id: "Collection/a.jpg", folder: "Collection", file: "a.jpg", title: "A", landscape: true, thumb: "/art-thumbs/a.jpg", url: "/art/Collection/a.jpg", source_url: "javascript:alert(1)" },
    ],
  });
  const index = parseArtIndex(doc);
  assert.equal(index.pieces[0].sourceUrl, null);
});

test("parseArtIndex drops a malformed trash entry", () => {
  const doc = indexDoc({
    trash: [
      { id: ".trash/t/good.jpg", original: "good.jpg", deleted_at: "2026-11-09T06:58:00-08:00", thumb: "/art-thumbs/g.jpg" },
      { original: "noid.jpg", deleted_at: "2026-11-09T06:58:00-08:00", thumb: "/art-thumbs/n.jpg" },
      { id: ".trash/t/badthumb.jpg", original: "badthumb.jpg", deleted_at: "x", thumb: "https://evil.example/t.jpg" },
    ],
  });
  const index = parseArtIndex(doc);
  assert.equal(index.trash.length, 1);
  assert.equal(index.trash[0].id, ".trash/t/good.jpg");
});

test("parseArtControl reads settings and the last command", () => {
  const control = parseArtControl(controlDoc());
  assert.equal(control.rotateMinutes, 120);
  assert.deepEqual(control.foldersEnabled, ["Collection"]);
  assert.equal(control.command.seq, 7);
  assert.equal(control.command.action, "next");
  assert.equal(control.command.piece, null);
});

test("parseArtControl defaults to 60 minutes and no folders for a missing document", () => {
  for (const bad of [null, undefined, 7, "x", []]) {
    const control = parseArtControl(bad);
    assert.equal(control.rotateMinutes, 60);
    assert.deepEqual(control.foldersEnabled, []);
    assert.equal(control.command, null);
  }
});

test("parseArtControl falls back to 60 for a disallowed interval", () => {
  const control = parseArtControl(controlDoc({ settings: { rotate_minutes: 45, folders_enabled: ["Collection"] } }));
  assert.equal(control.rotateMinutes, 60);
});

test("parseArtControl keeps only string folder names", () => {
  const control = parseArtControl(controlDoc({ settings: { rotate_minutes: 60, folders_enabled: ["Collection", 3, "", "Photos"] } }));
  assert.deepEqual(control.foldersEnabled, ["Collection", "Photos"]);
});

test("parseArtControl keeps a show command's piece and drops an unknown action", () => {
  const show = parseArtControl(controlDoc({ command: { seq: 9, action: "show", piece: "Photos/beach.jpg", issued_at: "x" } }));
  assert.equal(show.command.action, "show");
  assert.equal(show.command.piece, "Photos/beach.jpg");
  const bad = parseArtControl(controlDoc({ command: { seq: 10, action: "explode", piece: null, issued_at: "x" } }));
  assert.equal(bad.command, null);
});

test("piecesInFolder filters by folder and returns all for null or 'all'", () => {
  const index = parseArtIndex(indexDoc());
  assert.equal(piecesInFolder(index, "Collection").length, 2);
  assert.equal(piecesInFolder(index, "Photos").length, 1);
  assert.equal(piecesInFolder(index, null).length, 3);
  assert.equal(piecesInFolder(index, "all").length, 3);
  assert.equal(piecesInFolder(index, "Missing").length, 0);
});

test("rotationSummary counts landscape pieces in enabled folders", () => {
  const index = parseArtIndex(indexDoc());
  const control = parseArtControl(controlDoc({ settings: { rotate_minutes: 60, folders_enabled: ["Collection"] } }));
  const summary = rotationSummary(index, control);
  // Collection has a landscape wave and a portrait tower; only the wave rotates.
  assert.equal(summary.count, 1);
  assert.equal(summary.empty, false);
  assert.match(summary.text, /1 piece/);
});

test("rotationSummary counts across every enabled folder", () => {
  const index = parseArtIndex(indexDoc());
  const control = parseArtControl(controlDoc({ settings: { rotate_minutes: 60, folders_enabled: ["Collection", "Photos"] } }));
  const summary = rotationSummary(index, control);
  // wave (Collection) and beach (Photos) are landscape; tower is portrait.
  assert.equal(summary.count, 2);
  assert.match(summary.text, /2 pieces/);
});

test("rotationSummary says nothing is in rotation when enabled folders are empty or all portrait", () => {
  const index = parseArtIndex(indexDoc());
  // No folders enabled.
  const none = rotationSummary(index, parseArtControl(controlDoc({ settings: { rotate_minutes: 60, folders_enabled: [] } })));
  assert.equal(none.count, 0);
  assert.equal(none.empty, true);
  assert.match(none.text, /[Nn]othing/);

  // A folder with only a portrait piece enabled: the tower is portrait, so the
  // folder contributes nothing. Build an index whose only enabled folder is all
  // portrait.
  const portraitOnly = parseArtIndex(indexDoc({
    pieces: [
      { id: "Portraits/tall.jpg", folder: "Portraits", file: "tall.jpg", title: "Tall", landscape: false, thumb: "/art-thumbs/t.jpg", url: "/art/Portraits/tall.jpg" },
    ],
  }));
  const allPortrait = rotationSummary(portraitOnly, parseArtControl(controlDoc({ settings: { rotate_minutes: 60, folders_enabled: ["Portraits"] } })));
  assert.equal(allPortrait.count, 0);
  assert.equal(allPortrait.empty, true);
});

test("intervalOptions lists the allowed intervals with human labels", () => {
  const options = intervalOptions();
  assert.deepEqual(options.map((o) => o.minutes), ALLOWED_INTERVALS);
  assert.deepEqual(ALLOWED_INTERVALS, [15, 30, 60, 120, 180, 360, 720, 1440]);
  const byMinutes = new Map(options.map((o) => [o.minutes, o.label]));
  assert.equal(byMinutes.get(15), "15 min");
  assert.equal(byMinutes.get(30), "30 min");
  assert.equal(byMinutes.get(60), "1 hour");
  assert.equal(byMinutes.get(120), "2 hours");
  assert.equal(byMinutes.get(720), "12 hours");
  assert.equal(byMinutes.get(1440), "24 hours");
});

test("apiRequest builds a JSON POST that carries the guard header", () => {
  const req = apiRequest("/api/art/command", { action: "next" });
  assert.equal(req.url, "/api/art/command");
  assert.equal(req.init.method, "POST");
  assert.equal(req.init.headers["X-Snaphouse"], "1");
  assert.equal(req.init.headers["Content-Type"], "application/json");
  assert.equal(req.init.body, JSON.stringify({ action: "next" }));
});

test("apiRequest sends FormData as multipart without a Content-Type header", () => {
  const form = new FormData();
  form.append("folder", "Photos");
  const req = apiRequest("/api/art/upload", form);
  assert.equal(req.url, "/api/art/upload");
  assert.equal(req.init.method, "POST");
  assert.equal(req.init.headers["X-Snaphouse"], "1");
  // The browser sets multipart/form-data with the boundary itself; forcing a
  // Content-Type here would break the boundary.
  assert.equal("Content-Type" in req.init.headers, false);
  assert.equal(req.init.body, form);
});
