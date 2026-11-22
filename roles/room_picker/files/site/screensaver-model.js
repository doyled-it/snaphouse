// Pure rules for the panel screensaver: which URL parameters turn it on, whether
// the clock is inside quiet hours, and whether the overlay should show. The clock
// is passed in as a Date (the browser's local time, the same clock the backlight
// daemon reads). No DOM.

const DEFAULTS = { idle: 300, nidle: 60, qs: "22:00", qe: "06:00" };

function seconds(value, fallback) {
  const n = Number.parseFloat(value);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

function hhmm(value, fallback) {
  const m = /^(\d{1,2}):(\d{2})$/.exec(value || "");
  if (!m) return fallback;
  const h = Number(m[1]);
  const min = Number(m[2]);
  return h < 24 && min < 60 ? `${String(h).padStart(2, "0")}:${m[2]}` : fallback;
}

export function parsePanelParams(search) {
  const q = new URLSearchParams(search || "");
  if (q.get("panel") !== "1") return null;
  return {
    dayIdleMs: seconds(q.get("idle"), DEFAULTS.idle) * 1000,
    nightIdleMs: seconds(q.get("nidle"), DEFAULTS.nidle) * 1000,
    quietStart: hhmm(q.get("qs"), DEFAULTS.qs),
    quietEnd: hhmm(q.get("qe"), DEFAULTS.qe),
  };
}

const minutes = (s) => Number(s.slice(0, 2)) * 60 + Number(s.slice(3));

export function inQuietHours(date, params) {
  const now = date.getHours() * 60 + date.getMinutes();
  const start = minutes(params.quietStart);
  const end = minutes(params.quietEnd);
  if (start === end) return false;
  return start < end ? now >= start && now < end : now >= start || now < end;
}

export function idleLimitMs(date, params) {
  return inQuietHours(date, params) ? params.nightIdleMs : params.dayIdleMs;
}

export function shouldShow({ idleMs, date, params, inputHeld }) {
  return !inputHeld && idleMs >= idleLimitMs(date, params);
}
