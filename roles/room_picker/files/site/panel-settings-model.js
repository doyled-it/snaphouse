// Pure rules for the panel's Screen and Screensaver settings: parsing the hub's
// panel-settings.json, the same validation the hub applies to a write, the step and
// wrap rules for the controls, and the More tile lines. No DOM, no network.
// The hub half is lib/snaphouse/panelsettings.py.

export const SETTINGS_DEFAULTS = {
  brightness_pct: 100,
  night_dim_pct: 15,
  night_start: "22:00",
  night_end: "06:00",
  screensaver_on: true,
  screensaver_wait_s: 60,
  screensaver_style: "aurora",
  screensaver_pct: 40,
};

export const WAIT_CHOICES = [30, 60, 120, 300, 600];
export const STYLES = ["aurora"];

// The lowest value each brightness may take: Brightness stops at 20 so the panel is
// always usable when touched, the screensaver at 5, the night dim at 0 (off).
export const FLOORS = { brightness_pct: 20, night_dim_pct: 0, screensaver_pct: 5 };

const isInt = (v) => Number.isInteger(v) && typeof v === "number";
const pctOk = (low) => (v) => isInt(v) && v >= low && v <= 100 && v % 5 === 0;
const timeOk = (v) => {
  const m = /^(\d{2}):(\d{2})$/.exec(typeof v === "string" ? v : "");
  return Boolean(m) && Number(m[1]) < 24 && (m[2] === "00" || m[2] === "30");
};

const CHECKS = {
  brightness_pct: pctOk(FLOORS.brightness_pct),
  night_dim_pct: pctOk(FLOORS.night_dim_pct),
  night_start: timeOk,
  night_end: timeOk,
  screensaver_on: (v) => typeof v === "boolean",
  screensaver_wait_s: (v) => isInt(v) && WAIT_CHOICES.includes(v),
  screensaver_style: (v) => typeof v === "string" && STYLES.includes(v),
  screensaver_pct: pctOk(FLOORS.screensaver_pct),
};

const MESSAGES = {
  brightness_pct: "Brightness must be 20 to 100.",
  night_dim_pct: "Night dim must be 0 to 100.",
  night_start: "Pick a time on the hour or half hour.",
  night_end: "Pick a time on the hour or half hour.",
  screensaver_on: "Screensaver must be on or off.",
  screensaver_wait_s: "Pick one of the wait times.",
  screensaver_style: "That style is not available yet.",
  screensaver_pct: "Brightness must be 5 to 100.",
};

// This panel's entry from the hub's document, each key checked alone (a bad or
// missing one keeps the fallback), or null for a document the page cannot use.
export function parsePanelSettings(doc, pid, fallback = SETTINGS_DEFAULTS) {
  if (!doc || typeof doc !== "object" || Array.isArray(doc) || doc.version !== 1) return null;
  const panels = doc.panels;
  if (!panels || typeof panels !== "object" || Array.isArray(panels)) return null;
  if (typeof pid !== "string" || pid === "") return null;
  const entry = panels[pid];
  if (!entry || typeof entry !== "object" || Array.isArray(entry)) return null;
  const out = { ...fallback };
  for (const key of Object.keys(CHECKS)) {
    if (CHECKS[key](entry[key])) out[key] = entry[key];
  }
  if (out.night_start === out.night_end) {
    out.night_start = fallback.night_start;
    out.night_end = fallback.night_end;
  }
  return out;
}

function nearestWait(seconds) {
  let best = WAIT_CHOICES[0];
  for (const w of WAIT_CHOICES) {
    if (Math.abs(w - seconds) < Math.abs(best - seconds)) best = w;
  }
  return best;
}

// The settings the kiosk URL implies (the fallback until the hub answers), from
// screensaver-model.js's parsePanelParams result.
export function settingsFromParams(params) {
  if (!params) return { ...SETTINGS_DEFAULTS };
  const seconds = Number(params.dayIdleMs) / 1000;
  return {
    ...SETTINGS_DEFAULTS,
    screensaver_on: params.enabled !== false,
    screensaver_wait_s: Number.isFinite(seconds) && seconds > 0 ? nearestWait(seconds) : SETTINGS_DEFAULTS.screensaver_wait_s,
    night_start: timeOk(params.quietStart) ? params.quietStart : SETTINGS_DEFAULTS.night_start,
    night_end: timeOk(params.quietEnd) ? params.quietEnd : SETTINGS_DEFAULTS.night_end,
  };
}

// Validate a patch against the current settings, as the hub will.
export function checkPatch(current, patch) {
  const keys = patch && typeof patch === "object" ? Object.keys(patch) : [];
  if (keys.length === 0) return { ok: false, patch: null, error: "Nothing to change." };
  for (const key of keys) {
    if (!CHECKS[key]) return { ok: false, patch: null, error: "Unknown setting." };
    if (!CHECKS[key](patch[key])) return { ok: false, patch: null, error: MESSAGES[key] };
  }
  const merged = { ...current, ...patch };
  if (merged.night_start === merged.night_end) {
    return { ok: false, patch: null, error: "Night cannot start and end at the same time." };
  }
  return { ok: true, patch: { ...patch }, error: null };
}

export function stepPct(value, dir, min) {
  return Math.max(min, Math.min(100, value + (dir < 0 ? -5 : 5)));
}

// A tap at `fraction` (0 to 1) across a bar, snapped to steps of 5 and the floor.
export function pctFromFraction(fraction, min) {
  const f = Number.isFinite(fraction) ? Math.max(0, Math.min(1, fraction)) : 0;
  return Math.max(min, Math.round((f * 100) / 5) * 5);
}

const toMinutes = (hhmm) => Number(hhmm.slice(0, 2)) * 60 + Number(hhmm.slice(3));
const fromMinutes = (m) => {
  const wrapped = ((m % 1440) + 1440) % 1440;
  return `${String(Math.floor(wrapped / 60)).padStart(2, "0")}:${String(wrapped % 60).padStart(2, "0")}`;
};

// One 30 minute step, wrapping midnight and skipping the other night time.
export function stepTime(value, dir, other) {
  const delta = dir < 0 ? -30 : 30;
  let next = fromMinutes(toMinutes(value) + delta);
  if (next === other) next = fromMinutes(toMinutes(next) + delta);
  return next;
}

export function timeLabel(hhmm) {
  if (!timeOk(hhmm)) return "";
  const h = Number(hhmm.slice(0, 2));
  return `${h % 12 || 12}:${hhmm.slice(3)} ${h < 12 ? "AM" : "PM"}`;
}

export function waitLabel(seconds) {
  return seconds < 60 ? `${seconds} s` : `${seconds / 60} min`;
}

// The keys of `next` whose value differs from `prev`.
export function diffPatch(prev, next) {
  const out = {};
  for (const key of Object.keys(next || {})) {
    if (!prev || JSON.stringify(prev[key]) !== JSON.stringify(next[key])) out[key] = next[key];
  }
  return out;
}

const STYLE_NAMES = { aurora: "Aurora" };

// The More tiles' second lines.
export function tileLines(settings) {
  const style = STYLE_NAMES[settings.screensaver_style] || "Aurora";
  return {
    screen: "Brightness, night",
    saver: settings.screensaver_on ? `${style} after ${waitLabel(settings.screensaver_wait_s)}` : "Off",
  };
}

// A brightness bar's view: fill percent, label, and which steppers are spent.
export function barView(value, min) {
  return { fill: value, label: `${value}%`, low: value < 30, minusDisabled: value <= min, plusDisabled: value >= 100 };
}

// With the screensaver off its other rows read as inactive and Preview is off.
export function saverView(settings) {
  const off = settings.screensaver_on === false;
  return { rowsDimmed: off, previewDisabled: off };
}
