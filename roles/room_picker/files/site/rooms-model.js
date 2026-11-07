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

// Parse a JSON-RPC message, tolerating a body a strict JSON.parse rejects.
//
// snapserver can embed cover art and track metadata straight into a
// Server.GetStatus response, and that payload has carried unescaped control
// characters that JSON.parse throws on. A thrown parse must never break the
// page: this returns the parsed value, or null when the body will not parse, so
// the caller keeps its last good render and shows a quiet notice instead.
export function safeJsonParse(text) {
  try {
    return JSON.parse(text);
  } catch (err) {
    return null;
  }
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

// What is playing right now, or null.
//
// The rooms play the House meta stream, which carries a playbackStatus and, when
// the sender published it, a metadata block with a title and an artist list. The
// stream the groups are assigned to is preferred over any other, so an inner
// source (AirPlay or Bluetooth) that happens to be playing without a title does
// not mask House's metadata. A playing stream with no track (the "playing, no
// title" case the metadata bridge leaves bare) is treated as nothing to show.
// The artist list is joined for display.
export function nowPlaying(status) {
  const streams = status?.server?.streams ?? [];
  const groups = status?.server?.groups ?? [];
  const groupStreamIds = new Set(
    groups.map((group) => group.stream_id).filter((id) => id)
  );
  // The streams the rooms actually play come first; everything else is a
  // fallback for the unusual case where no group stream is playing.
  const ordered = [
    ...streams.filter((stream) => groupStreamIds.has(stream.id)),
    ...streams.filter((stream) => !groupStreamIds.has(stream.id)),
  ];
  const playing = ordered.find(isPlaying);
  if (!playing) {
    return null;
  }
  const metadata = playing.properties?.metadata;
  const title = metadata?.title;
  if (!title) {
    return null;
  }
  const artistList = metadata.artist;
  const artist = Array.isArray(artistList) ? artistList.join(", ") : artistList ?? "";
  return { title, artist };
}

function isPlaying(stream) {
  return stream.status === "playing" || stream.properties?.playbackStatus === "playing";
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
