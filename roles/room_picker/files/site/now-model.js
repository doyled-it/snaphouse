// Pure rules for the panel's Now playing screen and the ambient screensaver
// (design: docs/superpowers/specs/2026-11-23-panel-now-playing-design.md). No DOM,
// no network, no clock of its own: every function takes the time it needs. The
// tracks are parseNowPlayingDoc results (rooms-model.js).

export const AMBIENT_PAUSE_MS = 5 * 60 * 1000;
export const ROOMS_RETURN_MS = 30 * 1000;

export const AIRPLAY_TEXT = "Control from the phone";

// A track's identity for the paused clock: title, artist and album.
export function trackKey(track) {
  if (!track) return null;
  return [track.title, track.artist || "", track.album || ""].join("\u0001");
}

// "Paused less than 5 minutes ago" is measured on this device's own clock from
// the moment it first sees the track paused, never from a hub timestamp. `prev`
// is the previous result (or null); the result goes back in on the next call.
export function pausedClock(prev, track, nowMs) {
  if (!track) return { key: null, since: null };
  const key = trackKey(track);
  if (track.status !== "paused") return { key, since: null };
  if (prev && prev.key === key && typeof prev.since === "number") return { key, since: prev.since };
  return { key, since: nowMs };
}

// Whether the ambient Now Playing view (rather than the Aurora) is wanted.
export function ambientWanted(track, clock, nowMs, limitMs = AMBIENT_PAUSE_MS) {
  if (!track) return false;
  if (track.status !== "paused") return true;
  const since = clock && typeof clock.since === "number" ? clock.since : nowMs;
  return nowMs - since < limitMs;
}

// What the Now playing screen shows. `phoneName` is the connected phone from the
// Bluetooth devices list, or null. While the speakers cannot be read the screen
// gives way to the Rooms view's banner, so the mode is "none".
export function nowView(track, { phoneName = null, speakersDown = false } = {}) {
  if (!track || speakersDown) {
    return { mode: "none", title: "", artist: "", art: null, paused: false, pausedLine: "", transport: "none", airplayText: "", playing: false };
  }
  const paused = track.status === "paused";
  let transport = "none";
  if (track.source === "airplay") transport = "airplay";
  else if (track.source === "bluetooth" && track.controllable === true) transport = "buttons";
  return {
    mode: "track",
    title: track.title,
    artist: track.artist || "",
    art: track.artUrl || null,
    paused,
    pausedLine: paused ? (phoneName ? `Paused on ${phoneName}` : "Paused") : "",
    transport,
    airplayText: transport === "airplay" ? AIRPLAY_TEXT : "",
    playing: !paused,
  };
}

// The Rooms button's line: the rooms that are on, in the page's order.
export function roomsButtonLabel(rooms) {
  const names = (rooms || []).filter((r) => r.on && r.reachable !== false).map((r) => r.label);
  return names.length > 0 ? names.join(", ") : "All rooms off";
}

// After 30 s on Rooms with no touch the panel goes back to Now playing.
export function returnDue(lastTouchMs, nowMs) {
  return nowMs - lastTouchMs >= ROOMS_RETURN_MS;
}
