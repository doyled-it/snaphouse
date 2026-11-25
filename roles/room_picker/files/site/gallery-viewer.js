// The Gallery image viewer, pure: what the viewer shows for a piece in a list,
// how a step or a swipe moves through it, and how a piece rides in the hash.
// rooms.js owns the DOM. The same viewer serves the grid and the frame history.

import { parseBox } from "./crop-model.js";

const GRID_ACTIONS = ["show", "pin", "move", "trash"];

// Why a piece cannot be shown on the frame, or null. Today's orientation rule:
// only a landscape art piece rotates, so a portrait one is too tall. An Ente
// piece rotates at any shape, and so does one with a saved crop (the frame cuts
// the box whatever the shape).
function blockedReason(piece) {
  if (piece && piece.landscape === false && piece.source !== "ente" && !parseBox(piece.crop)) {
    return "Too tall to show on the frame.";
  }
  return null;
}

// A frame-history entry as a viewer item. `known` is the library piece with the
// same id (or null): it supplies the folder, the reachable url and the source, so
// an Ente piece opened from history is as read-only as one opened from the grid.
export function historyViewerItem({ piece, id, origId, known, fallbackUrl }) {
  return {
    id,
    file: piece.file,
    title: piece.title,
    artist: piece.artist,
    source_url: piece.source_url,
    origId,
    folder: known ? known.folder : "",
    url: known ? known.url : fallbackUrl,
    crop: known ? known.crop ?? null : null,
    autoCrop: known ? known.autoCrop ?? null : null,
    ...(known && known.landscape !== undefined ? { landscape: known.landscape } : {}),
    ...(known && known.source === "ente" ? { source: "ente" } : {}),
  };
}

// Which per-piece write controls the viewer offers. An Ente piece is read-only.
export function viewerControls(piece) {
  const writable = !(piece && piece.source === "ente");
  return { showMove: writable, showTrash: writable };
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

// Which list the viewer steps through: the grid's filtered pieces, or the whole
// library when the shown piece is not in them (a deep link outside the filter).
export function viewerSource(filtered, shownId) {
  return filtered.some((p) => p.id === shownId) ? "filter" : "all";
}

// The piece to land on after the one at `idx` left `list` (the list as it is
// now, built from the same source the index was taken from), or null if empty.
export function idAfterRemoval(list, idx) {
  if (!Array.isArray(list) || list.length === 0) {
    return null;
  }
  return list[Math.max(0, Math.min(list.length - 1, idx))].id;
}

// The two frame buttons for the chosen frame. "Show now" leaves the rotation
// running; "Pin" holds the picture until it is unpinned. `frameName` is the
// frame's display name, or empty when no frame is known.
export function frameButtons(frameName) {
  const name = frameName || "the frame";
  return {
    show: { label: `Show now on ${name}`, hint: "Rotation carries on" },
    pin: { label: `Pin to ${name}`, hint: "Stays until you unpin it" },
  };
}

// The frame picker beside them: shown with two or more frames, listing the
// frames from the frame-control document (rows from frameList) and marking the
// shared selection (the Frame tab's). An unknown selection reads as the first.
export function framePickerView(rows, selectedId) {
  const list = Array.isArray(rows) ? rows : [];
  const known = list.some((r) => r.id === selectedId);
  const chosen = known ? selectedId : list.length > 0 ? list[0].id : null;
  const options = list.map((r) => ({ id: r.id, label: r.label, offline: Boolean(r.offline), selected: r.id === chosen }));
  const current = options.find((o) => o.selected);
  return { visible: list.length >= 2, options, selectedId: chosen, selectedLabel: current ? current.label : "" };
}
