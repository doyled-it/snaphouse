// The panel's Screen and Screensaver pages. Decisions live in
// panel-settings-model.js (steps, labels, limits) and the store in
// panel-settings-controller.js; this file only draws and wires. Changes save on
// their own, and a brightness tap or step also shows that brightness on the real
// backlight for 3 s (the daemon does it; a failed call is ignored).
import { createAurora } from "./aurora.js";
import { saveStatus } from "./autosave-queue.js";
import { button, el, icon, setText } from "./panel-dom.js";
import {
  FLOORS,
  WAIT_CHOICES,
  barView,
  pctFromFraction,
  saverView,
  stepPct,
  stepTime,
  timeLabel,
  waitLabel,
} from "./panel-settings-model.js";

const MINUS = "M5 12h14";
const PLUS = "M12 5v14M5 12h14";
const PREVIEW_MAX_MS = 15000;

export function createSettingsScreens(panel) {
  const host = panel.host;
  const store = panel.settings;
  const views = []; // { update(settings) } for every control, redrawn together

  function edit(patch) {
    const out = store.edit(patch);
    if (!out.ok && host.notify) host.notify(out.error);
    redraw();
    return out;
  }

  // ---- controls ---------------------------------------------------------- //

  function row(label, note) {
    const r = el("div", "p-srow");
    const lbl = el("div", "p-slbl", label);
    if (note) lbl.append(el("small", "", note));
    const ctl = el("div", "p-sctl");
    r.append(lbl, ctl);
    return { node: r, ctl };
  }

  function stepButton(label, d, onTap) {
    const b = button("p-sbtn", "", onTap);
    b.setAttribute("aria-label", label);
    b.append(icon(d, 30));
    return b;
  }

  // A brightness bar with minus and plus. A tap or drag on the bar picks a value
  // in steps of 5, shown on the backlight while it is held and saved on release.
  function brightnessRow(label, key, note) {
    const min = FLOORS[key];
    const { node, ctl } = row(label, note);
    const minus = stepButton("Dimmer", MINUS, () => commit(stepPct(store.settings()[key], -1, min)));
    const plus = stepButton("Brighter", PLUS, () => commit(stepPct(store.settings()[key], 1, min)));
    const bar = el("div", "p-hbar");
    bar.setAttribute("role", "slider");
    bar.setAttribute("aria-label", label);
    bar.setAttribute("aria-valuemin", String(min));
    bar.setAttribute("aria-valuemax", "100");
    const fill = el("i", "p-hbar-fill");
    const text = el("span", "p-hbar-text");
    bar.append(fill, text);
    ctl.append(minus, bar, plus);

    let drag = null;
    const fraction = (event) => {
      const r = bar.getBoundingClientRect();
      return r.width > 0 ? (event.clientX - r.left) / r.width : 0;
    };
    function commit(pct) {
      edit({ [key]: pct });
      store.previewBrightness(pct);
    }
    function move(event) {
      const pct = pctFromFraction(fraction(event), min);
      if (pct === drag) return;
      drag = pct;
      draw(pct);
      store.previewBrightness(pct);
    }
    bar.addEventListener("pointerdown", (event) => {
      bar.setPointerCapture?.(event.pointerId);
      move(event);
    });
    bar.addEventListener("pointermove", (event) => {
      if (drag !== null) move(event);
    });
    const release = () => {
      if (drag === null) return;
      const pct = drag;
      drag = null;
      edit({ [key]: pct });
    };
    bar.addEventListener("pointerup", release);
    bar.addEventListener("pointercancel", release);

    function draw(value) {
      const view = barView(value, min);
      fill.style.width = `${view.fill}%`;
      setText(text, view.label);
      bar.dataset.low = view.low ? "true" : "false";
      bar.setAttribute("aria-valuenow", String(value));
      minus.disabled = view.minusDisabled;
      plus.disabled = view.plusDisabled;
    }
    views.push({ update: (s) => draw(drag === null ? s[key] : drag) });
    return node;
  }

  // A time with earlier and later steppers (30 minutes, never equal to the other).
  function timeRow(label, key, otherKey) {
    const { node, ctl } = row(label);
    const earlier = stepButton("Earlier", MINUS, () => edit({ [key]: stepTime(store.settings()[key], -1, store.settings()[otherKey]) }));
    const later = stepButton("Later", PLUS, () => edit({ [key]: stepTime(store.settings()[key], 1, store.settings()[otherKey]) }));
    const value = el("div", "p-tval");
    ctl.append(earlier, value, later);
    views.push({ update: (s) => setText(value, timeLabel(s[key])) });
    return node;
  }

  // A row of chips; the one matching the setting is pressed.
  function chipsRow(label, choices, key, { extra = null, note = null, dims = false } = {}) {
    const { node, ctl } = row(label, note);
    const chips = el("div", "p-schips");
    const nodes = choices.map((c) => {
      const b = button("p-schip", c.label, () => edit({ [key]: c.value }));
      return { c, b };
    });
    for (const n of nodes) chips.append(n.b);
    if (extra) chips.append(extra);
    ctl.append(chips);
    views.push({
      update(s) {
        for (const n of nodes) n.b.setAttribute("aria-pressed", s[key] === n.c.value ? "true" : "false");
      },
    });
    return { node, ctl, dims };
  }

  // ---- save status -------------------------------------------------------- //

  function statusLine() {
    const wrap = el("div", "p-sset-status");
    const text = el("span", "p-sset-status-text");
    const retry = button("p-schip p-sretry", "Retry", () => store.retry());
    wrap.append(text, retry);
    views.push({
      update() {
        const s = saveStatus(store.saveState());
        setText(text, s.text);
        retry.hidden = !s.retry;
      },
    });
    return wrap;
  }

  // ---- Screen -------------------------------------------------------------- //

  const screen = el("section", "p-sset");
  screen.append(
    brightnessRow("Brightness", "brightness_pct"),
    brightnessRow("Night dim", "night_dim_pct", "tap to preview"),
    timeRow("Night starts", "night_start", "night_end"),
    timeRow("Night ends", "night_end", "night_start"),
    statusLine(),
  );
  panel.register("screen", screen, () => redraw());

  // ---- Screensaver ---------------------------------------------------------- //

  const saver = el("section", "p-sset");
  const onRow = chipsRow(
    "Screensaver",
    [{ label: "On", value: true }, { label: "Off", value: false }],
    "screensaver_on",
  );
  const previewBtn = button("p-sbig", "Preview", () => startPreview());
  onRow.ctl.append(previewBtn);
  const waitRow = chipsRow("Starts after", WAIT_CHOICES.map((s) => ({ label: waitLabel(s), value: s })), "screensaver_wait_s");
  const soon = button("p-schip soon", "More soon", () => {});
  soon.disabled = true;
  const styleRow = chipsRow("Style", [{ label: "Aurora", value: "aurora" }], "screensaver_style", { extra: soon });
  const pctRow = brightnessRow("Brightness", "screensaver_pct", "tap to preview");
  saver.append(onRow.node, waitRow.node, styleRow.node, pctRow, statusLine());
  views.push({
    update(s) {
      const v = saverView(s);
      for (const n of [waitRow.node, styleRow.node, pctRow]) n.dataset.dimmed = v.rowsDimmed ? "true" : "false";
      previewBtn.disabled = v.previewDisabled;
    },
  });
  panel.register("screensaver", saver, () => redraw());

  // ---- Preview: the chosen style full screen, at its brightness, until a tap ---- //

  let preview = null;
  function startPreview() {
    if (preview || !store.settings().screensaver_on) return;
    const layer = el("div", "p-saver-preview");
    const canvas = document.createElement("canvas");
    const hint = el("div", "p-saver-preview-hint", "Preview. Tap to end.");
    layer.append(canvas, hint);
    document.body.appendChild(layer);
    const aurora = createAurora({
      canvas,
      getSize: () => ({ width: window.innerWidth, height: window.innerHeight }),
      raf: (fn) => window.requestAnimationFrame(fn),
      caf: (id) => window.cancelAnimationFrame(id),
      nowMs: () => window.performance.now(),
    });
    if (host.hues) aurora.setHues(host.hues());
    aurora.start();
    store.previewScreensaver(true);
    // The click (not the pointerdown) ends it, so the tap's own pointerup and
    // click never land on the page underneath.
    const end = (event) => {
      if (event) {
        event.preventDefault();
        event.stopPropagation();
      }
      if (!preview) return;
      window.clearTimeout(preview.timer);
      aurora.stop();
      layer.remove();
      preview = null;
      store.previewScreensaver(false);
    };
    layer.addEventListener("click", end);
    preview = { timer: window.setTimeout(end, PREVIEW_MAX_MS) };
  }

  // ---- redraw ---------------------------------------------------------------- //

  let lastKey = "";
  function redraw() {
    const s = store.settings();
    const key = JSON.stringify([s, store.saveState()]);
    if (key === lastKey) return;
    lastKey = key;
    for (const v of views) v.update(s);
  }
  redraw();
  return { redraw };
}
