// The room volume slider: a div with role="slider" that follows the finger.
// The send/hold rules live in volume-controller.js (pure, tested with a fake
// clock); this file is the DOM half. Fill and thumb move by transform, at most
// once per animation frame, using a track width cached by a ResizeObserver so no
// frame reads layout. Haptics fire only from pointer and key handlers.

import { createVolumeController } from "./volume-controller.js";
import { HORIZONTAL, VERTICAL, keyStepFor, pointerValue } from "./slider-model.js";

const THUMB_PX = 26;

export function createVolumeSlider({
  label,
  orientation = HORIZONTAL,
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
  let trackSize = 0; // width when horizontal, height when vertical
  let trackRect = { left: 0, top: 0, width: 0, height: 0 };
  let captured = null;
  let frameQueued = false;

  function paint() {
    frameQueued = false;
    const vertical = mode === VERTICAL;
    if (!(trackSize > 0)) {
      const measured = vertical ? el.offsetHeight : el.offsetWidth;
      if (typeof measured === "number") trackSize = measured;
    }
    // clip-path, not scaleX: a scaled fill is rasterized once and stretched,
    // which showed as blocky stripes on Android while dragging. A vertical
    // fader fills from the bottom.
    fill.style.clipPath = vertical
      ? `inset(${100 - value}% 0px 0px 0px round 8px)`
      : `inset(0px ${100 - value}% 0px 0px round 8px)`;
    const travel = Math.max(0, trackSize - THUMB_PX);
    // `translate`, not `transform`: CSS applies the held `scale: 1.2` before
    // `transform`, which would stretch a translate and overshoot the track.
    // The vertical thumb rests at the bottom and travels up (negative y).
    const offset = (value / 100) * travel;
    thumb.style.translate = vertical ? `0px ${offset === 0 ? 0 : -offset}px` : `${offset}px 0px`;
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
      const box = entries[0] && entries[0].contentRect ? entries[0].contentRect : null;
      const w = box ? (mode === VERTICAL ? box.height : box.width) : 0;
      if (w > 0 && w !== trackSize) {
        trackSize = w;
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

  function eventValue(e) {
    return pointerValue(mode, { x: e.clientX, y: e.clientY }, trackRect);
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
    trackRect = { left: rect.left, top: rect.top, width: rect.width, height: rect.height };
    trackSize = mode === VERTICAL ? rect.height : rect.width;
    captured = e.pointerId;
    try {
      el.setPointerCapture(e.pointerId);
    } catch (_) {
      // A synthetic or already-ended pointer cannot be captured; the drag still works.
    }
    // Mark pointer focus so the keyboard focus ring stays hidden after a tap;
    // the first key press removes the mark and the ring returns.
    el.classList.add("pointer-focus");
    try {
      el.focus({ preventScroll: true, focusVisible: false });
    } catch (_) {
      // Focus is a convenience so arrow keys work right after a tap.
    }
    el.classList.add("drag");
    const v = eventValue(e);
    change(v);
    controller.begin(v);
    if (e.preventDefault) e.preventDefault();
  });
  el.addEventListener("pointermove", (e) => {
    if (captured === null || e.pointerId !== captured) return;
    const v = eventValue(e);
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
    el.classList.remove("pointer-focus");
    if (!enabled) return;
    let next;
    if (e.key === "Home") next = 0;
    else if (e.key === "End") next = 100;
    else {
      const step = keyStepFor(e.key, mode);
      if (step === null) return;
      next = Math.max(0, Math.min(100, value + step));
    }
    e.preventDefault();
    change(next);
    controller.begin(next);
    controller.end();
  });

  function applyMode() {
    if (mode === VERTICAL) el.setAttribute("aria-orientation", "vertical");
    else el.removeAttribute("aria-orientation");
    el.setAttribute("data-orientation", mode);
  }

  let mode = orientation === VERTICAL ? VERTICAL : HORIZONTAL;
  applyMode();
  render(0);

  return {
    el,
    // Switch between the phone's horizontal track and the desktop's vertical
    // fader on a live slider. The cached size is dropped and re-measured.
    setOrientation(next) {
      const to = next === VERTICAL ? VERTICAL : HORIZONTAL;
      if (to === mode) return;
      release();
      mode = to;
      trackSize = 0;
      applyMode();
      render(value);
    },
    set(percent) {
      render(percent);
    },
    setEnabled(on) {
      enabled = Boolean(on);
      if (!enabled) release();
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
