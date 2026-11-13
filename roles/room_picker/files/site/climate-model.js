// The Climate tab's rules. Pure: no DOM, no clock, no fetch. rooms.js renders
// what these return. The input is the hub's homebody.json (see the homebody
// section of docs/cross-repo-features.md); every field may be null, and a
// document that is not usable parses to null so the tab hides.

export const EMPTY_PEOPLE_TEXT = "No phones are set up for climate yet.";

export const SETTING_LIMITS = {
  comfort_f: { min: 60, max: 85 },
  away_f: { min: 65, max: 90 },
  away_minutes: { min: 5, max: 120 },
};

const SETTING_KEYS = ["enabled", "comfort_f", "away_f", "away_minutes"];
const PERSON_STATES = ["home", "away", "unknown"];

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function finite(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function stamp(value) {
  if (typeof value !== "string") {
    return null;
  }
  const ms = Date.parse(value);
  return Number.isNaN(ms) ? null : ms;
}

function parsePerson(raw) {
  if (!isObject(raw) || typeof raw.name !== "string" || raw.name.trim() === "") {
    return null;
  }
  return {
    name: raw.name.trim(),
    state: PERSON_STATES.includes(raw.state) ? raw.state : "unknown",
    lastSeenMs: stamp(raw.last_seen),
  };
}

function parseThermostat(raw) {
  const t = isObject(raw) ? raw : {};
  return {
    reachable: typeof t.reachable === "boolean" ? t.reachable : null,
    mode: typeof t.mode === "string" && t.mode ? t.mode : null,
    setpointF: finite(t.setpoint_f),
    temperatureF: finite(t.temperature_f),
  };
}

function parseLastChange(raw) {
  if (!isObject(raw)) {
    return null;
  }
  const atMs = stamp(raw.at);
  const setpointF = finite(raw.setpoint_f);
  if (atMs === null || setpointF === null) {
    return null;
  }
  return { atMs, setpointF, reason: typeof raw.reason === "string" ? raw.reason : null };
}

// The parsed document, or null when it is not a version 1 document with the
// pieces the screen cannot do without (the three numbers in settings, the flags).
export function parseClimate(raw) {
  if (!isObject(raw) || raw.version !== 1) {
    return null;
  }
  if (typeof raw.enabled !== "boolean" || typeof raw.override !== "boolean") {
    return null;
  }
  if (!Array.isArray(raw.people) || !isObject(raw.thermostat) || !isObject(raw.settings)) {
    return null;
  }
  const s = raw.settings;
  if (finite(s.comfort_f) === null || finite(s.away_f) === null || finite(s.away_minutes) === null) {
    return null;
  }
  return {
    updatedMs: stamp(raw.updated),
    enabled: raw.enabled,
    occupied: typeof raw.occupied === "boolean" ? raw.occupied : null,
    people: raw.people.map(parsePerson).filter(Boolean),
    thermostat: parseThermostat(raw.thermostat),
    override: raw.override,
    lastChange: parseLastChange(raw.last_change),
    settings: { comfort_f: s.comfort_f, away_f: s.away_f, away_minutes: s.away_minutes },
  };
}

export function parseClimateText(text) {
  if (typeof text !== "string" || text === "") {
    return null;
  }
  try {
    return parseClimate(JSON.parse(text));
  } catch (_) {
    return null;
  }
}

const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

export function relativeSeen(ms, now) {
  if (ms === null || ms === undefined) {
    return "";
  }
  const age = now - ms;
  if (age < MIN) {
    return "seen just now";
  }
  if (age < HOUR) {
    return `seen ${Math.floor(age / MIN)} min ago`;
  }
  if (age < DAY) {
    return `seen ${Math.floor(age / HOUR)} hr ago`;
  }
  const days = Math.floor(age / DAY);
  return days === 1 ? "seen yesterday" : `seen ${days} days ago`;
}

const STATE_LABELS = { home: "Home", away: "Away", unknown: "Not sure yet" };

// `lastKnown` is true when the whole document is stale, so the page can mute it.
export function peopleLines(doc, now, lastKnown = false) {
  return doc.people.map((person) => ({
    name: person.name,
    state: person.state,
    label: STATE_LABELS[person.state],
    seen: relativeSeen(person.lastSeenMs, now),
    lastKnown,
  }));
}

const MODE_LABELS = { off: "Off", cool: "Cooling", heat: "Heating", auto: "Auto" };

function modeLabel(mode) {
  if (!mode) {
    return "";
  }
  if (MODE_LABELS[mode]) {
    return MODE_LABELS[mode];
  }
  const words = mode.replace(/[_-]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function degrees(value) {
  return value === null ? "--" : `${Math.round(value)}°`;
}

// The hub's helper rewrites the document every few seconds. Past this age the
// helper has probably stopped, and what the page shows is the last thing it knew.
export const STALE_AFTER_MS = 3 * MIN;

function ageWords(age) {
  if (age < HOUR) {
    return `${Math.max(1, Math.floor(age / MIN))} min`;
  }
  if (age < DAY) {
    return `${Math.floor(age / HOUR)} hr`;
  }
  const days = Math.floor(age / DAY);
  return days === 1 ? "1 day" : `${days} days`;
}

// { stale, text }: text is the quiet line to show, empty when the document is
// fresh or carries no readable time (then there is nothing to say).
export function staleness(doc, now) {
  if (doc.updatedMs === null || doc.updatedMs === undefined) {
    return { stale: false, text: "" };
  }
  const age = now - doc.updatedMs;
  if (age <= STALE_AFTER_MS) {
    return { stale: false, text: "" };
  }
  return {
    stale: true,
    text: `Last update ${ageWords(age)} ago. The climate helper may be stopped.`,
  };
}

// `stale` is the whole document being old. Either that or an unreachable
// thermostat makes the mode and setpoint last-known values, not live ones.
export function thermostatView(doc, stale = false) {
  const t = doc.thermostat;
  let status = "ok";
  let message = "";
  if (t.reachable === false) {
    status = "unreachable";
    message = "Can't reach the thermostat";
  } else if (t.reachable === null) {
    status = "checking";
    message = "Checking the thermostat";
  }
  const lastKnown = t.reachable === false || stale;
  return {
    status,
    message,
    lastKnown,
    lastKnownLabel: lastKnown ? "last known" : "",
    temperature: degrees(t.temperatureF),
    setpoint: degrees(t.setpointF),
    mode: modeLabel(t.mode),
  };
}

const REASONS = { home: "someone came home", away: "everyone left" };

function dayKey(ms, timeZone) {
  return new Intl.DateTimeFormat("en-CA", { timeZone }).format(ms);
}

// "Set to 82 at 2:14 PM, everyone left". An older change names its day.
export function lastChangeText(doc, now, timeZone) {
  const change = doc.lastChange;
  if (!change) {
    return "No automatic change yet";
  }
  const time = new Intl.DateTimeFormat("en-US", {
    timeZone, hour: "numeric", minute: "2-digit",
  }).format(change.atMs);
  let when = `at ${time}`;
  if (dayKey(change.atMs, timeZone) !== dayKey(now, timeZone)) {
    if (dayKey(change.atMs, timeZone) === dayKey(now - DAY, timeZone)) {
      when = `yesterday at ${time}`;
    } else {
      const day = new Intl.DateTimeFormat("en-US", { timeZone, month: "short", day: "numeric" }).format(change.atMs);
      when = `on ${day} at ${time}`;
    }
  }
  const why = REASONS[change.reason];
  return `Set to ${Math.round(change.setpointF)} ${when}${why ? `, ${why}` : ""}`;
}

export function overrideText(doc) {
  return doc.override
    ? "You changed the thermostat by hand. Automatic changes resume when someone leaves or comes home."
    : "";
}

export function parseWhole(text) {
  if (typeof text !== "string" || !/^\s*\d+\s*$/.test(text)) {
    return null;
  }
  return Number.parseInt(text, 10);
}

const RANGE_NAMES = {
  comfort_f: "The comfort temperature",
  away_f: "The away temperature",
  away_minutes: "The away delay",
};

// An empty string when the three numbers are fine, else a plain sentence saying
// what to fix. Run before any write, so a refused edit never reaches the hub.
export function checkSettings(settings) {
  for (const key of ["comfort_f", "away_f", "away_minutes"]) {
    const value = settings[key];
    const { min, max } = SETTING_LIMITS[key];
    if (!Number.isInteger(value) || value < min || value > max) {
      return `${RANGE_NAMES[key]} must be a whole number between ${min} and ${max}.`;
    }
  }
  if (settings.comfort_f >= settings.away_f) {
    return "Comfort must be lower than away. Lower the comfort temperature or raise the away one.";
  }
  return "";
}

// The keys of `next` (enabled and the three numbers only) that differ from what
// the hub holds, as the request body.
export function settingsBody(written, next) {
  const body = {};
  for (const key of SETTING_KEYS) {
    if (key in next && next[key] !== written[key]) {
      body[key] = next[key];
    }
  }
  return body;
}

// One number was edited. Returns the new valid `draft`, the `refused` Map
// (field -> sentence, per field, so a refused field stays flagged until its own
// value is valid), and `accepted`, the values to queue for saving. `raws` holds
// the parsed text of fields already refused, so raising away can release a
// comfort that was refused only for sitting at or above it.
export function editSetting(draft, refused, name, value, raws) {
  const nextRefused = new Map(refused);
  let nextDraft = draft;
  const accepted = {};
  const message = checkSettings({ ...draft, [name]: value });
  if (message) {
    nextRefused.set(name, message);
  } else {
    nextRefused.delete(name);
    nextDraft = { ...draft, [name]: value };
    accepted[name] = value;
  }
  for (const other of [...nextRefused.keys()]) {
    if (other === name || !(other in raws)) {
      continue;
    }
    const retry = { ...nextDraft, [other]: raws[other] };
    if (!checkSettings(retry)) {
      nextRefused.delete(other);
      nextDraft = retry;
      accepted[other] = raws[other];
    }
  }
  return { draft: nextDraft, refused: nextRefused, accepted };
}
