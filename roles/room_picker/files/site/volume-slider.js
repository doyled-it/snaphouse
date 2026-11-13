// The room volume slider: a div with role="slider" that follows the finger.
// The send/hold rules live in volume-controller.js (pure, tested with a fake
// clock); this file is the DOM half. Fill and thumb move by transform, at most
// once per animation frame, using a track width cached by a ResizeObserver so no
// frame reads layout. Haptics fire only from pointer and key handlers.

import { createVolumeController, valueFromPointer, keyStep } from "./volume-controller.js";

const THUMB_PX = 26;

export function createVolumeSlider({
  label,
  onSend,
  onRender,
  haptics,
  doc = globalThis.document,
  raf = (fn) => globalThis.requestAnimationFrame(fn),
  now = () => globalThis.performance.now(),
  setTimer = (fn, ms) => setTimeout(fn, ms),
  clearTimer = (id) => clearTimeout(id),
}) {
  const el = doc.createElement("div");
  el.className = "volume";
  el.setAttribute("role", "slider");
  el.setAttribute("tabindex", "0");
  el.setAttribute("aria-valuemin", "0");
  el.setAttribute("aria-valuemax", "100");
  el.setAttribute("aria-valuenow", "0");
  el.setAttribute("aria-label", label);

  const fill = doc.createElement("div");
  fill.className = "volume-fill";
  const thumb = doc.createElement("div");
  thumb.className = "volume-thumb";
  el.append(fill, thumb);

  const controller = createVolumeController({ send: onSend, now, setTimer, clearTimer });

  let value = 0;
  let enabled = true;
  let trackWidth = 0;
  let trackLeft = 0;
  let captured = null;
  let frameQueued = false;

  function paint() {
    frameQueued = false;
    if (!(trackWidth > 0) && typeof el.offsetWidth === "number") trackWidth = el.offsetWidth;
    fill.style.transform = `scaleX(${value / 100})`;
    const travel = Math.max(0, trackWidth - THUMB_PX);
    thumb.style.transform = `translateX(${(value / 100) * travel}px)`;
    if (onRender) onRender(value);
  }

  function render(percent) {
    value = percent;
    el.setAttribute("aria-valuenow", String(percent));
    if (!frameQueued) {
      frameQueued = true;
      raf(paint);
    }
  }

  if (typeof globalThis.ResizeObserver === "function") {
    new globalThis.ResizeObserver((entries) => {
      const w = entries[0] && entries[0].contentRect ? entries[0].contentRect.width : 0;
      if (w > 0 && w !== trackWidth) {
        trackWidth = w;
        render(value);
      }
    }).observe(el);
  }

  // Move the on-screen value for a gesture and buzz on a boundary crossing.
  function change(next) {
    const prev = value;
    render(next);
    if (haptics) haptics.volume(prev, next);
  }

  function pointerValue(e) {
    return valueFromPointer(e.clientX, trackLeft, trackWidth);
  }

  function release() {
    if (captured === null) return;
    captured = null;
    el.classList.remove("drag");
    controller.end();
  }

  el.addEventListener("pointerdown", (e) => {
    if (!enabled || captured !== null) return;
    const rect = el.getBoundingClientRect();
    trackLeft = rect.left;
    trackWidth = rect.width;
    captured = e.pointerId;
    try {
      el.setPointerCapture(e.pointerId);
    } catch (_) {
      // A synthetic or already-ended pointer cannot be captured; the drag still works.
    }
    el.classList.add("drag");
    const v = pointerValue(e);
    change(v);
    controller.begin(v);
    if (e.preventDefault) e.preventDefault();
  });
  el.addEventListener("pointermove", (e) => {
    if (captured === null || e.pointerId !== captured) return;
    const v = pointerValue(e);
    change(v);
    controller.move(v);
  });
  el.addEventListener("pointerup", (e) => {
    if (e.pointerId === captured) release();
  });
  el.addEventListener("pointercancel", (e) => {
    if (e.pointerId === captured) release();
  });
  el.addEventListener("lostpointercapture", (e) => {
    if (e.pointerId === captured) release();
  });

  el.addEventListener("keydown", (e) => {
    if (!enabled) return;
    let next;
    if (e.key === "Home") next = 0;
    else if (e.key === "End") next = 100;
    else {
      const step = keyStep(e.key);
      if (step === null) return;
      next = Math.max(0, Math.min(100, value + step));
    }
    e.preventDefault();
    change(next);
    controller.begin(next);
    controller.end();
  });

  render(0);

  return {
    el,
    set(percent) {
      render(percent);
    },
    setEnabled(on) {
      enabled = Boolean(on);
      if (enabled) el.removeAttribute("aria-disabled");
      else el.setAttribute("aria-disabled", "true");
      el.setAttribute("tabindex", enabled ? "0" : "-1");
    },
    applyStatus(percent) {
      const r = controller.incoming(percent);
      if (r.show) render(r.value);
    },
  };
}
