// The wall panel's layout rules, pure (no DOM, no network). panel.js draws.

import { howToText, playbackControls, roomState } from "./listen-model.js";
import { speakerBanner } from "./health-model.js";
import { EMPTY_PEOPLE_TEXT, SETTING_LIMITS, peopleLines, thermostatView } from "./climate-model.js";

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

// ---- Music ---------------------------------------------------------------- //

const STEP = 5;

// A tapped fill height snapped to steps of 5.
export function snapVolume(percent) {
  return Math.max(0, Math.min(100, Math.round(percent / STEP) * STEP));
}

// Plus and minus: the next multiple of 5 in that direction, clamped.
export function stepVolume(current, dir) {
  const next = dir > 0 ? (Math.floor(current / STEP) + 1) * STEP : (Math.ceil(current / STEP) - 1) * STEP;
  return Math.max(0, Math.min(100, next));
}

export function progressFraction(progress) {
  if (!progress || !(progress.total > 0)) return 0;
  return Math.max(0, Math.min(1, progress.elapsed / progress.total));
}

// What the now-playing bar shows. The speaker banner takes the bar's place at
// the same height while the audio server cannot be read.
export function nowPlayingBar(track, { btName = null, speakersDown = false } = {}) {
  const banner = speakerBanner(speakersDown ? "down" : "up");
  if (banner) {
    return { mode: "banner", title: banner, artist: "", howTo: "", showTransport: false, playing: false };
  }
  if (!track) {
    return {
      mode: "idle",
      title: "Nothing playing",
      artist: "",
      howTo: howToText(btName),
      showTransport: false,
      playing: false,
    };
  }
  const controls = playbackControls(track);
  return {
    mode: "track",
    title: track.title,
    artist: track.artist || "",
    howTo: "",
    showTransport: controls.visible,
    playing: track.status !== "paused",
  };
}

// One room's column. `room` is { id, label, on, reachable, volume }.
export function roomColumn(room, rooms, { speakersDown = false } = {}) {
  const st = roomState(room, rooms);
  const unreachable = !room.reachable;
  return {
    id: room.id,
    name: room.label,
    valueText: String(Math.round(room.volume)),
    fillPct: Math.max(0, Math.min(100, Math.round(room.volume))),
    greyed: unreachable || !room.on,
    disabled: st.disabled || speakersDown,
    power: Boolean(room.on) && !unreachable,
    status: unreachable ? st.label : "",
  };
}

const MIN_FILL_PX = 60;
const COLUMN_GAP = 10;
const COLUMN_CHROME = 72 + 10 + 16; // plus/minus width, gap, side padding
// 800 minus the 96 px rail and 14 px padding either side.
export const CONTENT_WIDTH = 676;

// Columns stay equal; flag (never shrink text) when one would leave under 60 px
// of fill.
export function columnFit(count, width = CONTENT_WIDTH) {
  const n = Math.max(1, count);
  const column = (width - COLUMN_GAP * (n - 1)) / n;
  const fillPx = Math.floor(column - COLUMN_CHROME);
  return { ok: fillPx >= MIN_FILL_PX, fillPx };
}

// ---- Climate -------------------------------------------------------------- //

// Who is home, on one line.
export function whoLine(people) {
  if (people.length === 0) return EMPTY_PEOPLE_TEXT;
  const home = people.filter((p) => p.state === "home").map((p) => p.name);
  if (home.length > 0) {
    const list = home.length === 2 ? `${home[0]} and ${home[1]}` : home.join(", ");
    return `Home: ${list}`;
  }
  return people.every((p) => p.state === "unknown") ? "Not sure yet" : "Everyone is away";
}

// The two cards. `doc` is parseClimate's result; `comfort` is the comfort
// setpoint to show (the draft, so a tap shows at once).
export function climateCards(doc, { now, comfort }) {
  const view = thermostatView(doc);
  const working = view.status === "ok" && (doc.thermostat.mode === "cool" || doc.thermostat.mode === "heat");
  let modeText = view.message;
  if (view.status === "ok") {
    modeText = working ? `${view.mode} to ${view.setpoint}` : `Holding ${view.setpoint}`;
  }
  const away = doc.settings.away_f;
  return {
    inside: {
      temp: view.temperature,
      modeText,
      modeAccent: working,
      who: whoLine(peopleLines(doc, now)),
    },
    comfort: {
      value: `${comfort}°`,
      minusDisabled: comfortStep(comfort, -1, away) === null,
      plusDisabled: comfortStep(comfort, 1, away) === null,
    },
  };
}

// One degree up or down, or null when it would leave the allowed range or reach
// the away temperature (comfort must stay below away).
export function comfortStep(current, dir, away) {
  const next = current + dir;
  const { min, max } = SETTING_LIMITS.comfort_f;
  if (next < min || next > max || next >= away) return null;
  return next;
}

// Re-exported for the panel tests; defined in rooms-model.js so phones never load
// this module.
export { pairInstructionPanel } from "./rooms-model.js";
