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
