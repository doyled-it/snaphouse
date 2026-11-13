// The Gallery image viewer, pure: what the viewer shows for a piece in a list,
// how a step or a swipe moves through it, and how a piece rides in the hash.
// rooms.js owns the DOM. The same viewer serves the grid and the frame history.

const GRID_ACTIONS = ["show", "pin", "move", "trash"];

// Why a piece cannot be shown on the frame, or null. Today's orientation rule:
// only a landscape piece rotates, so a portrait one is too tall.
function blockedReason(piece) {
  if (piece && piece.landscape === false) {
    return "Too tall to show on the frame.";
  }
  return null;
}

// source is "grid" or "history". History adds removeFromHistory.
export function viewerState(items, index, source) {
  const list = Array.isArray(items) ? items : [];
  const count = list.length;
  const i = count === 0 ? 0 : Math.max(0, Math.min(count - 1, Number.isInteger(index) ? index : 0));
  const actions = source === "history" ? [...GRID_ACTIONS, "removeFromHistory"] : GRID_ACTIONS.slice();
  return {
    position: count === 0 ? "0 of 0" : `${i + 1} of ${count}`,
    canPrev: count > 0 && i > 0,
    canNext: count > 0 && i < count - 1,
    actions,
    showBlockedReason: count === 0 ? null : blockedReason(list[i]),
  };
}

// One step through the list, clamped at both ends (no wrap).
export function stepIndex(index, count, dir) {
  if (!(count > 0)) {
    return 0;
  }
  return Math.max(0, Math.min(count - 1, index + (dir < 0 ? -1 : 1)));
}

// 1 for a swipe left (next), -1 for a swipe right (previous), 0 when the move
// is too short or mostly vertical, so a page scroll is never taken over.
export function swipeDirection(dx, dy, thresholdPx = 48) {
  if (Math.abs(dx) < thresholdPx || Math.abs(dx) <= Math.abs(dy)) {
    return 0;
  }
  return dx > 0 ? 1 : -1;
}

// The hash that opens a piece in the viewer. A piece id can hold a folder
// slash, so it is encoded as one segment.
export function galleryHash(pieceId) {
  return `#gallery/${encodeURIComponent(pieceId)}`;
}

// The piece a hash names (#gallery/<id>, or the old #art/<id>), or null.
export function pieceIdFromHash(hash) {
  if (typeof hash !== "string") {
    return null;
  }
  const match = /^#(?:gallery|art)\/(.+)$/i.exec(hash);
  if (!match) {
    return null;
  }
  try {
    return decodeURIComponent(match[1]);
  } catch (err) {
    return null;
  }
}
