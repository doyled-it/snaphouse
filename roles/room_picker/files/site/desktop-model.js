// Desktop layout rules, pure (no DOM). rooms.js reads the media query and applies these.
// The wall panel (?panel=1) never enters desktop mode.

export const DESKTOP_MIN_WIDTH = 1100;
export const DESKTOP_QUERY = `(min-width: ${DESKTOP_MIN_WIDTH}px)`;

// `matches` is the matchMedia result for DESKTOP_QUERY.
export function isDesktop({ matches, panel }) {
  return Boolean(matches) && !panel;
}

// The phone shows Everything off on Listen only; the desktop rail shows it on every screen.
export function everythingOffHidden(screen, desktop) {
  return desktop ? false : screen !== "listen";
}

// Climate and the garage poll fast while their own tab shows. At desktop width the
// Home chips show them too, so Listen counts as shown there.
export function climatePollShown(screen, desktop) {
  return screen === "climate" || (desktop && screen === "listen");
}

export function garagePollShown(screen, desktop) {
  return screen === "garage" || (desktop && screen === "listen");
}
