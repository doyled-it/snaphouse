// The state machine behind "Changing to ..." on the Frame screen (phone and
// panel). Pure: no DOM, no clock, no network; every time is passed in.
//
// One change is one command (Next, Back or Show on the frame) the page sent. Its
// seq comes from the command reply and is matched against the republished
// frame.json: `pending.seq` names the picture the frame is switching to, and
// `applied_seq >= seq` says it is on the panel. Confirmation is by seq only, never
// by comparing clocks (the frames' clocks can drift). A frame that does not write
// these fields yet never confirms, so every change ends in the 90 s message.
//
// A change is a plain object: { frameId, action, seq, targetId, startMs, phase,
// endedMs }. phase is sent | changing | waiting | failed | done | timeout.

export const CHANGE_TIMEOUT_MS = 90_000;
export const DONE_MS = 3000;
export const MESSAGE_MS = 8000;
export const FAST_POLL_MS = 2000;
export const PUSH_MS = 35_000;

const WAITING_WHAT = {
  music: "music",
  game: "the game",
  pin: "the pinned screen",
  settling: "music",
};

export function startChange({ frameId, action, seq, targetId, nowMs }) {
  const hasTarget = typeof targetId === "string" && targetId.length > 0;
  return {
    frameId,
    action,
    seq: Number.isInteger(seq) && seq >= 0 ? seq : null,
    targetId: hasTarget ? targetId : null,
    startMs: nowMs,
    phase: hasTarget ? "changing" : "sent",
    endedMs: null,
    reason: null,
    changingMs: hasTarget ? nowMs : null,
  };
}

function isDoc(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

// Move the change on with the latest republished frame.json (or null when it could
// not be read). Never mutates; returns the same object when nothing changed.
export function advance(change, mirror, nowMs) {
  if (!change || !inFlight(change)) {
    return change;
  }
  if (change.seq !== null && isDoc(mirror)) {
    const applied = mirror.applied_seq;
    if (Number.isInteger(applied) && applied >= change.seq) {
      return { ...change, phase: "done", endedMs: nowMs };
    }
    const pending = mirror.pending;
    if (isDoc(pending) && pending.seq === change.seq) {
      const targetId = typeof pending.id === "string" ? pending.id : change.targetId;
      if (pending.state === "waiting" || pending.state === "failed") {
        return {
          ...change,
          phase: pending.state,
          targetId,
          reason: typeof pending.reason === "string" ? pending.reason : null,
          endedMs: nowMs,
        };
      }
      if (pending.state === "changing" && (change.phase !== "changing" || change.targetId !== targetId)) {
        return { ...change, phase: "changing", targetId, changingMs: change.changingMs ?? nowMs };
      }
    }
  }
  if (nowMs - change.startMs >= CHANGE_TIMEOUT_MS) {
    return { ...change, phase: "timeout", endedMs: nowMs };
  }
  return change;
}

// True while the page is still waiting on the frame: the only time it reads
// frame.json every 2 s.
export function inFlight(change) {
  return Boolean(change) && (change.phase === "sent" || change.phase === "changing");
}

// True once the change's last message has been up long enough to go back to the
// normal screen.
export function settled(change, nowMs) {
  if (!change) {
    return true;
  }
  if (inFlight(change)) {
    return false;
  }
  const hold = change.phase === "done" ? DONE_MS : MESSAGE_MS;
  return nowMs - change.endedMs >= hold;
}

// How far along the e-ink push looks, 0 to 0.95, counted from when the frame took
// the command. The push takes about 30 s; this is a gentle guide, never a promise.
export function progressOf(change, nowMs) {
  if (!change || change.phase !== "changing" || change.changingMs === null) {
    return 0;
  }
  return Math.min(0.95, Math.max(0, (nowMs - change.changingMs) / PUSH_MS));
}

export function pollMs(change, idleMs) {
  return inFlight(change) ? FAST_POLL_MS : idleMs;
}

// The image is fetched only when the frame says it drew something new.
export function needsPng(previousUpdatedAt, mirror) {
  if (!isDoc(mirror) || typeof mirror.updated_at !== "string") {
    return false;
  }
  return mirror.updated_at !== previousUpdatedAt;
}

// What to draw. `lookup(id)` returns { title, thumb } from the page's art index, or
// null for an id it does not know. Returns { phase, text, thumb, busy, progress,
// ends }.
export function viewOf(change, { frameLabel, lookup, nowMs = 0 }) {
  const piece = change && change.targetId && lookup ? lookup(change.targetId) : null;
  const title = piece && typeof piece.title === "string" && piece.title ? piece.title : null;
  const thumb = piece && typeof piece.thumb === "string" && piece.thumb ? piece.thumb : null;
  const base = {
    phase: change.phase,
    action: change.action,
    thumb: null,
    busy: false,
    progress: false,
    ends: false,
    fill: progressOf(change, nowMs),
  };
  switch (change.phase) {
    case "sent":
      return { ...base, text: `Sending to ${frameLabel}...`, busy: true };
    case "changing":
      return { ...base, text: `Changing to ${title ?? "the next picture"}`, thumb, busy: true, progress: true };
    case "waiting": {
      const what = WAITING_WHAT[change.reason] ?? "something else";
      return { ...base, text: `Up next: ${title ?? "the next picture"}. The frame shows ${what} right now.`, thumb, ends: true };
    }
    case "failed":
      return { ...base, text: "The frame could not show it. Try again.", ends: true };
    case "done":
      return { ...base, text: `Now showing ${title ?? "the new picture"}`, ends: true };
    default:
      return {
        ...base,
        phase: "timeout",
        text: "The frame did not confirm the change. It may be busy or offline.",
        ends: true,
      };
  }
}
