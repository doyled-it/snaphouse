// The Home status strip's DOM half. The chip text and state come from
// desktop-status-model.js; this only patches the chips in place, so a poll that
// changes nothing leaves hover and focus alone. A chip is a link to its screen.

import { patchKeyed } from "./keyed-list.js";

export function renderStatusStrip(strip, chips, doc = globalThis.document) {
  strip.hidden = chips.length === 0;
  patchKeyed(strip, chips, {
    key: (chip) => chip.id,
    sig: (chip) => JSON.stringify([chip.text, chip.state, chip.target]),
    create: (chip) => {
      const link = doc.createElement("a");
      link.className = "home-chip";
      link.setAttribute("href", chip.target);
      link.dataset.chip = chip.id;
      link.dataset.state = chip.state;
      const dot = doc.createElement("span");
      dot.className = "home-chip-dot";
      dot.setAttribute("aria-hidden", "true");
      const text = doc.createElement("span");
      text.className = "home-chip-text";
      text.textContent = chip.text;
      link.append(dot, text);
      return link;
    },
  });
}
