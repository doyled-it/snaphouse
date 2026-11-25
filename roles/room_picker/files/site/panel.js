// The wall panel layout. rooms.js fetches this module only when the URL carries
// panel=1, so phones never get the attribute, the stylesheet or any panel node.
// Decisions live in the panel-*-model.js files; this file builds and updates DOM.
import { BACK_HASH, SCREEN_FADE_MS, isSubScreen, railActive, railItems, resolvePanelScreen, screenFade, stripBack, stripParts } from "./panel-model.js";
import { garageView } from "./garage-model.js";
import { createMusicScreen } from "./panel-music.js";
import { createClimateScreen } from "./panel-climate.js";
import { createMoreScreens } from "./panel-more.js";
import { createGarageScreen } from "./panel-garage.js";
import { button, el, icon, setText } from "./panel-dom.js";
import { createPanelSettings } from "./panel-settings-controller.js";
import { createSettingsScreens } from "./panel-settings.js";
import { settingsFromParams } from "./panel-settings-model.js";
import { parsePanelParams } from "./screensaver-model.js";
import { apiRequest } from "./rooms-model.js";

// The page's own screen name for each panel screen, so its polling rules (pairing,
// devices, climate, garage) keep following what is on show.
const PHONE_SCREEN = { listen: "listen", more: "listen", health: "listen", climate: "climate", garage: "garage", frame: "frame", gallery: "gallery", settings: "settings", screen: "listen", screensaver: "listen", "screensaver-styles": "listen" };

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

  // This panel's own settings (Screen and Screensaver), kept on the hub under the
  // id the kiosk URL carries (pid). The URL's own values are the fallback until the
  // hub answers; lp is the backlight daemon's loopback port, for brightness previews.
  const query = new URLSearchParams(window.location.search);
  const pid = query.get("pid") || "";
  const localPort = query.get("lp") || "";
  const withDeadline = (ms) => (typeof AbortSignal !== "undefined" && AbortSignal.timeout ? AbortSignal.timeout(ms) : undefined);
  const settings = createPanelSettings({
    pid,
    fallback: settingsFromParams(parsePanelParams(window.location.search)),
    getDoc: async () => {
      const response = await fetch("panel-settings.json", { cache: "no-store", signal: withDeadline(8000) });
      if (!response.ok) throw new Error("no settings document");
      return response.json();
    },
    post: async (body) => {
      const { url, init } = apiRequest("/api/panel/settings", body);
      const response = await fetch(url, { ...init, signal: withDeadline(8000) });
      let data = null;
      try {
        data = await response.json();
      } catch {
        data = null;
      }
      return { ok: response.ok, status: response.status, data };
    },
    local: async (path, body) => {
      if (!localPort) return;
      await fetch(`http://127.0.0.1:${localPort}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: withDeadline(2000),
      });
    },
    onChange: (next) => {
      if (host.settingsChanged) host.settingsChanged(next);
    },
    onSaveState: () => {},
    setTimer: (fn, ms) => window.setTimeout(fn, ms),
    clearTimer: (id) => window.clearTimeout(id),
  });

  const screens = new Map(); // name -> { node, render(ctx) }
  const railButtons = new Map();
  let current = "listen";
  let railKey = "";

  function visible() {
    // Screen and Screensaver need this panel's id to save anything.
    return { ...host.navVisible(), settings: settings.available() };
  }

  function route() {
    const { screen, hash } = resolvePanelScreen(window.location.hash, visible());
    if (window.location.hash !== hash) history.replaceState(null, "", hash);
    current = screen;
    host.setScreen(PHONE_SCREEN[screen] || "listen");
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

  // The screen that just left stays up for the crossfade, on top and untouchable.
  let shown = null;
  let leaving = null;
  let leavingTimer = 0;
  function reducedMotion() {
    return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }
  function clearLeaving() {
    window.clearTimeout(leavingTimer);
    if (leaving) {
      leaving.classList.remove("p-screen-out");
      if (leaving !== screens.get(current)?.node) leaving.hidden = true;
      leaving = null;
    }
  }

  function render() {
    // A feature that went away (Garage, Climate, Frame) sends the panel to Music.
    const wanted = resolvePanelScreen(window.location.hash, visible()).screen;
    if (wanted !== current) {
      route();
      return;
    }
    renderRail();
    renderStrip();
    if (current !== shown) {
      const change = screenFade(shown, current);
      shown = current;
      clearLeaving();
      const next = screens.get(current);
      if (change.fade && next && !reducedMotion()) {
        const old = screens.get(change.leaving);
        if (old) {
          leaving = old.node;
          leaving.classList.add("p-screen-out");
          leavingTimer = window.setTimeout(clearLeaving, SCREEN_FADE_MS + 50);
        }
        next.node.classList.add("p-screen-in");
        window.setTimeout(() => next.node.classList.remove("p-screen-in"), SCREEN_FADE_MS + 50);
      }
    }
    for (const [name, screen] of screens) {
      screen.node.hidden = name !== current && screen.node !== leaving;
    }
    const active = screens.get(current);
    if (active) active.render({ host, screen: current });
  }

  const api = {
    host,
    settings,
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

  createMusicScreen(api);
  createGarageScreen(api);
  createClimateScreen(api);
  createMoreScreens(api);
  createSettingsScreens(api);
  window.addEventListener("hashchange", route);
  host.timers.add(render, 500);
  // The hub's settings, read now and every 30 s (asleep while the tab is hidden).
  settings.load();
  host.timers.add(() => settings.load(), 30000);
  api.isSub = isSubScreen;
  route();
  return api;
}
