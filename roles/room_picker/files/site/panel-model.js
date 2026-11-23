// The wall panel's layout rules, pure (no DOM, no network). panel.js draws.

// Panel mode is the kiosk URL's panel=1, the same flag the screensaver reads.
export function isPanel(search) {
  return new URLSearchParams(search || "").get("panel") === "1";
}

export const BACK_HASH = "#more";

const SUB_SCREENS = ["frame", "gallery", "settings", "health"];
const TOP_SCREENS = ["listen", "climate", "garage", "more"];

function present(screen, visible) {
  if (screen === "frame") return Boolean(visible && visible.frame);
  if (screen === "climate") return Boolean(visible && visible.climate);
  if (screen === "garage") return Boolean(visible && visible.garage);
  return true;
}

// The screen a hash selects on the panel, plus its canonical hash. Music is
// #listen; More's sub-screens keep their phone hashes; #art aliases #gallery;
// a hidden or unknown hash (the play-history viewer included) is Music.
export function resolvePanelScreen(hash, visible) {
  const text = typeof hash === "string" ? hash.replace(/^#/, "") : "";
  const [head, ...rest] = text.split("/");
  const raw = head.toLowerCase();
  const screen = raw === "art" ? "gallery" : raw;
  if (![...TOP_SCREENS, ...SUB_SCREENS].includes(screen) || !present(screen, visible)) {
    return { screen: "listen", hash: "#listen" };
  }
  if ((screen === "frame" || screen === "gallery") && rest.length > 0) {
    return { screen, hash: `#${screen}/${rest.join("/")}` };
  }
  return { screen, hash: `#${screen}` };
}

const RAIL_LABELS = { listen: "Music", climate: "Climate", garage: "Garage", more: "More" };

export function railItems(visible) {
  return TOP_SCREENS.filter((s) => present(s, visible)).map((screen) => ({ screen, label: RAIL_LABELS[screen] }));
}

export function railActive(screen) {
  return TOP_SCREENS.includes(screen) && screen !== "more" ? screen : "more";
}

export function isSubScreen(screen) {
  return SUB_SCREENS.includes(screen);
}
