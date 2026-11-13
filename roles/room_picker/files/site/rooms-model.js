// The room-picker model: every rule that does not touch the DOM or the network.
//
// A plain ES module imported two ways:
//   - the browser page (rooms.js) loads it as a module script;
//   - node --test (tests/js/rooms-model.test.js) imports it directly.
// So it stays free of browser globals and has no side effects on import.
//
// The data it reads is snapserver's JSON-RPC Server.GetStatus result and the
// machine status files (the cross-repo status schema). The requests it builds
// are snapserver JSON-RPC calls the page sends back.

// Parse a JSON-RPC message without ever throwing.
//
// snapserver can embed cover art and track metadata straight into a
// Server.GetStatus response, and that payload has carried unescaped control
// characters that JSON.parse rejects. A thrown parse must never break the page:
// this returns the parsed value, or null when the body will not parse, so the
// caller keeps its last good render and shows a quiet notice instead.
export function safeJsonParse(text) {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

// Interpret a JSON-RPC HTTP reply and return its `result`, or throw a tagged
// error the caller can tell apart:
//
//   - a non-ok HTTP status (`ok` false) is the unreachable path: a proxy 502/504
//     page when snapserver is down is not a bad payload, so this throws a plain
//     error (no `parseFailed`), which the page reports as "not reachable";
//   - an ok reply whose body will not parse throws with `parseFailed` set, so the
//     page keeps its last render and shows the quiet "status unavailable" notice;
//   - a JSON-RPC error field throws its message.
export function interpretRpcResponse(ok, text) {
  if (!ok) {
    throw new Error("The speakers are not reachable");
  }
  const body = safeJsonParse(text);
  if (body === null) {
    const err = new Error("unparseable JSON-RPC response");
    err.parseFailed = true;
    throw err;
  }
  if (body.error) {
    throw new Error(body.error.message || "JSON-RPC error");
  }
  return body.result;
}

// Build one row per room, in the order the labels are given.
//
// `labels` is the ordered list the page loads from rooms.json:
//   [{ id, label }, ...]
// `id` is the snapclient hostID (living / office / bedroom), which is also the
// snapserver client id. A room snapserver has never seen (no matching client in
// any group) is still shown, marked off and disconnected, so the page always
// renders every room in the house.
//
// A room is "on" when it is audible: the client is not muted and its group is
// not muted. Muting a group (or the client) is how a room is turned off, so the
// living room going off only mutes it; nothing here disconnects anything.
export function roomsFromStatus(status, labels) {
  const byId = clientsById(status);
  return labels.map(({ id, label }) => {
    const found = byId.get(id);
    if (!found) {
      return { id, label, connected: false, on: false, volume: 0, groupId: null, groupMuted: false };
    }
    const { client, group } = found;
    const volume = client.config?.volume ?? {};
    const clientMuted = volume.muted === true;
    const groupMuted = group.muted === true;
    return {
      id,
      label,
      connected: client.connected === true,
      on: !clientMuted && !groupMuted,
      volume: typeof volume.percent === "number" ? volume.percent : 0,
      // The group this client is in: the page unmutes it when a room in a muted
      // group is turned on, so a tap on On always makes the room audible.
      groupId: group.id ?? null,
      groupMuted,
    };
  });
}

// Map every client id to its client object and the group that holds it.
function clientsById(status) {
  const byId = new Map();
  const groups = status?.server?.groups ?? [];
  for (const group of groups) {
    for (const client of group.clients ?? []) {
      byId.set(client.id, { client, group });
    }
  }
  return byId;
}

// The JSON-RPC request that turns a room on or off. Off mutes the client; on
// unmutes it. snapserver keeps the percent, so a room returns at its old volume.
export function toggleRequest(id, on) {
  return {
    method: "Client.SetVolume",
    params: { id, volume: { muted: !on } },
  };
}

// The JSON-RPC request that unmutes a group. Turning on a room whose group is
// muted sends this alongside the client unmute, so the room actually becomes
// audible instead of flipping On and snapping back to Off on the next refresh.
export function groupUnmuteRequest(groupId) {
  return {
    method: "Group.SetMute",
    params: { id: groupId, mute: false },
  };
}

// The JSON-RPC request that sets a room's volume, clamped to 0..100 and rounded
// to a whole percent (the slider can hand us a fractional value).
export function volumeRequest(id, percent) {
  const clamped = Math.max(0, Math.min(100, Math.round(percent)));
  return {
    method: "Client.SetVolume",
    params: { id, volume: { percent: clamped } },
  };
}

// The art shown on the nightstand frame, for the "On the frame" section.
//
// `json` is the parsed art-history.json the hub republishes same-origin (the
// "Art history file" contract in docs/cross-repo-features.md): a current piece
// and a history list, newest first. The hub already hardens the fetch, but the
// page validates again before anything reaches the DOM, so a missing, malformed,
// oversized, or hostile body degrades to an empty result the page hides rather
// than rendering junk or a broken image.
//
// Returns { current: Piece|null, history: [Piece, ...] } where each Piece is
// { file, title, artist|null, source_url|null, shown_at }. The caller builds the
// image path from `file` and must still escape the text fields in the DOM.
export function artFromHistory(json) {
  const empty = { current: null, history: [] };
  if (!json || typeof json !== "object" || Array.isArray(json)) {
    return empty;
  }
  const rawHistory = Array.isArray(json.history) ? json.history : [];
  const history = rawHistory
    .map(validatePiece)
    .filter((piece) => piece !== null)
    .slice(0, MAX_ART_HISTORY);
  return { current: validatePiece(json.current), history };
}

// At most this many entries are kept, matching the producer's cap, so a runaway
// or hostile history cannot grow the list without bound.
const MAX_ART_HISTORY = 50;

// One entry, validated to the contract, or null if it is unusable. file must be
// a bare file name (no path separators, no leading dot) because the page builds
// an /art/<file> URL from it; title and shown_at are required; artist is optional
// (null when absent or not a non-empty string); source_url is kept only when it
// is an https URL, so a http, javascript:, or malformed link never becomes a
// clickable target.
function validatePiece(raw) {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  if (!isSafeFileName(raw.file)) {
    return null;
  }
  if (typeof raw.title !== "string" || raw.title.length === 0) {
    return null;
  }
  if (typeof raw.shown_at !== "string" || raw.shown_at.length === 0) {
    return null;
  }
  const artist =
    typeof raw.artist === "string" && raw.artist.length > 0 ? raw.artist : null;
  return {
    // `id` is the piece id relative to the art folder, added to art-history
    // entries going forward (docs/cross-repo-features.md). It is optional: older
    // entries have none, and the page falls back to `file` when deleting. A
    // present `id` can be folder-deep (e.g. "Photos/beach.jpg"), so unlike `file`
    // it is kept verbatim with no separator check.
    id: nonEmptyStringOrNull(raw.id),
    file: raw.file,
    title: raw.title,
    artist,
    source_url: httpsUrlOrNull(raw.source_url),
    shown_at: raw.shown_at,
  };
}

function isSafeFileName(value) {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    !value.includes("/") &&
    !value.includes("\\") &&
    !value.startsWith(".")
  );
}

function httpsUrlOrNull(value) {
  if (typeof value !== "string") {
    return null;
  }
  try {
    return new URL(value).protocol === "https:" ? value : null;
  } catch (err) {
    return null;
  }
}

// The hub's now-playing document, validated into the shape the card uses, or
// null when nothing is playing or the body is missing or malformed (Review
// Focus 3). `raw` is the parsed now-playing.json (the "Now-playing file"
// contract in docs/cross-repo-features.md): an `updated_at`, a `track` that is
// null when nothing with a title is on House, and a `rooms` list.
//
// Returns { title, artist, album, kind, status, duration, position, positionAt,
// startedAt, artUrl, rooms } or null. Only `title` is required; a title-only
// track (internet radio with no artist, album, duration, or art, Review Focus
// 2) parses with those fields null so the card renders without empty lines.
// `positionAt` and `startedAt` stay as ISO strings for progressAt to anchor on;
// `artUrl` is kept only when it is a same-origin /cover/ path.
export function parseNowPlayingDoc(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return null;
  }
  const t = raw.track;
  if (!t || typeof t !== "object" || Array.isArray(t)) {
    return null;
  }
  if (typeof t.title !== "string" || t.title.length === 0) {
    return null;
  }
  return {
    title: t.title,
    artist: nonEmptyStringOrNull(t.artist),
    album: nonEmptyStringOrNull(t.album),
    kind: nonEmptyStringOrNull(t.kind),
    // Which input is playing ("bluetooth", "airplay", ...); the card shows
    // playback buttons only for the phone's Bluetooth stream.
    source: nonEmptyStringOrNull(t.source),
    status: t.status === "paused" ? "paused" : "playing",
    duration: positiveNumberOrNull(t.duration),
    position: nonNegativeNumberOrNull(t.position),
    positionAt: nonEmptyStringOrNull(t.position_at),
    startedAt: nonEmptyStringOrNull(t.started_at),
    artUrl: coverPathOrNull(t.art_url),
    // `podcast_id` is the hub's additive field (docs/cross-repo-features.md): a
    // matched podcast's iTunes collectionId, or null (and absent on older data).
    // The "Open in" sheet uses it to offer Pocket Casts and the Apple Podcasts
    // show page.
    podcastId: positiveIntegerOrNull(t.podcast_id),
    rooms: Array.isArray(raw.rooms)
      ? raw.rooms.filter((r) => typeof r === "string" && r.length > 0)
      : [],
  };
}

// Where a track is right now, extrapolated from its anchor, or null when it has
// no usable duration. `track` is a parseNowPlayingDoc result and `nowMs` is the
// client clock in ms (Date.now() in the page, a fixed value in tests).
//
// While playing, the elapsed time is the anchor plus the seconds since the
// anchor was taken; while paused it is frozen at the anchor, so a resend for an
// unrelated change never moves the bar backward (Review Focus 1 at the model
// boundary). The anchor is `(position, positionAt)` when both are present,
// otherwise `startedAt` counted from zero. Elapsed is clamped to the duration.
// Returns { elapsed, total, fraction, endsAtMs }.
export function progressAt(track, nowMs) {
  const total = positiveNumberOrNull(track?.duration);
  if (total === null) {
    return null;
  }
  const playing = track.status === "playing";
  const positionAtMs = parseMsOrNull(track.positionAt);
  const position = nonNegativeNumberOrNull(track.position);

  let anchorElapsed;
  let anchorAtMs;
  if (position !== null && positionAtMs !== null) {
    anchorElapsed = position;
    anchorAtMs = positionAtMs;
  } else {
    const startedMs = parseMsOrNull(track.startedAt);
    if (startedMs === null) {
      return null;
    }
    anchorElapsed = 0;
    anchorAtMs = startedMs;
  }

  const raw = playing ? anchorElapsed + (nowMs - anchorAtMs) / 1000 : anchorElapsed;
  const elapsed = Math.max(0, Math.min(total, raw));
  return {
    elapsed,
    total,
    fraction: elapsed / total,
    endsAtMs: nowMs + (total - elapsed) * 1000,
  };
}

// A whole number of seconds as "5:22" or, past an hour, "1:02:03". Invalid or
// negative input renders as "0:00" so the strip never shows "NaN:NaN".
export function formatDuration(seconds) {
  let total = Math.floor(Number(seconds));
  if (!Number.isFinite(total) || total < 0) {
    total = 0;
  }
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const pad = (n) => String(n).padStart(2, "0");
  if (hours > 0) {
    return `${hours}:${pad(minutes)}:${pad(secs)}`;
  }
  return `${minutes}:${pad(secs)}`;
}

// The hub's play history, validated into the list the history section renders.
// `raw` is the parsed play-history.json (the "Play-history file" contract): a
// `plays` array, newest first, at most 50. Invalid entries are dropped and the
// order is kept. Each play is { title, artist, album, duration, startedAt,
// artUrl }; only `title` is required, the rest are nulled when missing, and
// `artUrl` is kept only when it is a same-origin /cover/ path. A missing or
// malformed body yields an empty list the section hides.
export function parsePlayHistory(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return [];
  }
  const plays = Array.isArray(raw.plays) ? raw.plays : [];
  return plays.map(validatePlay).filter((p) => p !== null).slice(0, MAX_PLAY_HISTORY);
}

// At most this many plays are kept, matching the producer's cap, so a runaway or
// hostile file cannot grow the list without bound.
const MAX_PLAY_HISTORY = 50;

function validatePlay(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return null;
  }
  if (typeof raw.title !== "string" || raw.title.length === 0) {
    return null;
  }
  return {
    title: raw.title,
    artist: nonEmptyStringOrNull(raw.artist),
    album: nonEmptyStringOrNull(raw.album),
    // `kind` ("music" or "long_form") and `genre` (a list of strings) route the
    // "Open in" sheet to the right apps (openInLinks); both are optional. `genre`
    // is the hub's additive field (docs/cross-repo-features.md): older history
    // entries have none, so it is null when absent and keeps only non-empty
    // strings when present.
    kind: nonEmptyStringOrNull(raw.kind),
    genre: stringListOrNull(raw.genre),
    duration: positiveNumberOrNull(raw.duration),
    startedAt: nonEmptyStringOrNull(raw.started_at),
    artUrl: coverPathOrNull(raw.art_url),
    // `podcast_id` is the hub's additive field: a matched podcast's iTunes
    // collectionId, or null (absent on entries written before this field). It
    // routes the "Open in" sheet to Pocket Casts and the Apple Podcasts show.
    podcastId: positiveIntegerOrNull(raw.podcast_id),
  };
}

function nonEmptyStringOrNull(value) {
  return typeof value === "string" && value.length > 0 ? value : null;
}

// A list of non-empty, trimmed strings, or null when the value is absent, not an
// array, or has no usable strings. The hub's `genre` field is a list of strings
// (the hub already trims, caps, and drops empties); the page mirrors that so a
// malformed or legacy value is treated as "no genre" and routing falls back
// safely. Anything that is not an array, and any whitespace-only or non-string
// item, is dropped.
function stringListOrNull(value) {
  if (!Array.isArray(value)) {
    return null;
  }
  const list = [];
  for (const item of value) {
    if (typeof item !== "string") {
      continue;
    }
    const trimmed = item.trim();
    if (trimmed.length > 0) {
      list.push(trimmed);
    }
  }
  return list.length > 0 ? list : null;
}

function positiveNumberOrNull(value) {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
}

// A positive integer, or null for anything else. The hub's `podcast_id` is a
// matched show's iTunes collectionId (a positive integer) or null; the page
// mirrors that validation so a missing, non-integer, or non-positive value is
// treated as "no id" and the "Open in" sheet never builds a URL from it.
function positiveIntegerOrNull(value) {
  return typeof value === "number" && Number.isInteger(value) && value > 0 ? value : null;
}

function nonNegativeNumberOrNull(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

function parseMsOrNull(iso) {
  if (typeof iso !== "string") {
    return null;
  }
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? null : ms;
}

// A same-origin cover path, or null. The hub always writes art_url as a
// `/cover/...` path (or null); anything else (an off-origin or protocol-relative
// URL, a different root, a traversal) is rejected so a malformed or hostile file
// can never set a foreign <img src>.
function coverPathOrNull(value) {
  if (typeof value !== "string") {
    return null;
  }
  if (!value.startsWith("/cover/") || value.includes("..")) {
    return null;
  }
  return value;
}

// Turn a now-playing.json response body (text) into the card model, or null.
// The body is parsed here, not in the fetch, so the page can tell a network
// failure (keep the last render) apart from a 200 response with an empty or
// malformed body (hide the card, never show stale data: Review Focus 3). An
// empty, garbled, or non-string body parses to null, exactly like a 404 or a
// body with nothing playing.
export function nowPlayingFromBody(text) {
  return parseNowPlayingDoc(safeJsonParse(text));
}

// Turn a play-history.json response body (text) into the play list, or an empty
// list. Same split as nowPlayingFromBody: an empty or garbled 200 body yields an
// empty list the history section hides, while only a network failure keeps the
// last render.
export function historyFromBody(text) {
  return parsePlayHistory(safeJsonParse(text));
}

// The art manager model: reading the hub's art library documents and building
// the write requests the manager sends. These back the room-picker's art
// manager (the spec's "Room picker: the art manager"); the view glue lives in
// rooms.js, exactly as the rest of the model is split.

// The rotation intervals the service accepts, in minutes (global constraints and
// the "Art control file" contract). The selector offers only these, and a
// control file carrying anything else falls back to the default.
export const ALLOWED_INTERVALS = [15, 30, 60, 120, 180, 360, 720, 1440];

// The default interval when the control file is missing or carries a disallowed
// value (global constraints: default 60).
const DEFAULT_INTERVAL = 60;

// The reserved top-level folder; every other folder is one level under the
// library root.
const BUILTIN_FOLDER = "Collection";

// Parse art-index.json (the "Art index file" contract) into the shape the
// manager renders, or an empty library when the body is missing or malformed.
// The hub hardens the fetch and owns the validation, but the page validates
// again before anything reaches the DOM: a hostile or truncated body degrades to
// an empty library rather than rendering junk or pointing an <img> off-origin
// (Review Focus 1 at the page boundary).
//
// Returns { generatedAt, folders, pieces, trash }. Every piece keeps only a
// same-origin /art/ url and /art-thumbs/ thumb; a piece or trash entry that
// fails validation is dropped, so the caller can render whatever survives.
export function parseArtIndex(raw) {
  const empty = { generatedAt: null, folders: [], pieces: [], trash: [] };
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return empty;
  }
  const folders = Array.isArray(raw.folders)
    ? raw.folders.map(validateFolder).filter((f) => f !== null)
    : [];
  const pieces = Array.isArray(raw.pieces)
    ? raw.pieces.map(validateArtPiece).filter((p) => p !== null)
    : [];
  const trash = Array.isArray(raw.trash)
    ? raw.trash.map(validateTrashEntry).filter((t) => t !== null)
    : [];
  return {
    generatedAt: nonEmptyStringOrNull(raw.generated_at),
    folders,
    pieces,
    trash,
  };
}

function validateFolder(raw) {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  if (typeof raw.name !== "string" || raw.name.length === 0) {
    return null;
  }
  return {
    name: raw.name,
    count: nonNegativeNumberOrNull(raw.count) ?? 0,
    enabled: raw.enabled === true,
    builtin: raw.builtin === true,
  };
}

// One library piece, validated to the contract, or null if it is unusable. id,
// folder, file, and a non-empty title are required; url and thumb must be the
// service's own same-origin paths (so a tampered document can never set a
// foreign or traversing <img src>); landscape is a boolean the rotation summary
// reads; artist and source_url are optional, source kept only when https.
function validateArtPiece(raw) {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  if (!isNonEmptyString(raw.id) || !isNonEmptyString(raw.folder) || !isNonEmptyString(raw.file)) {
    return null;
  }
  if (!isNonEmptyString(raw.title)) {
    return null;
  }
  const url = artPathOrNull(raw.url, "/art/");
  if (url === null) {
    return null;
  }
  // thumb is display-only and may be null while the hub publishes the index
  // before thumbnails are generated (the contract allows it): keep the piece and
  // let the view show a placeholder, so a just-uploaded piece never vanishes and
  // rotationSummary never undercounts. A thumb that is present but not a
  // same-origin art path is still rejected (drop the piece rather than aim an
  // <img> off-origin or at a traversal).
  const thumb = optionalArtPath(raw.thumb, "/art-thumbs/");
  if (thumb === DROP) {
    return null;
  }
  return {
    id: raw.id,
    folder: raw.folder,
    file: raw.file,
    title: raw.title,
    artist: nonEmptyStringOrNull(raw.artist),
    sourceUrl: httpsUrlOrNull(raw.source_url),
    width: positiveNumberOrNull(raw.width),
    height: positiveNumberOrNull(raw.height),
    landscape: raw.landscape === true,
    size: nonNegativeNumberOrNull(raw.size),
    mtime: nonNegativeNumberOrNull(raw.mtime),
    thumb,
    url,
  };
}

function validateTrashEntry(raw) {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  if (!isNonEmptyString(raw.id) || !isNonEmptyString(raw.original)) {
    return null;
  }
  // Like a piece, a trash entry's thumb is display-only and may not be ready
  // yet: keep the entry and show a placeholder when it is null, reject only a
  // present off-origin or traversing path.
  const thumb = optionalArtPath(raw.thumb, "/art-thumbs/");
  if (thumb === DROP) {
    return null;
  }
  return {
    id: raw.id,
    original: raw.original,
    deletedAt: nonEmptyStringOrNull(raw.deleted_at),
    thumb,
  };
}

// Parse art-control.json (the "Art control file" contract) into the settings and
// last command the manager shows, with safe defaults. A missing or malformed
// body yields the default interval, no enabled folders, and no command, so the
// manager still renders.
//
// Returns { rotateMinutes, foldersEnabled, command }. rotateMinutes is clamped
// to the allowed set (default 60); foldersEnabled keeps only non-empty strings;
// command is { seq, action, piece, issuedAt } for a known action, else null.
export function parseArtControl(raw) {
  const fallback = { rotateMinutes: DEFAULT_INTERVAL, foldersEnabled: [], command: null };
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return fallback;
  }
  const settings = raw.settings && typeof raw.settings === "object" ? raw.settings : {};
  const minutes = settings.rotate_minutes;
  const rotateMinutes = ALLOWED_INTERVALS.includes(minutes) ? minutes : DEFAULT_INTERVAL;
  const foldersEnabled = Array.isArray(settings.folders_enabled)
    ? settings.folders_enabled.filter(isNonEmptyString)
    : [];
  return {
    rotateMinutes,
    foldersEnabled,
    command: validateCommand(raw.command),
  };
}

function validateCommand(raw) {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  if (raw.action !== "next" && raw.action !== "previous" && raw.action !== "show") {
    return null;
  }
  return {
    seq: nonNegativeNumberOrNull(raw.seq),
    action: raw.action,
    piece: raw.action === "show" ? nonEmptyStringOrNull(raw.piece) : null,
    issuedAt: nonEmptyStringOrNull(raw.issued_at),
  };
}

// The pieces in one folder, for the library grid. A null folder (or the "all"
// sentinel the default filter uses) returns every piece; an unknown folder name
// returns none. `index` is a parseArtIndex result.
export function piecesInFolder(index, folder) {
  const pieces = index?.pieces ?? [];
  if (folder === null || folder === undefined || folder === "all") {
    return pieces.slice();
  }
  return pieces.filter((piece) => piece.folder === folder);
}

// How many pieces are actually in rotation, and the line the manager shows.
// Only pieces in an enabled folder that pass the landscape rule rotate (the
// frame skips portraits); when none do, the manager says nothing is in rotation
// (Review Focus 5: all enabled folders empty or all portrait). `index` is a
// parseArtIndex result and `control` a parseArtControl result.
//
// Returns { count, empty, text }.
export function rotationSummary(index, control) {
  const enabled = new Set(control?.foldersEnabled ?? []);
  const pieces = index?.pieces ?? [];
  const count = pieces.filter((piece) => enabled.has(piece.folder) && piece.landscape).length;
  if (count === 0) {
    return { count: 0, empty: true, text: "No pictures will be shown. Turn on a folder." };
  }
  const noun = count === 1 ? "picture" : "pictures";
  return { count, empty: false, text: `${count} ${noun} will be shown.` };
}

// Decide whether a library refresh may rebuild the grid right now. Rebuilding
// does els.artGrid.replaceChildren(), which tears down every tile, so an open
// per-piece Options menu (a native <details>) and any half-made "Move to folder"
// selection snap shut. That is only acceptable for a refresh the owner asked for
// (an action, or the first load): a periodic refresh is held while a menu is open
// or a select has focus, and the page re-runs it once the menu closes.
//
// `periodic` is true only for the timer-driven refresh. `menuOpen` is true when
// any Options menu is open, `selectFocused` when a menu's select has focus. A
// non-periodic refresh always rebuilds; a periodic one rebuilds only when the
// grid is idle.
export function libraryRefreshMayRebuild({ periodic, menuOpen, selectFocused } = {}) {
  if (!periodic) {
    return true;
  }
  return !menuOpen && !selectFocused;
}

// The interval selector's options: the allowed set with a human label each. The
// page renders one option per entry and matches the current setting by minutes.
export function intervalOptions() {
  return ALLOWED_INTERVALS.map((minutes) => ({ minutes, label: intervalLabel(minutes) }));
}

function intervalLabel(minutes) {
  if (minutes < 60) {
    return `${minutes} min`;
  }
  const hours = minutes / 60;
  return hours === 1 ? "1 hour" : `${hours} hours`;
}

// Build the request the manager sends to a write endpoint: the url and a fetch
// init that always carries the X-Snaphouse guard header (a cross-site page
// cannot set it, so the service answers 403; the contract and global
// constraints). A plain object body goes as JSON; a FormData body (an upload)
// goes as multipart, and the browser sets the multipart Content-Type with its
// own boundary, so this never sets Content-Type for a form.
//
// Returns { url, init } so the caller does fetch(url, init).
export function apiRequest(path, body) {
  const headers = { [GUARD_HEADER]: GUARD_VALUE };
  let payload = body;
  const isForm = typeof FormData !== "undefined" && body instanceof FormData;
  if (!isForm) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body ?? {});
  }
  return { url: path, init: { method: "POST", headers, body: payload } };
}

// The custom header every write carries. A cross-site form cannot set it without
// a CORS preflight the service never answers, so it blocks CSRF (matches the hub
// service's GUARD_HEADER / GUARD_VALUE).
const GUARD_HEADER = "X-Snaphouse";
const GUARD_VALUE = "1";

function isNonEmptyString(value) {
  return typeof value === "string" && value.length > 0;
}

// A unique sentinel optionalArtPath returns when a present value is not a valid
// same-origin path, telling the caller to drop the whole record. It is distinct
// from null (an absent, tolerated value) so the two cases never collide.
const DROP = Symbol("drop");

// An optional same-origin art path: null when the value is absent (null or
// undefined), the validated path when it is a good same-origin path, or the DROP
// sentinel when it is present but not a same-origin art path. A thumb is allowed
// to be absent while the hub publishes before thumbnails exist; a present but
// off-origin or traversing path is still rejected.
function optionalArtPath(value, prefix) {
  if (value === null || value === undefined) {
    return null;
  }
  const path = artPathOrNull(value, prefix);
  return path === null ? DROP : path;
}

// A same-origin art path under `prefix` (either /art/ or /art-thumbs/), or null.
// The service writes url and thumb as these rooted paths; anything else (an
// off-origin URL, a protocol-relative one, a different root, or a traversal) is
// rejected so a tampered document can never aim an <img> off-origin.
function artPathOrNull(value, prefix) {
  if (typeof value !== "string") {
    return null;
  }
  if (!value.startsWith(prefix) || value.includes("..")) {
    return null;
  }
  return value;
}

// The page-redesign model: the collapsed paged library, the art-history delete
// target, the frame mirror's caption, and the "Open in" sheet. All pure, so the
// view glue in rooms.js stays a thin DOM layer over them.

// The default library page size (global constraints: 24 per page).
const PAGE_SIZE = 24;

// How many pages a library of `total` pieces needs, at least one so the pager
// always has a page to show. A negative or non-numeric total counts as empty.
export function pageCount(total, size = PAGE_SIZE) {
  const n = Number.isFinite(total) && total > 0 ? Math.floor(total) : 0;
  const per = Number.isFinite(size) && size > 0 ? Math.floor(size) : PAGE_SIZE;
  return Math.max(1, Math.ceil(n / per));
}

// The pieces on one zero-indexed page. The page is clamped into range, so a
// filter that shrinks the library (the current page no longer exists) shows the
// last page rather than nothing, and a stray negative shows the first. A missing
// or non-array list yields an empty page.
export function pageSlice(pieces, page, size = PAGE_SIZE) {
  if (!Array.isArray(pieces)) {
    return [];
  }
  const per = Number.isFinite(size) && size > 0 ? Math.floor(size) : PAGE_SIZE;
  const last = pageCount(pieces.length, per) - 1;
  const wanted = Number.isFinite(page) ? Math.floor(page) : 0;
  const clamped = Math.max(0, Math.min(last, wanted));
  const start = clamped * per;
  return pieces.slice(start, start + per);
}

// Which id the art-history Delete button deletes by, or null to hide the button.
// Prefer the entry's `id` (kept verbatim, so a folder-deep piece is targeted
// correctly). Without an id, fall back to the bare `file` name only when it has
// no path separator: an older entry can only be deleted at the library root by
// its plain name, so a name carrying a separator (or a piece actually in a
// folder) is never mis-targeted and the button simply hides (Review Focus 3).
export function historyDeleteTarget(entry) {
  if (!entry || typeof entry !== "object") {
    return null;
  }
  if (typeof entry.id === "string" && entry.id.length > 0) {
    return entry.id;
  }
  const file = entry.file;
  if (typeof file === "string" && file.length > 0 && !file.includes("/") && !file.includes("\\")) {
    return file;
  }
  return null;
}

// The id the Undo after a delete restores, matched against the trash by the
// delete target, or null when no trash entry matches (the caller then points the
// owner at the Trash section instead of silently doing nothing).
//
// `target` is the id the delete was keyed on (what historyDeleteTarget returned,
// or a library piece's id). The hub records a trash entry's `original` as the
// full folder-relative piece id (lib/snaphouse/artlib.py), so the match is on
// that whole id. A piece in a user folder is matched by its folder-relative id
// (e.g. "Photos/beach.jpg"), so Undo restores it instead of silently failing,
// and two pieces that share a basename in different folders never collide. A
// top-level piece's id is just its basename, so it is matched by name without a
// cross-folder fallback that could restore the wrong piece. When a name was
// deleted more than once, the newest entry wins.
export function trashRestoreId(trash, target) {
  if (!Array.isArray(trash) || typeof target !== "string" || target.length === 0) {
    return null;
  }
  const matches = trash
    .filter((e) => e && e.original === target && typeof e.id === "string" && e.id.length > 0)
    .sort((a, b) => (Date.parse(b.deletedAt || "") || 0) - (Date.parse(a.deletedAt || "") || 0));
  return matches.length > 0 ? matches[0].id : null;
}

// The id a piece has after a move into `folder`, found in the fresh index by its
// file name, or null when it is not there (a name collision renames it, so the
// caller then points the owner at the folder instead of guessing).
export function movedPieceId(index, folder, piece) {
  if (!index || !Array.isArray(index.pieces) || !piece || typeof piece.file !== "string") {
    return null;
  }
  const base = (name) => String(name).split("/").pop();
  const hit = index.pieces.find((p) => p.folder === folder && base(p.file) === base(piece.file));
  return hit ? hit.id : null;
}

// The frame mirror's visibility and "Updated" caption, from the hub's
// same-origin /frame.json ({ updated_at, screen }) and the client clock `nowMs`.
// A missing or malformed document (the caller passes null on a 404) hides the
// mirror. A present document shows it; the caption carries the wall-clock time
// from `updated_at` when it parses and is not in the future (clock skew), and is
// otherwise blank so the image is shown honestly without a misleading time
// (Review Focus 1). The clock is read straight off the ISO string's time part,
// so it shows the frame's own local time without a timezone round-trip.
//
// Returns { visible, updatedLabel }.
export function mirrorState(frameJson, nowMs) {
  if (!frameJson || typeof frameJson !== "object" || Array.isArray(frameJson)) {
    return { visible: false, updatedLabel: "" };
  }
  return { visible: true, updatedLabel: updatedLabelFor(frameJson.updated_at, nowMs) };
}

// What the frame is actually showing, so the page captions the mirror by the
// truth rather than always naming the art piece. The frame rotates art but also
// takes over the panel for now playing, weather, a game, or another screen; when
// it does, the art piece under the mirror is the next piece in rotation, not what
// is on the glass, so labelling it "title, artist" with Back / Next reads as if
// that painting were showing.
//
// `frameJson` is the same-origin /frame.json ({ updated_at, screen }). Returns
// { isArt, caption, nextLabel }:
//   - isArt is true when art is on the panel, or when `screen` is missing or
//     unusable (default to today's framing rather than invent a caption);
//   - caption is "" when art shows, otherwise a plain sentence naming the screen;
//   - nextLabel is null when art shows, otherwise "Up next", the label the page
//     puts on the art piece so its controls read as changing what comes next,
//     not what is up now.
export function mirrorCaption(frameJson) {
  const art = { isArt: true, caption: "", nextLabel: null };
  if (!frameJson || typeof frameJson !== "object" || Array.isArray(frameJson)) {
    return art;
  }
  const screen = frameJson.screen;
  if (typeof screen !== "string" || screen.length === 0 || screen === "art") {
    return art;
  }
  // `screen` comes off the network and is only validated to a non-empty string,
  // so look it up on a Map, never a plain object: a key like "constructor" or
  // "toString" would otherwise return an inherited function as the caption.
  return {
    isArt: false,
    caption: SCREEN_CAPTIONS.get(screen) ?? DEFAULT_SCREEN_CAPTION,
    nextLabel: "Up next",
  };
}

// The plain caption for each non-art screen the frame can hold, with a generic
// fallback for a screen name the page does not have specific wording for.
const SCREEN_CAPTIONS = new Map([
  ["now_playing", "Showing what is playing. Art resumes when the audio stops."],
  ["weather", "Showing the weather. Art resumes shortly."],
  ["game", "Showing a game. Art resumes shortly."],
]);

const DEFAULT_SCREEN_CAPTION = "Showing another screen. Art resumes shortly.";

function updatedLabelFor(updatedAt, nowMs) {
  if (typeof updatedAt !== "string") {
    return "";
  }
  const ms = Date.parse(updatedAt);
  if (Number.isNaN(ms)) {
    return "";
  }
  // Reject a timestamp in the future beyond a minute of skew: better no time
  // than a wrong one.
  if (Number.isFinite(nowMs) && ms > nowMs + 60000) {
    return "";
  }
  const clock = clock12FromIso(updatedAt);
  return clock ? `Updated ${clock}` : "";
}

// The "Open in" sheet for a play-history row: a short list of { label, url },
// each a search for the track in an app Android can open (the hub serves plain
// http on the LAN, so the real share sheet is out of reach; the spec's
// fallback). The apps are chosen by the play's `kind` and `genre`:
//   - "music": Apple Music, Spotify, YouTube Music, and a web search.
//   - an audiobook or spoken-word genre (case-insensitive "audiobook",
//     "audio book", or "spoken"): libro.fm and a web search.
//   - a podcast genre: the podcast apps and a web search.
//   - any other "long_form" play (no telling genre): both the podcast apps and
//     libro.fm, plus a web search, since the kind alone cannot tell a podcast
//     from a book.
//   - anything else, or a missing kind: a web search alone.
// The query is the title plus the artist (for music) or the show or book, i.e.
// the album (for long-form), URL-encoded. A play with no title to search yields
// no links. libro.fm's public search is `https://libro.fm/search?q=<query>`
// (verified: the endpoint stays on that path with no redirect).
//
// The podcast apps depend on the play's validated `podcastId` (the hub's matched
// iTunes collectionId). With an id they are Pocket Casts (`https://pca.st/itunes/
// <id>`, which 302s to the show on device and opens in the Pocket Casts app),
// then the Apple Podcasts show page (`https://podcasts.apple.com/podcast/id<id>`),
// then Spotify's search. Without an id Pocket Casts is dropped (it exposes no
// stable public search-by-query URL) and Apple Podcasts falls back to a search,
// so older entries and anything with no matched id keep today's links. The id is
// re-validated here, so a caller passing an unvalidated value never builds a URL.
export function openInLinks(play) {
  if (!play || typeof play !== "object") {
    return [];
  }
  const kind = nonEmptyStringOrNull(play.kind);
  const title = nonEmptyStringOrNull(play.title);
  const artist = nonEmptyStringOrNull(play.artist);
  const album = nonEmptyStringOrNull(play.album);
  const podcastId = positiveIntegerOrNull(play.podcastId);

  const secondTerm = kind === "long_form" ? album || artist : artist;
  const query = [title, secondTerm].filter(Boolean).join(" ");
  if (query.length === 0) {
    return [];
  }
  const q = encodeURIComponent(query);
  const web = { label: "Web search", url: `https://duckduckgo.com/?q=${q}` };
  const libro = { label: "libro.fm", url: `https://libro.fm/search?q=${q}` };
  const applePodcasts =
    podcastId !== null
      ? { label: "Apple Podcasts", url: `https://podcasts.apple.com/podcast/id${podcastId}` }
      : { label: "Apple Podcasts", url: `https://podcasts.apple.com/search?term=${q}` };
  const podcastApps = [
    ...(podcastId !== null
      ? [{ label: "Pocket Casts", url: `https://pca.st/itunes/${podcastId}` }]
      : []),
    applePodcasts,
    { label: "Spotify", url: `https://open.spotify.com/search/${q}` },
  ];

  // Music keeps its apps whatever the genre says (classify never marks talk
  // content as music, so this branch is reached only for actual music).
  if (kind === "music") {
    return [
      { label: "Apple Music", url: `https://music.apple.com/search?term=${q}` },
      { label: "Spotify", url: `https://open.spotify.com/search/${q}` },
      { label: "YouTube Music", url: `https://music.youtube.com/search?q=${q}` },
      web,
    ];
  }

  const genre = (play.genre && Array.isArray(play.genre) ? play.genre : [])
    .filter((item) => typeof item === "string")
    .join(" ")
    .toLowerCase();
  const isAudiobook =
    genre.includes("audiobook") || genre.includes("audio book") || genre.includes("spoken");
  const isPodcast = genre.includes("podcast");

  if (isAudiobook) {
    return [libro, web];
  }
  if (isPodcast) {
    return [...podcastApps, web];
  }
  if (kind === "long_form") {
    // A long-form play whose genre names neither a podcast nor a book: it could
    // be either, so offer both routes.
    return [...podcastApps, libro, web];
  }
  return [web];
}

// The frame-control model: the hash router for the four screens, the hub's
// /frame-control.json (settings and the current pin), the Frame screen's pin
// summary, the Settings form validation, and the request bodies the new
// /api/frame/* endpoints take. All pure, so the view glue in rooms.js stays a
// thin DOM layer, exactly as the rest of the model is split.

// The screens the tab bar switches between, each addressed by the
// URL hash so Back and refresh keep the screen (design "Page: screens").
export const SCREENS = ["listen", "frame", "gallery", "climate", "settings"];

// The default screen when the hash is empty or unknown (design: Listen).
const DEFAULT_SCREEN = "listen";

// Which screen a URL hash selects. A leading "#" is optional and case is
// ignored; an empty, bare, or unknown hash falls back to the default so a
// hand-typed or stale fragment never lands on a blank page. The chosen frame
// rides in the hash as "#frame/<id>", so only the first segment (before any
// "/") names the screen: "#frame/office" is still the Frame screen.
export function screenFromHash(hash) {
  if (typeof hash !== "string") {
    return DEFAULT_SCREEN;
  }
  const raw = hash.replace(/^#/, "").split("/")[0].toLowerCase();
  // "#art" is the old name of the Gallery screen.
  const name = raw === "art" ? "gallery" : raw;
  return SCREENS.includes(name) ? name : DEFAULT_SCREEN;
}

// A frame id is an inventory key: a lowercase letter then up to 31 lowercase
// letters, digits, or hyphens (global constraints; the hub validates the same
// pattern). The page checks it before building any per-frame path or request,
// so a malformed or hostile id from the hash or a tampered document can never
// name an arbitrary frame (Review Focus 5).
const FRAME_ID_RE = /^[a-z][a-z0-9-]{0,31}$/;

// The frame the page falls back to when the hub publishes the old single-frame
// contract (no "frames" map) or no document at all: the nightstand was the only
// frame then, and the old single mirror and history paths still serve its copies
// for this release.
const DEFAULT_FRAME = "nightstand";

function isValidFrameId(value) {
  return typeof value === "string" && FRAME_ID_RE.test(value);
}

// The known frame ids from a parseFrameControl result, in published order. There
// is always at least the nightstand, so the switcher and the per-frame requests
// always have a frame to act on.
export function frameIdsFrom(control) {
  if (!control || typeof control !== "object" || !control.frames) {
    return [DEFAULT_FRAME];
  }
  return Object.keys(control.frames);
}

// Which frame the hash selects, defaulting to the first frame. The chosen frame
// rides in the hash as "#frame/<id>"; a hash that names no frame (another
// screen, or a bare "#frame") keeps the current selection when it is still a
// known frame, else takes the first. An id that is not one of the known frames
// is never selected, so the page never builds a request for an arbitrary frame
// (Review Focus 5). With no frames at all, there is nothing to select.
export function selectedFrameFrom(hash, frameIds, current) {
  const ids = Array.isArray(frameIds) ? frameIds : [];
  if (ids.length === 0) {
    return null;
  }
  const named = frameIdFromHash(hash);
  if (named !== null && ids.includes(named)) {
    return named;
  }
  if (typeof current === "string" && ids.includes(current)) {
    return current;
  }
  return ids[0];
}

// The frame id named in a "#frame/<id>" hash, lowercased, or null when the hash
// is not a frame hash or carries no second segment. Pure string parsing: the
// caller validates the id against the known frames.
function frameIdFromHash(hash) {
  if (typeof hash !== "string") {
    return null;
  }
  const parts = hash.replace(/^#/, "").toLowerCase().split("/");
  if (parts[0] !== "frame" || parts.length < 2 || parts[1].length === 0) {
    return null;
  }
  return parts[1];
}

// The switcher rows for the Frame screen: one per known frame, in published
// order, each { id, label, offline }. A frame is offline when its mirror is
// missing (its frame.json 404s, or has not been republished): the office before
// its Inky arrives shows offline while the nightstand is unaffected (Review
// Focus 1). `mirrors` maps a frame id to its parsed frame.json (or null).
export function frameList(control, mirrors) {
  const have = mirrors && typeof mirrors === "object" ? mirrors : {};
  return frameIdsFrom(control).map((id) => ({
    id,
    label: frameLabel(id),
    offline: !isUsableMirror(have[id]),
  }));
}

// A parsed frame.json is usable when it is a plain object (the "Frame mirror
// file" contract is validated further by mirrorState); anything else, including
// a missing entry, reads as offline.
function isUsableMirror(meta) {
  return Boolean(meta) && typeof meta === "object" && !Array.isArray(meta);
}

// A human label for a frame id: the inventory key titlecased, hyphens as spaces
// ("guest-room" becomes "Guest room"). Nothing identifying: the id is an
// inventory key, not a person or place.
function frameLabel(id) {
  const spaced = String(id).replace(/-/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

// The same-origin URLs for one frame's mirror and history. On the new contract
// each frame is republished under "/frames/<id>/"; on the old single-frame
// contract (legacy) the hub serves only the nightstand's copies at the old
// single paths, so the fallback view reads those (Review Focus 3). A malformed
// id yields null so no arbitrary path is ever built (Review Focus 5). The paths
// are relative, like the rest of the page's fetches, so the proxy decides the
// origin.
export function frameUrls(frameId, legacy) {
  if (legacy) {
    return { history: "art-history.json", image: "frame.png", meta: "frame.json" };
  }
  if (!isValidFrameId(frameId)) {
    return null;
  }
  const base = `frames/${frameId}/`;
  return { history: `${base}art-history.json`, image: `${base}frame.png`, meta: `${base}frame.json` };
}

// The body for POST /api/art/command, always carrying the chosen frame id so a
// rotation command reaches only that frame (the compatible "every frame"
// default is never used by the page; the design: the page always sends a frame
// id). `piece` is required for "show" and omitted otherwise. A bad frame or
// action yields null so the view posts nothing rather than an arbitrary path.
export function artCommandBody(frame, action, piece) {
  if (!isValidFrameId(frame)) {
    return null;
  }
  if (action !== "next" && action !== "previous" && action !== "show") {
    return null;
  }
  if (action === "show") {
    if (typeof piece !== "string" || piece.length === 0) {
      return null;
    }
    return { action, piece, frame };
  }
  return { action, frame };
}

// The per-frame quiet-hours override the Settings screen posts to
// /api/frame/settings. The override is null (fall back to the shared quiet
// hours), { enabled: false } (this frame keeps no quiet hours), or a window
// { start, end } of valid HH:MM times. Returns { ok: true, value } with the
// body (carrying the frame), or { ok: false, error } with a short message, so a
// bad custom time is caught here and explained in place rather than posted.
export function frameQuietHoursBody(frame, override) {
  if (!isValidFrameId(frame)) {
    return { ok: false, error: "That frame is not one the page knows." };
  }
  const quiet = normalizeQuietOverride(override);
  if (quiet === INVALID) {
    return { ok: false, error: "Use 24-hour times like 07:30 for quiet hours." };
  }
  return { ok: true, value: { frame, quiet_hours: quiet } };
}

// The one quiet-hours control a frame shows. A frame follows the shared window
// until it has an override of its own; an override is a window or
// { enabled: false }. Off keeps the shared times so switching back on has
// something sensible to show.
export function effectiveQuietHours(shared, override) {
  if (override && override.enabled === false) {
    return { enabled: false, start: shared.start, end: shared.end };
  }
  if (override && isClock(override.start) && isClock(override.end)) {
    return { enabled: true, start: override.start, end: override.end };
  }
  return { enabled: true, start: shared.start, end: shared.end };
}

// The override stored for the switch and the two times (null for a reset, so
// the frame follows the default hours again), or undefined while a
// time is missing or not a 24-hour clock time (the view then shows the error
// and does not write).
export function quietOverrideFromFields({ enabled, start, end, reset }) {
  if (reset) {
    return null;
  }
  if (!enabled) {
    return { enabled: false };
  }
  if (isClock(start) && isClock(end)) {
    return { start, end };
  }
  return undefined;
}

// A sentinel distinct from null so "fall back to shared" (null) never collides
// with "the value could not be validated" (INVALID).
const INVALID = Symbol("invalid");

function normalizeQuietOverride(override) {
  if (override === null || override === undefined) {
    return null;
  }
  if (typeof override !== "object" || Array.isArray(override)) {
    return INVALID;
  }
  if (override.enabled === false && override.start === undefined && override.end === undefined) {
    return { enabled: false };
  }
  if (isClock(override.start) && isClock(override.end)) {
    return { start: override.start, end: override.end };
  }
  return INVALID;
}

// The three kinds of audio the now-playing toggle covers, in display order
// (design "Kinds of audio"; global constraints). The default is music only.
export const FRAME_KINDS = ["music", "podcast", "audiobook"];
const DEFAULT_KINDS = ["music"];

// The pin targets and durations the pin control offers (design "Pinning";
// global constraints). A body is only built from these exact values.
export const PIN_WHATS = ["art", "weather", "now_playing"];
export const PIN_DURATIONS = ["1h", "4h", "morning", "forever"];

// The documented defaults when a settings field is missing (design "Hub: frame
// settings and pin"): quiet hours 22:00 to 06:00, morning weather on with
// 06:00 to 08:30 weekdays and 07:00 to 09:00 weekends.
const DEFAULT_QUIET_HOURS = { start: "22:00", end: "06:00" };
const DEFAULT_MORNING = {
  enabled: true,
  weekday: ["06:00", "08:30"],
  weekend: ["07:00", "09:00"],
};

// Parse the hub's /frame-control.json into the shared settings and the per-frame
// quiet hours and pins the page shows. The hub owns this document and may not
// have published it yet (it 404s until deployed), so a missing or malformed body
// reads as unavailable: the Settings screen then shows the frame settings as
// unknown rather than inventing values, and the frame keeps running on its own
// local config (Review Focus 1).
//
// Returns { available, legacy, settings, frames }:
//   - `settings` is the shared settings (every field filled with its documented
//     default when missing), or null when unavailable.
//   - `frames` maps each known frame id to { quietHours, pin }: the per-frame
//     quiet-hours override (null means fall back to the shared quiet hours) and
//     the per-frame pin. It always has at least the nightstand, so the switcher
//     and the per-frame requests always have a frame to act on.
//   - `legacy` is true when the document is unavailable or carries the old
//     single-frame contract (no "frames" map). The page then shows only the
//     nightstand and reads the old single mirror and history paths, so a page
//     loaded mid-rollout on the old contract keeps working (Review Focus 3).
export function parseFrameControl(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return {
      available: false,
      legacy: true,
      settings: null,
      frames: { [DEFAULT_FRAME]: { quietHours: null, pin: null } },
    };
  }
  const settings = parseFrameSettings(raw.settings);
  // The new contract carries a "frames" map keyed by inventory frame ids. The
  // old single-frame contract has a top-level "pin" and no map; fold it into the
  // nightstand so the page shows exactly that frame, keeping the old pin.
  if (raw.frames && typeof raw.frames === "object" && !Array.isArray(raw.frames)) {
    const frames = parseFramesMap(raw.frames);
    if (Object.keys(frames).length > 0) {
      return { available: true, legacy: false, settings, frames };
    }
  }
  return {
    available: true,
    legacy: true,
    settings,
    frames: { [DEFAULT_FRAME]: { quietHours: null, pin: parsePin(raw.pin) } },
  };
}

// The "frames" map, keeping only well-formed inventory ids (a malformed or
// hostile key is skipped, never built into a path: Review Focus 5) and parsing
// each entry's per-frame quiet-hours override and pin.
function parseFramesMap(raw) {
  const frames = {};
  for (const [id, entry] of Object.entries(raw)) {
    if (!isValidFrameId(id)) {
      continue;
    }
    const e = entry && typeof entry === "object" && !Array.isArray(entry) ? entry : {};
    frames[id] = { quietHours: parseQuietOverride(e.quiet_hours), pin: parsePin(e.pin) };
  }
  return frames;
}

// A per-frame quiet-hours override, validated into one of its three shapes, or
// null when it should fall back to the shared quiet hours. `{ enabled: false }`
// means the frame keeps no quiet hours; a valid { start, end } is an override; a
// missing or malformed value reads as null (fall back), never as wrong.
function parseQuietOverride(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return null;
  }
  if (raw.enabled === false && raw.start === undefined && raw.end === undefined) {
    return { enabled: false };
  }
  if (isClock(raw.start) && isClock(raw.end)) {
    return { start: raw.start, end: raw.end };
  }
  return null;
}

// Decide the frame-control state to render after a refresh fetch, from the prior
// state and the fetch outcome. There are three failure shapes and they must all
// land on the "unavailable" state, never on the HTML form defaults:
//   - a 404 (the hub has not published the document yet): `json` is null.
//   - a malformed body: `json` is null.
//   - the fetch itself throwing (a network error): `threw` is true.
// A throw while a good document is already on screen is a transient blip, so the
// last-good render is kept; a throw with no good prior state falls to
// unavailable, matching the 404 and malformed paths. Any non-throw outcome parses
// `json`, where null reads as unavailable and an object reads as available.
export function frameControlAfterFetch(prior, { threw, json }) {
  if (threw) {
    if (prior && prior.available) {
      return prior;
    }
    return parseFrameControl(null);
  }
  return parseFrameControl(json);
}

function parseFrameSettings(raw) {
  const s = raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
  const kinds = Array.isArray(s.now_playing_kinds)
    ? FRAME_KINDS.filter((kind) => s.now_playing_kinds.includes(kind))
    : DEFAULT_KINDS.slice();
  return {
    now_playing_kinds: kinds,
    quiet_hours: parseWindowOrDefault(s.quiet_hours, DEFAULT_QUIET_HOURS, false),
    morning_weather: parseMorning(s.morning_weather),
    weather_location: parseWeatherLocation(s.weather_location),
  };
}

// The hub's resolved weather location, validated into { name, latitude,
// longitude, timezone }, or null when none is set or the stored value is
// malformed. The hub geocodes a typed city and stores this shape; the page only
// shows the name, but it validates the whole thing so a tampered document never
// reaches the DOM as a half-filled location.
function parseWeatherLocation(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return null;
  }
  if (typeof raw.name !== "string" || raw.name.length === 0) {
    return null;
  }
  if (!isFiniteNumber(raw.latitude) || !isFiniteNumber(raw.longitude)) {
    return null;
  }
  if (typeof raw.timezone !== "string" || raw.timezone.length === 0) {
    return null;
  }
  return {
    name: raw.name,
    latitude: raw.latitude,
    longitude: raw.longitude,
    timezone: raw.timezone,
  };
}

function isFiniteNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

// The label the Settings screen shows for the weather city: the stored display
// name, or "Not set" when no location is configured (a null location or one the
// page could not validate).
export function weatherLocationLabel(location) {
  if (location && typeof location === "object" && typeof location.name === "string" && location.name.length > 0) {
    return location.name;
  }
  return "Not set";
}

// The body the Save button posts to /api/frame/settings: a typed city for the
// hub to geocode. The query is trimmed; a blank entry yields null so the view
// does nothing rather than post an empty query. Only this typed place name
// leaves the LAN (the hub sends just the city part to the geocoder).
export function weatherSaveBody(query) {
  if (typeof query !== "string") {
    return null;
  }
  const trimmed = query.trim();
  if (trimmed.length === 0) {
    return null;
  }
  return { weather_location: { query: trimmed } };
}

// The body the Clear button posts: a null location the hub stores as "unset",
// so the frame falls back to its own local config.
export function weatherClearBody() {
  return { weather_location: null };
}

// A { start, end } of valid HH:MM times, or the given default when either is
// missing or malformed. `ordered` requires start before end (the morning
// windows); quiet hours wrap midnight, so they pass ordered false.
function parseWindowOrDefault(raw, fallback, ordered) {
  if (!raw || typeof raw !== "object") {
    return { ...fallback };
  }
  if (!isClock(raw.start) || !isClock(raw.end)) {
    return { ...fallback };
  }
  if (ordered && clockMinutes(raw.start) >= clockMinutes(raw.end)) {
    return { ...fallback };
  }
  return { start: raw.start, end: raw.end };
}

function parseMorning(raw) {
  const m = raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
  return {
    enabled: m.enabled !== false,
    weekday: parsePairOrDefault(m.weekday, DEFAULT_MORNING.weekday),
    weekend: parsePairOrDefault(m.weekend, DEFAULT_MORNING.weekend),
  };
}

function parsePairOrDefault(raw, fallback) {
  if (!Array.isArray(raw) || raw.length !== 2) {
    return fallback.slice();
  }
  const [start, end] = raw;
  if (!isClock(start) || !isClock(end) || clockMinutes(start) >= clockMinutes(end)) {
    return fallback.slice();
  }
  return [start, end];
}

// A stored pin, validated into the shape the Frame screen reads, or null. `what`
// must be one of the three targets; a pin with any other target is not something
// the page can show, so it reads as no pin. The other fields are kept as given
// (piece id, until, seq, set_at) or nulled.
function parsePin(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return null;
  }
  if (!PIN_WHATS.includes(raw.what)) {
    return null;
  }
  return {
    what: raw.what,
    piece: nonEmptyStringOrNull(raw.piece),
    until: nonEmptyStringOrNull(raw.until),
    seq: nonNegativeNumberOrNull(raw.seq),
    setAt: nonEmptyStringOrNull(raw.set_at),
  };
}

// The Frame screen's one-line pin status, or null when nothing is pinned. A pin
// whose `until` has already passed reads as no pin, so the page returns to
// showing the schedule rather than a stale "Pinned" line (the hub clears an
// expired pin; the page must not outlive it). A forever pin (until null) says so
// plainly; a present but unparseable `until` drops the clause rather than show a
// wrong time.
//
// `nowMs` is the client clock in ms (Date.now() in the page, a fixed value in
// tests).
export function pinSummary(pin, nowMs) {
  if (!pin || typeof pin !== "object" || !PIN_WHATS.includes(pin.what)) {
    return null;
  }
  const label = PIN_WHAT_LABELS[pin.what];
  if (pin.until === null || pin.until === undefined) {
    return `Pinned: ${label} until you unpin`;
  }
  const ms = Date.parse(pin.until);
  if (Number.isNaN(ms)) {
    return `Pinned: ${label}`;
  }
  if (Number.isFinite(nowMs) && ms <= nowMs) {
    return null;
  }
  const clock = clock12FromIso(pin.until);
  return clock ? `Pinned: ${label} until ${clock}` : `Pinned: ${label}`;
}

const PIN_WHAT_LABELS = {
  art: "this picture",
  weather: "weather",
  now_playing: "now playing",
};

// Validate a subset of the frame settings before the page posts it, mirroring
// the hub's own checks (design "Hub: frame settings and pin"): kinds from the
// three, times as HH:MM, and morning windows whose start is before their end.
// Quiet hours wrap midnight, so they are not required to be ordered. The page
// builds valid input from its own controls, but validating here keeps a bad
// value off the wire and lets the Settings screen explain the problem in place.
//
// Returns { ok: true, value } with the cleaned body to send, or { ok: false,
// error } with a short message in the interface's voice.
export function validateFrameSettings(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    return { ok: false, error: "Settings are not in a form the frame understands." };
  }
  const value = {};

  if ("now_playing_kinds" in input) {
    const kinds = input.now_playing_kinds;
    if (!Array.isArray(kinds) || kinds.some((kind) => !FRAME_KINDS.includes(kind))) {
      return { ok: false, error: "That is not a kind the frame knows." };
    }
    value.now_playing_kinds = kinds.slice();
  }

  if ("quiet_hours" in input) {
    const window = input.quiet_hours;
    if (!window || typeof window !== "object" || !isClock(window.start) || !isClock(window.end)) {
      return { ok: false, error: "Use 24-hour times like 07:30 for quiet hours." };
    }
    value.quiet_hours = { start: window.start, end: window.end };
  }

  if ("morning_weather" in input) {
    const m = input.morning_weather;
    if (!m || typeof m !== "object" || Array.isArray(m)) {
      return { ok: false, error: "Morning weather is not in a form the frame understands." };
    }
    for (const window of [m.weekday, m.weekend]) {
      if (!Array.isArray(window) || window.length !== 2 || !isClock(window[0]) || !isClock(window[1])) {
        return { ok: false, error: "Use 24-hour times like 07:30 for morning weather." };
      }
      if (clockMinutes(window[0]) >= clockMinutes(window[1])) {
        return { ok: false, error: "Morning weather must start before it ends." };
      }
    }
    value.morning_weather = {
      enabled: m.enabled === true,
      weekday: m.weekday.slice(),
      weekend: m.weekend.slice(),
    };
  }

  return { ok: true, value };
}

// The body the /api/frame/pin endpoint takes: the chosen frame plus the pin
// target and duration, or null when any of the three is not one the API accepts
// (the view then does nothing rather than post garbage). The required frame
// scopes the pin to one frame, so pinning the office never moves the nightstand
// (Review Focus 2). The guard header is added by apiRequest, as for every write.
export function framePinBody(frame, what, duration) {
  if (!isValidFrameId(frame) || !PIN_WHATS.includes(what) || !PIN_DURATIONS.includes(duration)) {
    return null;
  }
  return { frame, what, duration };
}

// The body the /api/frame/unpin endpoint takes: the chosen frame the guarded
// POST unpins, or null for a malformed frame id. Unpinning is scoped to one
// frame, so it never clears another frame's pin (Review Focus 2).
export function frameUnpinBody(frame) {
  if (!isValidFrameId(frame)) {
    return null;
  }
  return { frame };
}

// The body the one-tap "Keep showing this" sends: pin the frame's current image
// until it is unpinned. The same body the full form sends for "this art piece /
// until I unpin", or null for a malformed frame id.
export function keepShowingBody(frame) {
  return framePinBody(frame, "art", "forever");
}

// The body that puts a pin back after Unpin was tapped by mistake: the same
// target, for the shortest fixed duration that covers the time it had left
// (until you unpin stays until you unpin). Null without a pin.
export function repinBody(frame, pin, nowMs) {
  if (!pin || typeof pin !== "object") {
    return null;
  }
  let duration = "forever";
  if (pin.until !== null && pin.until !== undefined) {
    const left = Date.parse(pin.until) - nowMs;
    if (Number.isNaN(left)) {
      duration = "morning";
    } else if (left <= 60 * 60000) {
      duration = "1h";
    } else if (left <= 4 * 60 * 60000) {
      duration = "4h";
    } else {
      duration = "morning";
    }
  }
  return framePinBody(frame, pin.what, duration);
}

// What the Frame tab's Keep showing control looks like: hidden without the
// frame-control document, "Keep showing this" with no pin, "Unpin" while one is
// held. `pin` is the chosen frame's pin (already cleared once it has expired).
export function pinControlState(frameControl, pin, showingArt = true) {
  // The pin always holds art, so while another screen (now playing) is on the
  // glass the label says "art" rather than promise to keep that screen.
  const keep = showingArt ? "Keep showing this" : "Keep showing art";
  if (!frameControl || !frameControl.available) {
    return { visible: false, pinned: false, label: keep };
  }
  if (pin) {
    return { visible: true, pinned: true, label: "Unpin" };
  }
  return { visible: true, pinned: false, label: keep };
}

// A 24-hour HH:MM clock string (00:00 to 23:59), the format the hub stores and
// the page sends. Anything else (a single-digit hour, an out-of-range value, a
// different separator) is rejected.
function isClock(value) {
  return typeof value === "string" && /^([01]\d|2[0-3]):[0-5]\d$/.test(value);
}

function clockMinutes(value) {
  const [h, m] = value.split(":");
  return Number(h) * 60 + Number(m);
}

// The 12-hour clock ("4:12") read straight off an ISO string's time part, so it
// shows the frame's own local time without a timezone round-trip, or null when
// the string carries no usable time. Shared with the mirror's "Updated" caption.
function clock12FromIso(iso) {
  if (typeof iso !== "string") {
    return null;
  }
  const match = /T(\d{2}):(\d{2})/.exec(iso);
  if (!match) {
    return null;
  }
  const hour24 = Number(match[1]);
  const hour12 = ((hour24 + 11) % 12) + 1;
  return `${hour12}:${match[2]}`;
}

// --------------------------------------------------------------------------- //
// Pair a phone (Settings): the pure model behind the Bluetooth pairing window.
//
// The page starts a window and polls GET /api/pair/state about once a second
// while a window is open or the Settings screen is visible. The hub answers the
// first phone's pairing request itself (a code shown here could never be read:
// switching to the browser dismisses Android's own pairing dialog), so the page
// shows no code and has nothing to confirm: it tells the person what to do on the
// phone, counts the window down, and reports the outcome when they come back.
// Every write POST carries the X-Snaphouse guard header (apiRequest). All copy and
// timing lives in one place; the view glue in rooms.js stays a thin DOM layer.
// --------------------------------------------------------------------------- //

// The states GET /api/pair/state can report. `idle` is also the safe fallback for
// anything unexpected, which includes the retired `confirm` and `declined`.
const PAIR_STATES = ["idle", "open", "paired", "failed"];

// The sentence shown while a window is open, built so the speaker name is a
// single point of change. The page reads the name from bluetooth.json
// (loadBluetoothName); without it the generic "the house speaker" shows.
export function pairInstruction(speakerName) {
  const speaker =
    typeof speakerName === "string" && speakerName.trim().length > 0
      ? speakerName.trim()
      : "the house speaker";
  return `Now open Bluetooth on this phone, tap Pair new device, choose ${speaker}, and tap Pair. You can stay there; come back here when it's done.`;
}

// Who is pairing right now, once the hub has accepted a phone and the window is
// still finishing; null before that and in every other state.
export function pairProgress(parsed) {
  if (parsed && parsed.state === "open" && typeof parsed.device === "string" && parsed.device.length > 0) {
    return `Pairing with ${parsed.device}.`;
  }
  return null;
}

// The outcome line, its detail, and its tone for a finished window. `phase` is
// the pairPhase result: paired, failed, or closed (the window ended with nobody
// paired). Tone is "ok", "bad", or null for a neutral line.
export function pairOutcomeView(phase, parsed) {
  const device = parsed && typeof parsed.device === "string" && parsed.device.length > 0 ? parsed.device : null;
  const message = parsed && typeof parsed.message === "string" && parsed.message.length > 0 ? parsed.message : null;
  if (phase === "paired") {
    return {
      text: device ? `Paired with ${device}.` : "Phone paired.",
      detail: "It will reconnect on its own.",
      tone: "ok",
    };
  }
  if (phase === "failed") {
    return { text: "Pairing failed.", detail: message, tone: "bad" };
  }
  return { text: "Nobody paired before the window closed.", detail: null, tone: null };
}

// A plain non-empty string, trimmed, or null. The device the phone reports and
// the failure message both reach the DOM as text, so a non-string or empty value
// is treated as absent.
function plainStringOrNull(value) {
  return typeof value === "string" && value.trim().length > 0 ? value.trim() : null;
}

// Read GET /api/pair/state. Returns a normalized record, always with every
// field, so the view never has to guard a missing key. A body that will not
// parse, is not an object, or carries an unknown state collapses to idle: the
// page then shows the start view rather than a broken one. The passkey is always
// dropped: the page shows no code.
export function parsePairState(raw) {
  const data = typeof raw === "string" ? safeJsonParse(raw) : null;
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    return { state: "idle", until: null, passkey: null, device: null, message: null };
  }
  const state = PAIR_STATES.includes(data.state) ? data.state : "idle";
  const until = typeof data.until === "string" && data.until.length > 0 ? data.until : null;
  return {
    state,
    until,
    passkey: null,
    device: plainStringOrNull(data.device),
    message: plainStringOrNull(data.message),
  };
}

// The POST that opens a window. apiRequest sets the method and the X-Snaphouse
// guard header; the body is empty.
export function pairStartRequest() {
  return apiRequest("/api/pair/start", {});
}

// The POST that closes a window now (idempotent).
export function pairStopRequest() {
  return apiRequest("/api/pair/stop", {});
}

// Map a pairing POST's HTTP status and body to a result the page can act on.
// 200/202 carry the new state. 409 is a conflict, but it means different things
// per endpoint, so `endpoint` ("start" | "stop"):
//   - on start, a window is already open (this tab, another tab, or the CLI).
// 403 is the guard rejecting the request. Any other non-2xx is a plain failure.
// The service's own `error` string is used for a generic failure when present,
// with a short fallback otherwise; the 409 copy is page copy, so the raw
// lowercase service string (e.g. "already open") is never shown verbatim.
export function interpretPairResponse(status, text, endpoint = "start") {
  if (status === 200 || status === 202) {
    return { ok: true, state: parsePairState(text) };
  }
  const body = safeJsonParse(text);
  const reason = body && typeof body.error === "string" && body.error.length > 0 ? body.error : null;
  if (status === 403) {
    return { ok: false, forbidden: true, error: reason || "That was not allowed. Try again." };
  }
  if (status === 409) {
    return { ok: false, conflict: true, error: "Pairing is already open." };
  }
  return { ok: false, error: reason || `Couldn't connect (${status}). Try again.` };
}

// After this many polls in a row with no good answer, the pairing service is
// treated as gone (I2). A 120 s window polled about once a second gives plenty
// of room, so five straight failures is a real outage, not a blip.
export const MAX_PAIR_POLL_FAILURES = 5;

// Whether a run of failed polls means contact with the pairing service is lost
// (I2). The page then stops polling and shows a plain "lost contact" view with a
// way to retry or close, rather than leaving a dead window on screen. A
// non-numeric count is never lost, so a missing counter degrades safely.
export function pairLostContact(failures) {
  return typeof failures === "number" && Number.isFinite(failures) && failures >= MAX_PAIR_POLL_FAILURES;
}

// Whether the action buttons are disabled while a POST is in flight (I3). A
// second tap on "Pair a phone" would otherwise reach the
// service as a duplicate and come back 409, painting a false error over a
// successful result. The page disables the controls (and guards the handlers)
// until the request settles.
export function pairControlsDisabled(pending) {
  return pending === true;
}

// A coarse, spoken cue for the countdown, or null for every other second (I4).
// The countdown text updates silently (no live region), so a screen reader is
// not read "1:57, 1:56 ..." over the code the owner needs to hear; only these
// marks are announced, through a dedicated polite region.
export function pairCountdownCue(seconds) {
  if (seconds === 30) {
    return "30 seconds left.";
  }
  if (seconds === 10) {
    return "10 seconds left.";
  }
  return null;
}

// Whole seconds left until an ISO-8601 deadline, never below zero, or null when
// there is no usable deadline. A fractional second rounds up so the final tick
// still reads 1 rather than flashing 0 early.
export function pairCountdown(until, nowMs) {
  const deadline = typeof until === "string" ? Date.parse(until) : NaN;
  if (!Number.isFinite(deadline)) {
    return null;
  }
  return Math.max(0, Math.ceil((deadline - nowMs) / 1000));
}

// A seconds count as m:ss with a zero-padded seconds field. A missing or
// negative value reads as 0:00.
export function formatCountdown(seconds) {
  const total = Number.isFinite(seconds) && seconds > 0 ? Math.floor(seconds) : 0;
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}

// Reduce a parsed state plus whether this page opened a window to the phase the
// view renders. The live and terminal states pass through. `idle` means the
// start view before a window, but a window we opened that returns to idle (timed
// out, stopped, or the service restarted) reads as closed, so the outcome is
// never silently lost.
export function pairPhase(parsed, { started } = {}) {
  const state = parsed && typeof parsed.state === "string" ? parsed.state : "idle";
  if (state !== "idle") {
    return state;
  }
  return started ? "closed" : "idle";
}

// Whether the page may poll GET /api/pair/state. It polls only while the tab is
// visible, and then only when the Settings screen is showing or a window is live
// (open). A hidden tab never polls, so the page never polls in the
// background (contract).
export function shouldPollPair({ documentVisible, settingsVisible, windowActive } = {}) {
  return Boolean(documentVisible) && Boolean(settingsVisible || windowActive);
}

// --------------------------------------------------------------------------- //
// Bluetooth devices (Settings): the phones paired to the hub.
//
// GET /api/pair/devices lists them (name, connected, when last connected) and two
// guarded POSTs act on one by its opaque id: disconnect drops the current
// connection and keeps the pairing; forget removes the pairing, after an inline
// confirm. The id is a keyed hash the hub makes; the page never sees an address.
// The list is polled under the same rule as the pairing section (visible tab,
// Settings showing), so shouldPollPair is reused as is.
// --------------------------------------------------------------------------- //

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// Read GET /api/pair/devices. Returns the devices in the order the hub sent them
// (connected first, then by name), each normalized, or null when the body is not
// a device list so the caller can keep what it had. An entry with no id cannot be
// acted on and is dropped.
export function parseDevices(raw) {
  const data = typeof raw === "string" ? safeJsonParse(raw) : null;
  if (!data || typeof data !== "object" || Array.isArray(data) || !Array.isArray(data.devices)) {
    return null;
  }
  const out = [];
  for (const entry of data.devices) {
    const device = normalizeDevice(entry);
    if (device) {
      out.push(device);
    }
  }
  return out;
}

function normalizeDevice(entry) {
  if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
    return null;
  }
  const id = typeof entry.id === "string" ? entry.id : "";
  if (id.length === 0) {
    return null;
  }
  const lastRaw = typeof entry.last_connected === "string" ? entry.last_connected : null;
  return {
    id,
    name: plainStringOrNull(entry.name) || "Unnamed device",
    connected: entry.connected === true,
    lastConnected: lastRaw !== null && Number.isFinite(Date.parse(lastRaw)) ? lastRaw : null,
  };
}

export function deviceStatus(device) {
  return device && device.connected ? "Connected" : "Not connected";
}

// The "last connected" line for a device that is not connected now; null while it
// is connected (the status already says so). A time ahead of the page's clock
// reads as just now.
export function lastConnectedText(device, nowMs) {
  if (!device || device.connected) {
    return null;
  }
  const at = typeof device.lastConnected === "string" ? Date.parse(device.lastConnected) : NaN;
  if (!Number.isFinite(at)) {
    return "Not seen connected yet";
  }
  const seconds = Math.floor((nowMs - at) / 1000);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  if (minutes < 1) {
    return "Last connected just now";
  }
  if (hours < 1) {
    return `Last connected ${minutes} ${minutes === 1 ? "minute" : "minutes"} ago`;
  }
  if (days < 1) {
    return `Last connected ${hours} ${hours === 1 ? "hour" : "hours"} ago`;
  }
  if (days === 1) {
    return "Last connected yesterday";
  }
  if (days < 7) {
    return `Last connected ${days} days ago`;
  }
  const when = new Date(at);
  const sameYear = when.getFullYear() === new Date(nowMs).getFullYear();
  const dayText = `${MONTHS[when.getMonth()]} ${when.getDate()}`;
  return `Last connected ${sameYear ? dayText : `${dayText}, ${when.getFullYear()}`}`;
}

export function deviceDisconnectRequest(id) {
  return apiRequest("/api/pair/devices/disconnect", { id });
}

export function deviceForgetRequest(id) {
  return apiRequest("/api/pair/devices/forget", { id });
}

// Map a device POST's status and body to a result. `kind` is "disconnect" or
// "forget". 200 carries the updated device (disconnect) or nothing more (forget).
// 404 means the phone is no longer paired (another tab or the CLI got there
// first) and 409 that it was not connected after all; both tell the caller to
// refresh rather than show a raw service string.
export function interpretDeviceResponse(status, text, kind = "disconnect") {
  if (status === 200) {
    if (kind === "forget") {
      return { ok: true };
    }
    return { ok: true, device: normalizeDevice(safeJsonParse(text)) };
  }
  const body = safeJsonParse(text);
  const reason = body && typeof body.error === "string" && body.error.length > 0 ? body.error : null;
  if (status === 403) {
    return { ok: false, forbidden: true, error: reason || "That was not allowed. Try again." };
  }
  if (status === 404) {
    return { ok: false, gone: true, error: "That phone is no longer paired." };
  }
  if (status === 409) {
    return { ok: false, stale: true, error: "That phone is not connected any more." };
  }
  return { ok: false, error: reason || `Couldn't connect (${status}). Try again.` };
}

// Which buttons a row shows. Disconnect only while connected; Forget always. While
// a request is in flight both are disabled. The row being confirmed shows the
// confirm view instead of either button.
export function deviceButtons(device, { pending, confirmingId } = {}) {
  const confirm = Boolean(device) && confirmingId === device.id;
  const disabled = pending === true;
  return {
    disconnect: { visible: Boolean(device && device.connected) && !confirm, disabled },
    forget: { visible: !confirm, disabled },
    confirm,
  };
}

// The inline confirm before a pairing is removed.
export function forgetPrompt(device) {
  const name = device && device.name ? device.name : "this phone";
  return {
    question: `Forget ${name}?`,
    detail: "It will stop connecting to the house speakers until you pair it again.",
    confirm: "Forget",
    cancel: "Keep",
  };
}

// Shown after a forget: the hub no longer knows the phone, but the phone still
// remembers the speaker, and a stale entry there is what stops a fresh pairing.
export function forgetReminder(name) {
  const who = typeof name === "string" && name.length > 0 ? name : "the phone";
  return `Forgot ${who}. On the phone, also forget the house speaker in Bluetooth settings, or pairing again may fail.`;
}
