// Page navigation: which tabs exist, which are visible, and what a URL hash
// resolves to. Pure; rooms.js owns the DOM.
import { screenFromHash } from "./rooms-model.js";

export const TAB_LABELS = {
  listen: "Listen",
  frame: "Frame",
  gallery: "Gallery",
  climate: "Climate",
  settings: "Settings",
};

const TAB_ORDER = ["listen", "frame", "gallery", "climate", "settings"];

// visible: { frame: boolean, climate: boolean }. Listen, Gallery and Settings
// are always present.
function isVisible(screen, visible) {
  if (screen === "frame") {
    return Boolean(visible && visible.frame);
  }
  if (screen === "climate") {
    return Boolean(visible && visible.climate);
  }
  return true;
}

export function visibleTabs(visible) {
  return TAB_ORDER.filter((screen) => isVisible(screen, visible));
}

// The screen a hash selects, plus the canonical hash for it. "#art" aliases to
// "#gallery"; a hidden or unknown screen falls back to Listen. "#frame/<id>" is
// kept as written while the Frame tab is visible.
export function resolveScreen(hash, visible) {
  const screen = screenFromHash(hash);
  if (!isVisible(screen, visible)) {
    return { screen: "listen", hash: "#listen" };
  }
  if (screen === "frame") {
    const text = typeof hash === "string" ? hash : "";
    return { screen, hash: text.startsWith("#") ? text : `#${text}` };
  }
  if (screen === "gallery") {
    // "#gallery/<id>" opens the viewer and is kept; "#art/<id>" is its old name.
    const rest = (typeof hash === "string" ? hash : "").replace(/^#/, "").split("/").slice(1).join("/");
    return { screen, hash: rest ? `#gallery/${rest}` : "#gallery" };
  }
  return { screen, hash: `#${screen}` };
}

// Whether the Frame tab shows, from the signals the Frame screen already uses:
// the frame-control document (it lists frames on the current contract) and the
// frame mirrors (frame.json). Until the first probe of the mirrors finishes the
// answer is yes, so a deep link to #frame is not rewritten while data loads.
export function frameTabVisible(control, mirrors, probed) {
  if (!probed) {
    return true;
  }
  if (control && control.available && !control.legacy) {
    return true;
  }
  return Object.values(mirrors || {}).some((mirror) => mirror);
}

// Whether the Climate tab shows: the hub's homebody.json answered with a
// document the page can read. Until the first read finishes the answer is yes,
// so a deep link to #climate is not rewritten while it loads; a missing or
// malformed file hides the tab.
export function climateTabVisible(doc, probed) {
  if (!probed) {
    return true;
  }
  return Boolean(doc);
}
