// Pure rules for the Clock screensaver style: the 12-hour text, the date line, when
// to redraw and move, and where to move to. The time is the panel's local time (the
// Date passed in). No DOM.

export const MOVE_EVERY_MIN = 4;
const MIN_JUMP = 0.3;
const TRIES = 8;

const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

// "7:05" (no AM or PM) and "Tuesday, November 24", in English whatever the locale.
export function clockText(date) {
  const hour = date.getHours() % 12 || 12;
  const minute = String(date.getMinutes()).padStart(2, "0");
  return {
    time: `${hour}:${minute}`,
    date: `${DAYS[date.getDay()]}, ${MONTHS[date.getMonth()]} ${date.getDate()}`,
  };
}

// Milliseconds to the next whole minute, so the text is redrawn once a minute.
export function msToNextMinute(date) {
  return 60000 - (date.getSeconds() * 1000 + date.getMilliseconds());
}

// `minutesSinceMove` counts redraws since the text last moved.
export function moveDue(minutesSinceMove) {
  return minutesSinceMove >= MOVE_EVERY_MIN;
}

// A new spot as fractions (0 to 1) of the free area, away from the last one so the
// move is visible. The last draw is used when every try lands close by.
export function pickSpot(rand, prev) {
  let spot = { x: rand(), y: rand() };
  if (!prev) return spot;
  for (let i = 0; i < TRIES; i += 1) {
    if (Math.hypot(spot.x - prev.x, spot.y - prev.y) >= MIN_JUMP) return spot;
    spot = { x: rand(), y: rand() };
  }
  return spot;
}
