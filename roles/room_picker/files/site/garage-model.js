// The Garage tab's rules. Pure: no DOM, no fetch, the clock is passed in.
// rooms.js renders what these return. The input is the `garage` key of the
// hub's homebody.json (see the garage section of docs/cross-repo-features.md);
// a document that is not usable parses to null so the tab hides.

export const settingLimits = {
  left_open_minutes: { min: 0, max: 240 },
  away_open_grace_minutes: { min: 0, max: 15 },
};

const STATES = ["closed", "open", "opening", "closing", "stopped", "unknown"];
const MIN = 60 * 1000;

const SETTING_API_KEYS = {
  left_open_minutes: "garage_left_open_minutes",
  away_open_grace_minutes: "garage_away_open_grace_minutes",
  alerts: "garage_alerts",
  remote: "garage_remote",
};

const OBSTRUCTION_LINE = "The opener reports something in the way";
const LOCKOUT_LINE = "Remote lockout is on at the opener";
const AWAY_NOTE = "You are away. This moves the door.";
const NO_MOVEMENT_LINE = "No movement seen yet. Check the card before trying again.";

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function stamp(value) {
  if (typeof value !== "string") {
    return null;
  }
  const ms = Date.parse(value);
  return Number.isNaN(ms) ? null : ms;
}

function flag(value) {
  return typeof value === "boolean" ? value : null;
}

function parseSettings(raw) {
  const s = isObject(raw) ? raw : {};
  return {
    left_open_minutes: Number.isInteger(s.left_open_minutes) ? s.left_open_minutes : null,
    away_open_grace_minutes: Number.isInteger(s.away_open_grace_minutes) ? s.away_open_grace_minutes : null,
    alerts: flag(s.alerts),
    remote: flag(s.remote),
  };
}

function parseRecent(raw) {
  if (!Array.isArray(raw)) {
    return [];
  }
  return raw.filter(isObject).map((entry) => ({
    atMs: stamp(entry.at),
    event: typeof entry.event === "string" ? entry.event : null,
    origin: typeof entry.origin === "string" ? entry.origin : null,
    who: typeof entry.who === "string" ? entry.who : null,
  }));
}

// The parsed garage block from the text of homebody.json, or null when the
// garage module is off (key null or missing) or the block is unusable.
export function parseGarageText(text) {
  let raw;
  try {
    raw = JSON.parse(text);
  } catch {
    return null;
  }
  if (!isObject(raw) || !isObject(raw.garage)) {
    return null;
  }
  const g = raw.garage;
  if (!isObject(g.link) || !isObject(g.door) || typeof g.link.connected !== "boolean") {
    return null;
  }
  const state = STATES.includes(g.door.state) ? g.door.state : "unknown";
  const known = STATES.includes(g.door.last_known_state) && g.door.last_known_state !== "unknown";
  return {
    connected: g.link.connected,
    lastSeenMs: stamp(g.link.last_seen),
    state,
    sinceMs: stamp(g.door.since),
    sinceApproximate: g.door.since_approximate === true,
    position: typeof g.door.position === "number" ? g.door.position : null,
    lastKnownState: known ? g.door.last_known_state : null,
    obstruction: flag(g.obstruction),
    lockout: flag(g.lockout),
    light: flag(g.light),
    settings: parseSettings(g.settings),
    recent: parseRecent(g.recent),
  };
}

// "12 min", "2 h 14 min", "1 d 3 h". Negative ages (a clock that stepped back)
// count as zero.
function span(ms) {
  const mins = Math.floor(Math.max(0, ms) / MIN);
  if (mins < 1) {
    return null;
  }
  if (mins < 60) {
    return `${mins} min`;
  }
  const hours = Math.floor(mins / 60);
  if (hours < 24) {
    const rest = mins % 60;
    return rest ? `${hours} h ${rest} min` : `${hours} h`;
  }
  const days = Math.floor(hours / 24);
  const restHours = hours % 24;
  return restHours ? `${days} d ${restHours} h` : `${days} d`;
}

function durationText(doc, now) {
  if (doc.sinceMs === null) {
    return null;
  }
  const text = span(now - doc.sinceMs);
  const about = doc.sinceApproximate ? "about " : "";
  return text ? `for ${about}${text}` : `for ${about}less than a minute`.replace("about less", "less");
}

function lastSeenText(doc, now) {
  const was = doc.lastKnownState ? ` (was ${doc.lastKnownState})` : "";
  if (doc.lastSeenMs === null) {
    return `Not seen yet${was}`;
  }
  const text = span(now - doc.lastSeenMs);
  return `Last seen ${text ? `${text} ago` : "just now"}${was}`;
}

// What the card shows. The word is UNKNOWN whenever the link is down, whatever
// state the document carries: the card never says closed unless the link is live.
// `buttons` says which buttons are drawn, `disabled` which of those look dead
// (both on unknown; Close while the opener reports something in the way or its
// remote lockout is on).
export function garageView(doc, now) {
  const live = doc.connected && doc.state !== "unknown";
  const state = live ? doc.state : "unknown";
  const word = state.toUpperCase();
  const moving = state === "opening" || state === "closing";
  const buttons = {
    open: state === "closed" || state === "stopped" || state === "unknown",
    close: state === "open" || state === "stopped" || state === "unknown",
  };
  const lines = [];
  if (live && doc.obstruction === true) {
    lines.push(OBSTRUCTION_LINE);
  }
  if (live && doc.lockout === true) {
    lines.push(LOCKOUT_LINE);
  }
  const blocked = lines.length > 0;
  const unknown = state === "unknown";
  return {
    word,
    duration: live && !moving ? durationText(doc, now) : null,
    lastSeen: unknown ? lastSeenText(doc, now) : null,
    buttons,
    disabled: { open: unknown, close: unknown || blocked },
    line: blocked ? lines.join(". ") : null,
    linkLine: doc.connected ? "Opener link: connected" : "Opener link: not connected",
  };
}

// The request body for the keys of `next` that differ from what the hub holds,
// under the names POST /api/homebody/settings takes.
export function settingsBody(written, next) {
  const body = {};
  for (const [key, apiKey] of Object.entries(SETTING_API_KEYS)) {
    if (key in next && next[key] !== written[key]) {
      body[apiKey] = next[key];
    }
  }
  return body;
}

// Whether a tap needs a second, confirming tap. Only the intent response decides
// (`confirm_required`), never the page's own guess about where it is. `intent` is
// that response with the tapped `action` added.
export function confirmState(intent) {
  if (!isObject(intent) || intent.confirm_required !== true) {
    return { needsConfirm: false, text: null, note: null };
  }
  const text = intent.action === "close" ? "Close the garage door?" : "Open the garage door?";
  return { needsConfirm: true, text, note: AWAY_NOTE };
}

const ERROR_LINES = {
  400: "That request expired. Start again.",
  403: "The garage can't be controlled from here right now.",
  409: "The door can't do that right now.",
  423: "Remote lockout is on at the opener.",
  429: "Too many tries. Wait a moment and try again.",
  503: "Can't reach the opener right now.",
  504: NO_MOVEMENT_LINE,
};

// The sentence for a refused or failed command. A timeout (504) always gets the
// fixed no-movement line; the refusals use the hub's own plain sentence when it
// sent one, since 403 and 409 each have several causes.
export function errorLine(status, body) {
  if (status === 504) {
    return NO_MOVEMENT_LINE;
  }
  const sentence = isObject(body) && typeof body.error === "string" ? body.error.trim() : "";
  if (sentence && sentence.length <= 160 && status in ERROR_LINES) {
    return sentence;
  }
  return ERROR_LINES[status] || "Something went wrong. Try again.";
}

// What the page does with the reply to POST garage/intent. An error shows the
// hub's own sentence; a nonce with `confirm_required` true waits for a second
// tap; a nonce with it false goes straight to the command. No nonce is never a
// send.
export function intentOutcome(status, body, action) {
  if (status !== 200 || !isObject(body) || typeof body.nonce !== "string" || body.nonce === "") {
    return { kind: "error", text: errorLine(status === 200 ? 400 : status, body) };
  }
  const confirm = confirmState({ ...body, action });
  if (confirm.needsConfirm) {
    return { kind: "confirm", nonce: body.nonce, text: confirm.text, note: confirm.note };
  }
  return { kind: "send", nonce: body.nonce };
}

// What the page does with the reply to POST garage/command: sent, or the error
// sentence. A command is never retried by the page.
export function commandOutcome(status, body) {
  if (status === 200) {
    return { kind: "sent" };
  }
  return { kind: "error", text: errorLine(status, body) };
}

const SETTING_NAMES = {
  left_open_minutes: "The time before an alert",
  away_open_grace_minutes: "The wait",
};

// "" when the value is a whole number inside the setting's range, else the
// sentence to show next to the field. Nothing out of range is ever written.
export function checkSetting(key, value) {
  const limits = settingLimits[key];
  if (!limits || !Number.isInteger(value) || value < limits.min || value > limits.max) {
    return `${SETTING_NAMES[key] || "This"} must be a whole number between ${limits ? limits.min : 0} and ${limits ? limits.max : 0}.`;
  }
  return "";
}

// One Recent row: "17:40  Closed (from home)". `formatTime` turns the timestamp
// in milliseconds into the clock text, so the rule stays pure.
export function recentLine(entry, formatTime) {
  const word = entry.event ? entry.event[0].toUpperCase() + entry.event.slice(1) : "Changed";
  let from = "";
  if (entry.origin === "home") {
    from = " (from home)";
  } else if (entry.origin === "tunnel") {
    from = entry.who ? ` (from away, ${entry.who})` : " (from away)";
  }
  const when = entry.atMs === null ? "" : formatTime(entry.atMs);
  return when ? `${when}  ${word}${from}` : `${word}${from}`;
}

export function pollMs(screenIsGarage) {
  return screenIsGarage ? 5000 : 60000;
}

export function afterCommandPoll() {
  return { everyMs: 2000, forMs: 15000 };
}

// While the door travels (opening or closing, however it was started) the page
// reads the document every second so the percentage follows it, for at most 30 s
// per movement; then the normal cadence resumes. `since` is when this movement
// was first seen (null when the door is not moving).
export const MOVE_POLL = { everyMs: 1000, forMs: 30000 };

export function movementPoll({ state, since, now }) {
  if (state !== "opening" && state !== "closing") return { fast: false, since: null };
  const start = since === null || since === undefined ? now : since;
  return { fast: now - start < MOVE_POLL.forMs, since: start };
}
