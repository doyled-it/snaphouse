// The wall panel layout. rooms.js fetches this module only when the URL carries
// panel=1, so phones never get the attribute, the stylesheet or any panel node.
// Decisions live in the panel-*-model.js files; this file builds and updates DOM.
import { BACK_HASH, isSubScreen, railActive, railItems, resolvePanelScreen, stripBack, stripParts } from "./panel-model.js";
import { garageView } from "./garage-model.js";

const SVG = "http://www.w3.org/2000/svg";
const ICONS = {
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

export function initPanel(host) {
  const root = document.documentElement;
  root.dataset.panel = "1";
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = "panel.css";
  document.head.appendChild(link);

  const shell = el("div", "p-shell");
  shell.id = "p-root";
  const rail = el("nav", "p-rail");
  rail.setAttribute("aria-label", "Pages");
  const content = el("div", "p-content");
  const strip = el("div", "p-strip");
  const stripBackBtn = button("p-strip-back", "", () => {
    window.location.hash = BACK_HASH;
  });
  stripBackBtn.append(icon("back", 28), el("span", "p-strip-back-label", "More"));
  const stripStatus = el("div", "p-strip-status");
  const stripTemp = el("span", "p-strip-temp");
  const stripMode = el("span", "p-strip-mode");
  const stripGarage = el("span", "p-strip-garage");
  stripStatus.append(stripTemp, stripMode, stripGarage);
  const stripTime = el("span", "p-strip-time");
  strip.append(stripBackBtn, stripStatus, stripTime);
  const stage = el("main", "p-stage");
  content.append(strip, stage);
  shell.append(rail, content);
  document.body.appendChild(shell);

  const screens = new Map(); // name -> { node, render(ctx) }
  const railButtons = new Map();
  let current = "listen";
  let railKey = "";

  function visible() {
    return host.navVisible();
  }

  function route() {
    const { screen, hash } = resolvePanelScreen(window.location.hash, visible());
    if (window.location.hash !== hash) history.replaceState(null, "", hash);
    current = screen;
    render();
  }

  function renderRail() {
    const items = railItems(visible());
    const key = items.map((i) => i.screen).join(",");
    if (key !== railKey) {
      railKey = key;
      rail.replaceChildren();
      railButtons.clear();
      for (const item of items) {
        const b = button("p-rail-item", "", () => {
          window.location.hash = `#${item.screen}`;
        });
        b.append(icon(item.screen, 32), el("span", "p-rail-label", item.label));
        rail.appendChild(b);
        railButtons.set(item.screen, b);
      }
    }
    const active = railActive(current);
    for (const [screen, b] of railButtons) {
      if (screen === active) b.setAttribute("aria-current", "page");
      else b.removeAttribute("aria-current");
    }
  }

  function renderStrip() {
    const st = host.state;
    const back = stripBack(current);
    stripBackBtn.hidden = !back;
    stripStatus.hidden = back;
    stripTime.hidden = back;
    const parts = stripParts({
      thermostat: st.climate ? st.climate.thermostat : null,
      garage: st.garage ? garageView(st.garage, Date.now()) : null,
      now: new Date(),
    });
    setText(stripTemp, parts.temp === null ? "" : `${parts.temp}°`);
    stripTemp.hidden = parts.temp === null;
    setText(stripMode, parts.mode || "");
    stripMode.hidden = !parts.mode;
    setText(stripGarage, parts.garage || "");
    stripGarage.hidden = !parts.garage;
    setText(stripTime, parts.time);
  }

  function render() {
    renderRail();
    renderStrip();
    for (const [name, screen] of screens) {
      screen.node.hidden = name !== current;
    }
    const active = screens.get(current);
    if (active) active.render({ host, screen: current });
  }

  const api = {
    host,
    stage,
    route,
    render,
    current: () => current,
    // A screen module registers its node and its render function.
    register(name, node, renderFn) {
      node.hidden = true;
      stage.appendChild(node);
      screens.set(name, { node, render: renderFn });
    },
  };

  window.addEventListener("hashchange", route);
  host.timers.add(render, 500);
  api.isSub = isSubScreen;
  route();
  return api;
}
