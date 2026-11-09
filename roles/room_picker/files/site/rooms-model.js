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
    throw new Error("snapserver is not reachable");
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

// The health line, or null when the whole house is well.
//
// One entry per host: { host, status } where status is the parsed status.json
// object, or null when the fetch failed. The host name travels with the status
// because an unreachable host has no body to read its own name from. A host is
// troubled when it is unreachable, its last verify did not pass, or the commit
// it is running is not the main commit it last saw (an apply in flight or stuck).
export function healthBanner(statuses) {
  const troubled = statuses.filter(isTroubled).map((entry) => entry.host);
  if (troubled.length === 0) {
    return null;
  }
  const names = troubled.join(", ");
  const verb = troubled.length === 1 ? "needs" : "need";
  return `${names} ${verb} attention`;
}

function isTroubled(entry) {
  const status = entry.status;
  if (!status) {
    return true;
  }
  if (status.verify !== "pass") {
    return true;
  }
  if (status.commit !== status.main_commit) {
    return true;
  }
  return false;
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
    status: t.status === "paused" ? "paused" : "playing",
    duration: positiveNumberOrNull(t.duration),
    position: nonNegativeNumberOrNull(t.position),
    positionAt: nonEmptyStringOrNull(t.position_at),
    startedAt: nonEmptyStringOrNull(t.started_at),
    artUrl: coverPathOrNull(t.art_url),
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
    duration: positiveNumberOrNull(raw.duration),
    startedAt: nonEmptyStringOrNull(raw.started_at),
    artUrl: coverPathOrNull(raw.art_url),
  };
}

function nonEmptyStringOrNull(value) {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function positiveNumberOrNull(value) {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
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
    return { count: 0, empty: true, text: "Nothing is in rotation." };
  }
  const noun = count === 1 ? "piece" : "pieces";
  return { count, empty: false, text: `${count} ${noun} in rotation.` };
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
