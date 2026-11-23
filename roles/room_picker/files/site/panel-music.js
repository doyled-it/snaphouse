// The panel's Music screen: a now-playing bar over one tall column per room.
import { createVolumeController, valueFromPointerVertical } from "./volume-controller.js";
import { columnFit, nowPlayingBar, progressFraction, roomColumn, snapVolume, stepVolume } from "./panel-model.js";
import { swapImage } from "./cover-color.js";
import { button, el, icon, setText } from "./panel-dom.js";

const TRANSPORT = {
  previous: "M6 5h2.2v14H6zM19 5.5v13a.6.6 0 0 1-.95.5L9.6 13a1.2 1.2 0 0 1 0-2l8.45-6a.6.6 0 0 1 .95.5z",
  next: "M15.8 5H18v14h-2.2zM5 5.5v13a.6.6 0 0 0 .95.5l8.45-6a1.2 1.2 0 0 0 0-2l-8.45-6A.6.6 0 0 0 5 5.5z",
  play: "M7.5 4.7v14.6a.7.7 0 0 0 1.07.6l11.6-7.3a.7.7 0 0 0 0-1.2L8.57 4.1a.7.7 0 0 0-1.07.6z",
  pause: "M7 4.5h3.4v15H7zM13.6 4.5H17v15h-3.4z",
  plus: "M12 5v14M5 12h14",
  minus: "M5 12h14",
};

export function createMusicScreen(panel) {
  const host = panel.host;
  const node = el("section", "p-music");

  // --- now-playing bar
  const bar = el("div", "p-np");
  const cover = el("img", "p-np-art");
  cover.alt = "";
  const text = el("div", "p-np-text");
  const title = el("div", "p-np-title");
  const artist = el("div", "p-np-artist");
  text.append(title, artist);
  const transport = el("div", "p-np-transport");
  const prev = button("p-np-btn", "", () => host.playback("previous"));
  prev.setAttribute("aria-label", "Previous");
  prev.append(icon(TRANSPORT.previous, 40));
  const play = button("p-np-btn p-np-play", "", () => host.playback("playPause"));
  const playIcon = icon(TRANSPORT.play, 44);
  play.append(playIcon);
  const next = button("p-np-btn", "", () => host.playback("next"));
  next.setAttribute("aria-label", "Next");
  next.append(icon(TRANSPORT.next, 40));
  transport.append(prev, play, next);
  const progress = el("div", "p-np-progress");
  const progressFill = el("div", "p-np-progress-fill");
  progress.appendChild(progressFill);
  bar.append(cover, text, transport, progress);

  // --- room columns
  const cols = el("div", "p-cols");
  const columns = new Map(); // id -> entry
  node.append(bar, cols);

  let coverSrc = null;

  function buildColumn(room) {
    const col = el("div", "p-col");
    const name = el("div", "p-col-name");
    const status = el("div", "p-col-status");
    const body = el("div", "p-col-body");
    const track = el("div", "p-fill");
    track.setAttribute("role", "slider");
    track.setAttribute("aria-orientation", "vertical");
    track.setAttribute("aria-valuemin", "0");
    track.setAttribute("aria-valuemax", "100");
    track.tabIndex = 0;
    const level = el("div", "p-fill-level");
    const value = el("div", "p-fill-value");
    track.append(level, value);
    const steps = el("div", "p-steps");
    const entry = { id: room.id, col, name, status, track, level, value, power: null, controller: null, shown: -1 };
    entry.controller = createVolumeController({
      send: (p) => {
        const r = currentRoom(room.id);
        if (r && !r.on) host.power(room.id, true);
        host.volume(room.id, p);
      },
      now: () => Date.now(),
      setTimer: (fn, ms) => setTimeout(fn, ms),
      clearTimer: (id) => clearTimeout(id),
    });
    const plus = button("p-step", "", () => stepBy(entry, 1));
    plus.setAttribute("aria-label", `${room.label} volume up`);
    plus.append(icon(TRANSPORT.plus, 40));
    const minus = button("p-step", "", () => stepBy(entry, -1));
    minus.setAttribute("aria-label", `${room.label} volume down`);
    minus.append(icon(TRANSPORT.minus, 40));
    steps.append(plus, minus);
    entry.plus = plus;
    entry.minus = minus;
    body.append(track, steps);
    const power = button("p-power", "", () => host.power(room.id, !entry.on));
    entry.power = power;
    col.append(name, status, body, power);

    const valueAt = (event) => {
      const rect = track.getBoundingClientRect();
      return snapVolume(valueFromPointerVertical(event.clientY, rect.top, rect.height));
    };
    track.addEventListener("pointerdown", (event) => {
      if (entry.disabled) return;
      track.setPointerCapture?.(event.pointerId);
      entry.dragging = true;
      const v = valueAt(event);
      show(entry, v);
      entry.controller.begin(v);
    });
    track.addEventListener("pointermove", (event) => {
      if (!entry.dragging) return;
      const v = valueAt(event);
      show(entry, v);
      entry.controller.move(v);
    });
    const finish = () => {
      if (!entry.dragging) return;
      entry.dragging = false;
      entry.controller.end();
    };
    track.addEventListener("pointerup", finish);
    track.addEventListener("pointercancel", finish);
    return entry;
  }

  function currentRoom(id) {
    return host.rooms().find((r) => r.id === id);
  }

  function stepBy(entry, dir) {
    if (entry.disabled) return;
    const next = stepVolume(entry.volume, dir);
    show(entry, next);
    entry.controller.begin(next);
    entry.controller.end();
  }

  // The fill moves by clip-path (a scaled fill rasterizes blocky on Android).
  function show(entry, pct) {
    entry.volume = pct;
    if (entry.shown === pct) return;
    entry.shown = pct;
    entry.level.style.clipPath = `inset(${100 - pct}% 0 0 0)`;
    setText(entry.value, String(pct));
    entry.track.setAttribute("aria-valuenow", String(pct));
  }

  function renderBar() {
    const st = host.state;
    const model = nowPlayingBar(st.nowTrack, { btName: st.btName, speakersDown: st.speakers === "down" });
    bar.dataset.mode = model.mode;
    setText(title, model.title);
    setText(artist, model.mode === "idle" ? model.howTo : model.artist);
    transport.hidden = !model.showTransport;
    play.setAttribute("aria-label", model.playing ? "Pause" : "Play");
    playIcon.firstChild.setAttribute("d", model.playing ? TRANSPORT.pause : TRANSPORT.play);
    const art = model.mode === "track" && st.nowTrack.artUrl ? st.nowTrack.artUrl : null;
    cover.hidden = !art;
    if (art !== coverSrc) {
      coverSrc = art;
      if (art) swapImage(cover, art);
    }
    progress.hidden = model.mode !== "track";
    const p = model.mode === "track" ? host.progress() : null;
    progressFill.style.transform = `scaleX(${progressFraction(p).toFixed(4)})`;
  }

  function renderColumns() {
    const rooms = host.rooms();
    const speakersDown = host.state.speakers === "down";
    const key = rooms.map((r) => r.id).join(",");
    if (key !== cols.dataset.key) {
      cols.dataset.key = key;
      cols.replaceChildren();
      columns.clear();
      for (const room of rooms) {
        const entry = buildColumn(room);
        columns.set(room.id, entry);
        cols.appendChild(entry.col);
      }
      cols.dataset.crowded = columnFit(rooms.length).ok ? "false" : "true";
    }
    for (const room of rooms) {
      const entry = columns.get(room.id);
      const c = roomColumn(room, rooms, { speakersDown });
      entry.disabled = c.disabled;
      entry.on = c.power;
      entry.col.classList.toggle("is-greyed", c.greyed);
      setText(entry.name, c.name);
      setText(entry.status, c.status);
      entry.track.setAttribute("aria-label", `${c.name} volume`);
      entry.track.setAttribute("aria-disabled", c.disabled ? "true" : "false");
      entry.plus.disabled = c.disabled;
      entry.minus.disabled = c.disabled;
      entry.power.disabled = c.disabled;
      entry.power.setAttribute("aria-pressed", c.power ? "true" : "false");
      setText(entry.power, c.power ? "On" : "Off");
      const incoming = entry.controller.incoming(c.fillPct);
      if (incoming.show) show(entry, c.fillPct);
    }
  }

  panel.register("listen", node, () => {
    renderBar();
    renderColumns();
  });
}
