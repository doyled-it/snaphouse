// The panel's More screen and interim Gallery rules, pure.
import { viewerState } from "./gallery-viewer.js";
import { pageCount, pageSlice } from "./rooms-model.js";

export const PER_PAGE = 3;

// Three pictures a page. `page` is zero-indexed and clamped.
export function galleryPage(pieces, page, perPage = PER_PAGE) {
  const total = Array.isArray(pieces) ? pieces.length : 0;
  if (total === 0) {
    return { items: [], label: "No pictures yet", hasPrev: false, hasNext: false, page: 0 };
  }
  const last = pageCount(total, perPage) - 1;
  const at = Math.max(0, Math.min(last, Number.isFinite(page) ? Math.floor(page) : 0));
  const items = pageSlice(pieces, at, perPage);
  const from = at * perPage + 1;
  const to = at * perPage + items.length;
  return {
    items,
    label: from === to ? `${from} of ${total}` : `${from} to ${to} of ${total}`,
    hasPrev: at > 0,
    hasNext: at < last,
    page: at,
  };
}

// The show buttons for one piece: one per frame when there are several, one
// "Show on the frame" for one. A tall art piece the frame cannot show gets the
// phone viewer's sentence and no button.
export function showButtons(piece, frames) {
  const note = viewerState([piece], 0, "grid").showBlockedReason;
  if (note) return { buttons: [], note };
  const buttons = frames.map((f) => ({
    frameId: f.id,
    label: frames.length > 1 ? `Show on ${f.label}` : "Show on the frame",
  }));
  return { buttons, note: null };
}

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

// The tiles on More (four, plus Screen and Screensaver on a panel that can save). `frames` is the frame count, or null to leave the
// Frames tile out (no frames, so its hash would fall back to Music).
export function moreTiles({ frames, pictureCount, troubled, settings = null }) {
  const tiles = [];
  if (frames !== null && frames !== undefined) {
    tiles.push({ id: "frames", hash: "#frame", title: "Frames", line: plural(frames, "frame", "frames"), warn: false });
  }
  tiles.push({ id: "gallery", hash: "#gallery", title: "Gallery", line: plural(pictureCount, "picture", "pictures"), warn: false });
  tiles.push({ id: "bluetooth", hash: "#settings", title: "Bluetooth", line: "Pair a phone, devices", warn: false });
  tiles.push({
    id: "health",
    hash: "#health",
    title: "House health",
    line: troubled === 0 ? "All good" : `${troubled} ${troubled === 1 ? "needs" : "need"} attention`,
    warn: troubled > 0,
  });
  // Screen and Screensaver, with their tile lines, only when this panel can save
  // settings (settings is null otherwise, and their hashes would fall back to Music).
  if (settings) {
    tiles.push({ id: "screen", hash: "#screen", title: "Screen", line: settings.screenLine, warn: false });
    tiles.push({ id: "screensaver", hash: "#screensaver", title: "Screensaver", line: settings.saverLine, warn: false });
  }
  return tiles;
}
