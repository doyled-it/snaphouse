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

export function peopleLines(doc, now) {
  return doc.people.map((person) => ({
    name: person.name,
    state: person.state,
    label: STATE_LABELS[person.state],
    seen: relativeSeen(person.lastSeenMs, now),
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

export function thermostatView(doc) {
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
  return {
    status,
    message,
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
