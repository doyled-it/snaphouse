// The Clock screensaver style: a large thin 12-hour time with the date under it, in
// the panel's local time. It is DOM text, redrawn once at each whole minute and moved
// to a new spot every few minutes so nothing burns in. There is no timer while
// stopped. The rules are in clock-model.js; the look is in panel.css (.saver-clock).

import { clockText, moveDue, msToNextMinute, pickSpot } from "./clock-model.js";

export function createClockStyle({ doc, now, setTimeout, clearTimeout, random }) {
  const node = doc.createElement("div");
  node.classList.add("saver-clock");
  const timeNode = doc.createElement("div");
  timeNode.className = "saver-clock-time";
  const dateNode = doc.createElement("div");
  dateNode.className = "saver-clock-date";
  node.append(timeNode, dateNode);

  let timer = null;
  let spot = null;
  let sinceMove = 0;

  function place() {
    spot = pickSpot(random, spot);
    sinceMove = 0;
    node.style.setProperty("--clock-x", spot.x.toFixed(3));
    node.style.setProperty("--clock-y", spot.y.toFixed(3));
  }

  function draw() {
    const date = new Date(now());
    const text = clockText(date);
    timeNode.textContent = text.time;
    dateNode.textContent = text.date;
    return date;
  }

  function tick() {
    sinceMove += 1;
    if (moveDue(sinceMove)) place();
    schedule(draw());
  }

  function schedule(date) {
    timer = setTimeout(tick, msToNextMinute(date));
  }

  place();

  return {
    node,
    start() {
      if (timer !== null) return;
      schedule(draw());
    },
    stop() {
      if (timer === null) return;
      clearTimeout(timer);
      timer = null;
    },
    setHues() {},
    setNight(on) {
      node.classList.toggle("is-night", Boolean(on));
    },
  };
}
