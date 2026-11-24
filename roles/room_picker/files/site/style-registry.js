// The screensaver style registry: one factory per shipped style id. A factory takes
// { doc, win, now, setTimeout, clearTimeout, random } and returns
// { node, start(), stop(), setHues(h), setNight(bool) }: the node to mount in the
// overlay, and the four calls the overlay drives. The ids, names and order are in
// style-list.js; a later style adds its id there and its factory here.

import { createAuroraStyle } from "./aurora-style.js";
import { createClockStyle } from "./clock-style.js";

export const FACTORIES = {
  aurora: createAuroraStyle,
  clock: createClockStyle,
};

// The style for an id, or null when this page has no module for it.
export function createStyle(id, env) {
  const factory = FACTORIES[id];
  return factory ? factory(env) : null;
}
