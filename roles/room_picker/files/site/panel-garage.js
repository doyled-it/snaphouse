// The panel's Garage screen: the door state on the left, one tall two-tap
// button on the right whose fill shows how closed the door is.
import { garageView } from "./garage-model.js";
import { armState, garageButton, garageFill } from "./panel-garage-model.js";
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
    const pct = f.fill === null ? 0 : f.fill;
    if (shownFill !== pct) {
      shownFill = pct;
      level.style.clipPath = `inset(${(1 - pct) * 100}% 0 0 0)`;
    }
    level.hidden = f.fill === null;
    setText(label, armed ? arm.label : f.text || (b.action === "close" ? "Close" : b.action === "open" ? "Open" : ""));
  }

  panel.register("garage", node, render);
}
