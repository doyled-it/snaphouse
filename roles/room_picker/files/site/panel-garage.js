// The panel's Garage screen: the door state on the left, one tall two-tap
// button on the right whose fill shows how closed the door is.
import { garageView } from "./garage-model.js";
import { armState, garageButton, garageFill, glideOpen, glideRate } from "./panel-garage-model.js";
import { button, el, setText } from "./panel-dom.js";

export function createGarageScreen(panel) {
  const host = panel.host;
  const node = el("section", "p-garage");

  const info = el("div", "p-card p-garage-info");
  const heading = el("div", "p-card-title", "Garage door");
  const word = el("div", "p-garage-word");
  const duration = el("div", "p-garage-line");
  const note = el("div", "p-garage-line p-garage-note");
  info.append(heading, word, duration, note);

  const main = button("p-garage-btn", "", onTap);
  const level = el("div", "p-garage-fill");
  const label = el("div", "p-garage-label");
  main.append(level, label);
  node.append(info, main);

  let arm = null;
  let armTimer = null;
  let lastState = null;
  let movingSince = 0;
  let shownFill = -2;
  // The glide while the door moves with a known position (see glideOpen).
  let report = null; // last { open, ms } the opener reported
  let prevReport = null;
  let rate = 0;
  let glideDir = 0;
  let shownOpen = null;
  let raf = 0;

  function drawFill(fill) {
    if (shownFill !== fill) {
      shownFill = fill;
      level.style.clipPath = `inset(${(1 - fill) * 100}% 0 0 0)`;
    }
  }

  function glideFrame() {
    raf = 0;
    if (!glideDir || !report || (arm && arm.armed)) return;
    shownOpen = glideOpen(report, glideDir, rate, Date.now(), shownOpen);
    drawFill(1 - shownOpen);
    const pct = Math.round((glideDir > 0 ? shownOpen : 1 - shownOpen) * 100);
    setText(label, `${glideDir > 0 ? "Opening" : "Closing"}, ${pct}%`);
    raf = requestAnimationFrame(glideFrame);
  }

  function stopGlide() {
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
    glideDir = 0;
    report = null;
    prevReport = null;
    shownOpen = null;
  }

  // Track the opener's reports while the door moves; true while gliding.
  function followReports(doc) {
    const dir = doc.state === "opening" ? 1 : doc.state === "closing" ? -1 : 0;
    if (!dir || typeof doc.position !== "number") {
      stopGlide();
      return false;
    }
    if (dir !== glideDir) stopGlide();
    glideDir = dir;
    const open = Math.max(0, Math.min(1, doc.position / 100));
    if (!report || report.open !== open) {
      prevReport = report;
      report = { open, ms: Date.now() };
      rate = glideRate(prevReport, report);
    }
    if (!raf) raf = requestAnimationFrame(glideFrame);
    return true;
  }

  function view() {
    const doc = host.state.garage;
    return doc ? { doc, view: garageView(doc, Date.now()) } : null;
  }

  async function onTap() {
    const now = view();
    if (!now || host.garageBusy()) return;
    const b = garageButton(now.doc, now.view);
    arm = armState(arm, { type: "tap", action: b.action, disabled: b.disabled }, Date.now());
    clearTimeout(armTimer);
    if (arm.armed) {
      armTimer = setTimeout(() => {
        arm = armState(arm, { type: "tick" }, Date.now());
        render();
      }, arm.expiresMs - Date.now() + 20);
    }
    if (arm.send) {
      const action = arm.sendAction;
      arm = null;
      render();
      await host.garageTap(action);
    }
    render();
  }

  function render() {
    const now = view();
    if (!now) return;
    const { doc, view: v } = now;
    if (doc.state !== lastState) {
      lastState = doc.state;
      movingSince = Date.now();
    }
    arm = armState(arm, { type: "tick" }, Date.now());
    const b = garageButton(doc, v);
    const f = garageFill(doc, v, Date.now(), movingSince);
    word.dataset.state = v.word.toLowerCase();
    setText(word, v.word);
    setText(duration, v.duration || v.lastSeen || "");
    setText(note, v.line || host.garageNotice() || "");
    const armed = arm.armed;
    main.dataset.armed = armed ? "true" : "false";
    main.disabled = b.disabled && !armed;
    level.hidden = f.fill === null;
    if (followReports(doc) && !armed) return; // glideFrame draws the fill and label
    drawFill(f.fill === null ? 0 : f.fill);
    setText(label, armed ? arm.label : f.text || (b.action === "close" ? "Close" : b.action === "open" ? "Open" : ""));
  }

  panel.register("garage", node, render);
}
