// The screensaver's style list and the pure rules over it: ids, names, which styles
// have a module yet, the pick validation the hub applies (lib/snaphouse/panelsettings.py),
// the toggle rule for the Styles screen and the rotation order. No DOM and no imports,
// so the settings model and the overlay can both use it. The factories are in
// style-registry.js.

// The ids are stored on the hub, so they are fixed. This is the registry order.
export const STYLES = [
  { id: "aurora", name: "Aurora" },
  { id: "facts", name: "Weird facts" },
  { id: "quotes", name: "Quotes" },
  { id: "covers", name: "Album covers" },
  { id: "clock", name: "Clock" },
  { id: "sky", name: "Night sky" },
  { id: "planets", name: "Planets" },
  { id: "moon", name: "Moon" },
  { id: "flow", name: "Flow field" },
  { id: "contours", name: "Contours" },
  { id: "life", name: "Game of Life" },
  { id: "harmonograph", name: "Harmonograph" },
];

export const STYLE_IDS = STYLES.map((s) => s.id);

// The styles with a module on this page. A later style's PR adds its id here and its
// factory to style-registry.js (a test pins that the two agree).
export const SHIPPED_IDS = ["aurora", "facts", "quotes", "clock", "flow", "contours", "life", "harmonograph"];

export const CHANGE_CHOICES = [0, 5, 15, 30, 60];
export const DEFAULT_STYLES = ["aurora"];
export const DEFAULT_CHANGE_MIN = 15;

const indexOf = (id) => STYLE_IDS.indexOf(id);
const isShipped = (id) => SHIPPED_IDS.includes(id);

export function styleName(id) {
  const found = STYLES.find((s) => s.id === id);
  return found ? found.name : "Aurora";
}

// The hub's rule: a non-empty list of known ids, in registry order, no duplicates.
export function checkStyles(value) {
  if (!Array.isArray(value) || value.length === 0) return false;
  if (!value.every((v) => typeof v === "string" && indexOf(v) >= 0)) return false;
  for (let i = 1; i < value.length; i += 1) {
    if (indexOf(value[i - 1]) >= indexOf(value[i])) return false;
  }
  return true;
}

// The picks the page can show: shipped ones only, Aurora when none are.
export function shippedPicks(picks) {
  const list = Array.isArray(picks) ? picks.filter(isShipped) : [];
  return list.length > 0 ? list : [...DEFAULT_STYLES];
}

// The style to show after `lastId` (null before anything has been shown). When the
// last one shown is no longer picked, carry on from where it sat in registry order.
export function nextPick(picks, lastId) {
  const list = shippedPicks(picks);
  if (lastId === null || lastId === undefined) return list[0];
  const at = list.indexOf(lastId);
  if (at >= 0) return list[(at + 1) % list.length];
  const after = list.find((id) => indexOf(id) > indexOf(lastId));
  return after || list[0];
}

// Turn a tile on or off. A style with no module is refused, and so is turning off
// the last picked style that has one. `picks` comes back in registry order.
export function togglePick(picks, id) {
  const current = Array.isArray(picks) ? [...picks] : [];
  if (!isShipped(id)) return { ok: false, picks: current };
  if (current.includes(id)) {
    if (current.filter(isShipped).length === 1) return { ok: false, picks: current };
    return { ok: true, picks: current.filter((p) => p !== id) };
  }
  const next = [...current, id].sort((a, b) => indexOf(a) - indexOf(b));
  return { ok: true, picks: next };
}

// "Aurora" for one pick, "6 styles" for several.
export function stylesLabel(picks) {
  const list = shippedPicks(picks);
  return list.length === 1 ? styleName(list[0]) : `${list.length} styles`;
}

// The style the Styles screen's Preview shows: the one last tapped (when it has a
// module), else the first pick.
export function tappedPreviewId(picks, tapped) {
  return isShipped(tapped) ? tapped : shippedPicks(picks)[0];
}
