// The Home status strip, pure (no DOM): which chips show, their text and how much
// they ask for attention. Built from the documents the page already parses
// (climate-model.js, garage-model.js); nothing new is read from the hub.
//
// state is "calm", "warn" (yellow) or "alert" (red). Only the garage door being
// open or moving, every person reading "Not sure yet", an obstruction or lockout,
// or a down opener link color a chip. Nothing else does.

import { peopleLines, staleness, thermostatView } from "./climate-model.js";
import { garageView } from "./garage-model.js";

function peopleChip(doc, now) {
  const lines = peopleLines(doc, now);
  if (lines.length === 0) {
    return null;
  }
  const home = lines.filter((line) => line.state === "home");
  if (home.length > 0) {
    return { text: home.map((line) => line.name).join(", "), state: "calm" };
  }
  if (lines.every((line) => line.state === "unknown")) {
    return { text: "Not sure who is home", state: "warn" };
  }
  if (lines.every((line) => line.state === "away")) {
    return { text: "Away", state: "calm" };
  }
  return { text: "Nobody home", state: "calm" };
}

function thermostatChip(doc, now) {
  const view = thermostatView(doc, staleness(doc, now).stale);
  if (view.status !== "ok") {
    return { text: view.message, state: "calm" };
  }
  let text = view.temperature;
  const mode = view.mode.toLowerCase();
  if (mode === "cooling" || mode === "heating") {
    text += view.setpoint === "--" ? `, ${mode}` : `, ${mode} to ${view.setpoint}`;
  } else if (mode) {
    text += `, ${mode}`;
  }
  if (doc.override) {
    text += ", held";
  }
  return { text, state: "calm" };
}

function garageChip(doc, now) {
  if (!doc.connected) {
    return { text: "Can't reach the garage door", state: "alert" };
  }
  const view = garageView(doc, now);
  const word = view.word.charAt(0) + view.word.slice(1).toLowerCase();
  let text;
  if (view.word === "UNKNOWN") {
    text = "Garage door state unknown";
  } else if (view.word === "OPENING" || view.word === "CLOSING") {
    text = word;
  } else {
    text = view.duration ? `${word} ${view.duration}` : word;
  }
  let state = "calm";
  if (view.word === "OPEN" || view.word === "OPENING" || view.word === "CLOSING") {
    state = "warn";
  }
  if (doc.obstruction === true) {
    text += ", something in the way";
    state = "alert";
  }
  if (doc.lockout === true) {
    text += ", remote lockout on";
    state = "alert";
  }
  return { text, state };
}

// `climate` and `garage` are the parsed documents (or null). A chip is absent when
// its module is unavailable (the signals that hide its tab) or its document is.
export function statusChips({ climate, garage, climateVisible, garageVisible, now }) {
  const chips = [];
  if (climate && climateVisible) {
    const people = peopleChip(climate, now);
    if (people) {
      chips.push({ id: "people", target: "#climate", ...people });
    }
    chips.push({ id: "thermostat", target: "#climate", ...thermostatChip(climate, now) });
  }
  if (garage && garageVisible) {
    chips.push({ id: "garage", target: "#garage", ...garageChip(garage, now) });
  }
  return chips;
}
