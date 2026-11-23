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

const GARAGE_WORDS = { CLOSED: "closed", OPEN: "open", OPENING: "opening", CLOSING: "closing", STOPPED: "stopped", UNKNOWN: "unknown" };

function whole(value) {
  return typeof value === "number" && Number.isFinite(value) ? String(Math.round(value)) : null;
}

// The status strip's parts. `thermostat` is the climate document's parsed
// thermostat ({ reachable, mode, setpointF, temperatureF }) or null when the
// feature is off; `garage` is garageView's result or null. Each part is null
// when it should not show. Mode wording: cool and heat name the target, anything
// else (off, auto, other) is "holding N".
export function stripParts({ thermostat = null, garage = null, now, locale } = {}) {
  let temp = null;
  let mode = null;
  if (thermostat) {
    if (thermostat.reachable === false) {
      mode = "Thermostat offline";
    } else {
      temp = whole(thermostat.temperatureF);
      const target = whole(thermostat.setpointF);
      if (temp !== null && target !== null) {
        if (thermostat.mode === "cool") mode = `cooling to ${target}`;
        else if (thermostat.mode === "heat") mode = `heating to ${target}`;
        else mode = `holding ${target}`;
      }
    }
  }
  const word = garage ? GARAGE_WORDS[garage.word] || "unknown" : null;
  return {
    temp,
    mode,
    garage: word ? `Garage ${word}` : null,
    time: (now || new Date()).toLocaleTimeString(locale, { hour: "numeric", minute: "2-digit" }),
  };
}

// More's sub-screens put a back button where the status lives and hide the time.
export function stripBack(screen) {
  return isSubScreen(screen);
}
