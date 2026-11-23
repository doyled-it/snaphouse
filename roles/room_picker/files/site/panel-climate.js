// The panel's Climate screen: the inside temperature, and the comfort setpoint
// with big minus and plus buttons. Away rules stay on the phone.
import { climateCards, comfortStep } from "./panel-model.js";
import { button, el, icon, setText } from "./panel-dom.js";

export function createClimateScreen(panel) {
  const host = panel.host;
  const node = el("section", "p-climate");

  const inside = el("div", "p-card p-inside");
  const insideTitle = el("div", "p-card-title", "Inside now");
  const temp = el("div", "p-inside-temp");
  const mode = el("div", "p-inside-mode");
  const who = el("div", "p-inside-who");
  inside.append(insideTitle, temp, mode, who);

  const comfort = el("div", "p-card p-comfort");
  const comfortTitle = el("div", "p-card-title", "Comfort, when home");
  const value = el("div", "p-comfort-value");
  const row = el("div", "p-comfort-row");
  const minus = button("p-comfort-btn", "", () => change(-1));
  minus.setAttribute("aria-label", "Comfort temperature down");
  minus.append(icon("M5 12h14", 56));
  const plus = button("p-comfort-btn", "", () => change(1));
  plus.setAttribute("aria-label", "Comfort temperature up");
  plus.append(icon("M12 5v14M5 12h14", 56));
  row.append(minus, plus);
  const hint = el("div", "p-comfort-hint", "Away rules live on your phone");
  comfort.append(comfortTitle, value, row, hint);
  node.append(inside, comfort);

  function change(dir) {
    const doc = host.state.climate;
    if (!doc) return;
    const next = comfortStep(host.comfort(), dir, doc.settings.away_f);
    if (next !== null) host.setComfort(next);
    render();
  }

  function render() {
    const doc = host.state.climate;
    if (!doc) return;
    const c = climateCards(doc, { now: Date.now(), comfort: host.comfort() });
    setText(temp, c.inside.temp);
    setText(mode, c.inside.modeText);
    mode.dataset.accent = c.inside.modeAccent ? "true" : "false";
    setText(who, c.inside.who);
    setText(value, c.comfort.value);
    minus.disabled = c.comfort.minusDisabled;
    plus.disabled = c.comfort.plusDisabled;
  }

  panel.register("climate", node, render);
}
