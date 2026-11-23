// The panel's Garage rules, pure. The big button's fill shows how closed the
// door is (full closed, empty open); the button needs two taps (arm, then send).

// The door's usual travel time, used for the fill when the opener gives no position.
export const TRAVEL_MS = 15000;
// How long the first tap keeps the button armed.
export const ARM_MS = 4000;

const clamp01 = (n) => Math.max(0, Math.min(1, n));

// The glide. The opener reports the door's position about once a second, but
// the page notices each report on its own poll and render ticks, so the gap
// between two noticed reports jitters. The speed is therefore the average over
// the whole movement so far (first moving report to the latest), falling back
// to this door's measured travel times; between reports the fill moves on at
// that speed and eases off as it nears GLIDE_AHEAD past the last report, never
// stopping dead and never moving backwards.
export const GLIDE_AHEAD = 0.25;
// Measured on the house door 2026-11-23: about 10.7 s up, 13.5 s down (after the
// opener's pre-close warning, during which it reports no movement).
export const OPEN_TRAVEL_MS = 11000;
export const CLOSE_TRAVEL_MS = 14000;
const FASTEST_MS = 5000; // a full travel faster than this is not believed
const SLOWEST_MS = 40000; // nor slower than this
const MIN_SPAN_MS = 1500; // less movement time than this is too little to average

// Speed in "open" fraction per ms. `first` is the first report after the door
// started moving, `latest` the newest, each `{ open, ms }`; `dir` is 1 opening or
// -1 closing (it picks the fallback).
export function glideRate(first, latest, dir) {
  const fallback = 1 / (dir > 0 ? OPEN_TRAVEL_MS : CLOSE_TRAVEL_MS);
  if (!first || !latest) return fallback;
  const dt = latest.ms - first.ms;
  const dOpen = Math.abs(latest.open - first.open);
  if (!(dt >= MIN_SPAN_MS) || !(dOpen > 0)) return fallback;
  return Math.min(1 / FASTEST_MS, Math.max(1 / SLOWEST_MS, dOpen / dt));
}

// Where to draw the door (open fraction) at `nowMs`: the last report moved on by
// the rate in direction `dir`, easing toward GLIDE_AHEAD past the report
// (ahead = A * (1 - e^(-rate * dt / A))), clamped to 0..1, never behind `shown`.
export function glideOpen(report, dir, rate, nowMs, shown) {
  const dt = Math.max(0, nowMs - report.ms);
  const ahead = GLIDE_AHEAD * (1 - Math.exp(-(rate * dt) / GLIDE_AHEAD));
  const est = clamp01(report.open + dir * ahead);
  if (typeof shown !== "number") return est;
  return dir > 0 ? Math.max(est, shown) : Math.min(est, shown);
}

// `doc` is parseGarageText's result, `view` is garageView(doc, now). `startMs` is
// when the page first saw the door moving (only used with no position).
// fill: 0 empty (open) to 1 full (closed), or null for no fill (link down or
// state unknown: the button never shows closed unless the link is live).
export function garageFill(doc, view, nowMs, startMs) {
  if (view.word === "UNKNOWN") return { fill: null, text: null };
  const moving = doc.state === "opening" || doc.state === "closing";
  const hasPosition = typeof doc.position === "number";
  if (hasPosition) {
    const open = clamp01(doc.position / 100);
    let text = null;
    if (doc.state === "opening") text = `Opening, ${Math.round(open * 100)}%`;
    if (doc.state === "closing") text = `Closing, ${Math.round((1 - open) * 100)}%`;
    return { fill: 1 - open, text };
  }
  if (moving) {
    const t = clamp01((nowMs - startMs) / TRAVEL_MS);
    return doc.state === "opening"
      ? { fill: 1 - t, text: "Opening" }
      : { fill: t, text: "Closing" };
  }
  if (doc.state === "closed") return { fill: 1, text: null };
  if (doc.state === "open") return { fill: 0, text: null };
  return { fill: 0.5, text: null }; // stopped, position unknown
}

// Which way the one button moves the door, and whether it can right now.
export function garageButton(doc, view) {
  if (view.word === "UNKNOWN" || doc.state === "opening" || doc.state === "closing") {
    return { action: null, disabled: true };
  }
  const action = doc.state === "open" ? "close" : "open";
  return { action, disabled: Boolean(view.disabled[action]) };
}

const IDLE = { armed: false, action: null, label: null, expiresMs: null, send: false };

// The two-tap button. `prev` is the last result (or null). Events:
// { type: "tap", action, disabled? } and { type: "tick" }. A second tap for the
// same action inside the window returns send: true (and sendAction); the page
// then runs the existing intent and command flow. Anything else re-arms or
// disarms; nothing is ever sent without the armed tap before it.
export function armState(prev, event, nowMs) {
  const armed = prev && prev.armed && nowMs < prev.expiresMs ? prev : null;
  if (event.type === "tick") return armed ? prev : IDLE;
  if (event.disabled || !event.action) return IDLE;
  if (armed && armed.action === event.action) {
    return { ...IDLE, send: true, sendAction: event.action };
  }
  return {
    armed: true,
    action: event.action,
    label: event.action === "close" ? "Tap to close" : "Tap to open",
    expiresMs: nowMs + ARM_MS,
    send: false,
  };
}
