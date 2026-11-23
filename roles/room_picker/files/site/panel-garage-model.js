// The panel's Garage rules, pure. The big button's fill shows how closed the
// door is (full closed, empty open); the button needs two taps (arm, then send).

// The door's usual travel time, used for the fill when the opener gives no position.
export const TRAVEL_MS = 15000;
// How long the first tap keeps the button armed.
export const ARM_MS = 4000;

const clamp01 = (n) => Math.max(0, Math.min(1, n));

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
