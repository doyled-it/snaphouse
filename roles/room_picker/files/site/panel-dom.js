// Small DOM helpers for the panel layout (no decisions here).
const SVG = "http://www.w3.org/2000/svg";
export const ICONS = {
  listen: "M9 18V6l10-2v12M9 18a3 3 0 1 1-3-3 3 3 0 0 1 3 3zm10-2a3 3 0 1 1-3-3 3 3 0 0 1 3 3z",
  climate: "M12 3v18M5 7l14 10M19 7L5 17",
  garage: "M3 10l9-6 9 6v10H3zM7 20v-7h10v7M7 15.5h10",
  more: "M5 7h14M5 12h14M5 17h14",
  back: "M15 5l-7 7 7 7",
};

export function icon(name, size) {
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.8");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  if (size) {
    svg.setAttribute("width", String(size));
    svg.setAttribute("height", String(size));
  }
  const path = document.createElementNS(SVG, "path");
  path.setAttribute("d", ICONS[name] || name);
  svg.appendChild(path);
  return svg;
}

export function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

export function button(cls, text, onTap) {
  const node = el("button", cls, text);
  node.type = "button";
  node.addEventListener("click", onTap);
  return node;
}

// Set text only when it changed, so a poll that changes nothing touches nothing.
export function setText(node, text) {
  if (node.textContent !== text) node.textContent = text;
}
