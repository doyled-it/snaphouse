// The crop box editor on the viewer's own picture: a 5:3 box with corner
// handles over the fixed whole picture. DOM and pointer wiring only; every
// rule (clamping, aspect lock, minimum size, keys, the request) is crop-model.js
// and the dither is crop-preview.js.

import {
  cropErrorMessage,
  initialBox,
  moveBox,
  nudgeBox,
  pointToFraction,
  resizeBox,
} from "./crop-model.js";
import { placeBox } from "./crop-overlay.js";
import { ditherToPanel, previewSize, sourceRect } from "./crop-preview.js";

const IDLE_FULL_MS = 160;

// `els` holds the nodes (frame, img, boxEl, handles, canvas, status, hint, save,
// cancel, auto), `win` is the window. Returns { open, close, isOpen }.
export function createCropEditor(els, win = window) {
  const doc = win.document;
  let session = null;

  function render() {
    placeBox(els.boxEl, session.box);
    queuePreview(session.dragging);
  }

  // ---- preview: never blocks the pointer ------------------------------------
  function drawPreview(size) {
    const canvas = els.canvas;
    const { box } = session;
    try {
      canvas.width = size.w;
      canvas.height = size.h;
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      const src = sourceRect(box, session.W, session.H);
      ctx.imageSmoothingQuality = "high";
      ctx.drawImage(els.img, src.sx, src.sy, src.sw, src.sh, 0, 0, size.w, size.h);
      const pixels = ctx.getImageData(0, 0, size.w, size.h);
      pixels.data.set(ditherToPanel(pixels.data, size.w, size.h));
      ctx.putImageData(pixels, 0, 0);
    } catch (err) {
      // A picture the browser will not read back: leave the canvas as it is.
    }
  }

  function queuePreview(dragging) {
    if (!session) {
      return;
    }
    win.clearTimeout(session.fullTimer);
    if (!session.frameRequest) {
      session.frameRequest = win.requestAnimationFrame(() => {
        if (session) {
          session.frameRequest = 0;
          drawPreview(previewSize(session.dragging));
        }
      });
    }
    if (dragging) {
      session.fullTimer = win.setTimeout(() => {
        if (session && !session.dragging) {
          drawPreview(previewSize(false));
        }
      }, IDLE_FULL_MS);
    }
  }

  // ---- pointers ----------------------------------------------------------------
  function frameRect() {
    return els.frame.getBoundingClientRect();
  }

  function onPointerDown(event) {
    if (!session || session.gesture || (event.button !== undefined && event.button > 0)) {
      return;
    }
    const handle = event.target.closest && event.target.closest(".vc-handle");
    const inBox = event.target.closest && event.target.closest(".vc-box");
    if (!handle && !inBox) {
      return;
    }
    const rect = frameRect();
    const at = pointToFraction(event.clientX, event.clientY, rect);
    session.gesture = {
      id: event.pointerId,
      kind: handle ? "resize" : "move",
      corner: handle ? handle.dataset.corner : null,
      start: { ...session.box },
      from: at,
    };
    if (handle) {
      // Keep the grab point on the handle: remember how far the pointer is from
      // the corner it holds.
      const corner = session.gesture.corner;
      session.gesture.offset = {
        x: at.x - (corner.includes("w") ? session.box.x : session.box.x + session.box.w),
        y: at.y - (corner.includes("n") ? session.box.y : session.box.y + session.box.h),
      };
    }
    session.dragging = true;
    els.frame.setPointerCapture(event.pointerId);
    event.preventDefault();
  }

  function onPointerMove(event) {
    const g = session && session.gesture;
    if (!g || g.id !== event.pointerId) {
      return;
    }
    const at = pointToFraction(event.clientX, event.clientY, frameRect());
    if (g.kind === "move") {
      session.box = moveBox(g.start, at.x - g.from.x, at.y - g.from.y, session.W, session.H);
    } else {
      const point = { x: at.x - g.offset.x, y: at.y - g.offset.y };
      session.box = resizeBox(g.start, g.corner, point, session.W, session.H);
    }
    event.preventDefault();
    render();
  }

  function onPointerEnd(event) {
    const g = session && session.gesture;
    if (!g || g.id !== event.pointerId) {
      return;
    }
    session.gesture = null;
    session.dragging = false;
    queuePreview(false);
  }

  // ---- keys ----------------------------------------------------------------------
  function onKey(event) {
    if (!session) {
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close();
    } else if (event.key === "Enter" && !(event.target instanceof win.HTMLButtonElement)) {
      event.preventDefault();
      event.stopPropagation();
      submit(session.box);
    } else if (event.key.startsWith("Arrow") && !(event.target instanceof win.HTMLSelectElement)) {
      // Never step the gallery behind the editor.
      event.stopPropagation();
      const next = nudgeBox(session.box, event.key, event.shiftKey, session.W, session.H);
      if (next) {
        event.preventDefault();
        session.box = next;
        render();
      }
    }
  }

  // ---- saving ---------------------------------------------------------------------
  function setBusy(busy) {
    for (const b of [els.save, els.cancel, els.auto]) {
      b.disabled = busy;
    }
  }

  async function submit(box) {
    if (!session || session.saving) {
      return;
    }
    session.saving = true;
    setBusy(true);
    els.status.textContent = "Saving";
    const result = await session.handlers.save(box);
    if (!session) {
      return;
    }
    session.saving = false;
    setBusy(false);
    if (result && result.ok) {
      els.status.textContent = "";
      close({ saved: true });
      return;
    }
    els.status.textContent = cropErrorMessage(result && result.error);
    els.save.textContent = "Retry";
  }

  // ---- open and close -----------------------------------------------------------------
  // `handlers.save(box | null)` resolves to { ok, error }; `handlers.closed()`
  // runs after the editor goes away (saved or not).
  function open(piece, handlers) {
    const W = els.img.naturalWidth;
    const H = els.img.naturalHeight;
    if (session || !(W > 0 && H > 0)) {
      return false;
    }
    session = {
      piece,
      handlers,
      W,
      H,
      box: initialBox(piece, W, H),
      gesture: null,
      dragging: false,
      saving: false,
      frameRequest: 0,
      fullTimer: 0,
    };
    els.frame.classList.add("is-editing");
    els.layer.hidden = false;
    els.boxEl.dataset.tone = "editing";
    for (const h of els.handles) {
      h.hidden = false;
    }
    els.boxEl.tabIndex = 0;
    els.boxEl.setAttribute("role", "group");
    els.boxEl.setAttribute(
      "aria-label",
      "Crop box. Arrow keys move it. Shift with arrows resizes it. Enter saves. Escape cancels."
    );
    els.auto.hidden = !(piece.crop && typeof piece.crop === "object");
    els.save.textContent = "Save";
    els.status.textContent = "";
    setBusy(false);
    els.frame.addEventListener("pointerdown", onPointerDown);
    els.frame.addEventListener("pointermove", onPointerMove);
    els.frame.addEventListener("pointerup", onPointerEnd);
    els.frame.addEventListener("pointercancel", onPointerEnd);
    doc.addEventListener("keydown", onKey, true);
    els.save.onclick = () => submit(session.box);
    els.cancel.onclick = () => close();
    els.auto.onclick = () => submit(null);
    render();
    els.boxEl.focus({ preventScroll: true });
    return true;
  }

  function close({ saved = false } = {}) {
    if (!session) {
      return;
    }
    const { handlers } = session;
    win.clearTimeout(session.fullTimer);
    if (session.frameRequest) {
      win.cancelAnimationFrame(session.frameRequest);
    }
    session = null;
    els.frame.classList.remove("is-editing");
    for (const h of els.handles) {
      h.hidden = true;
    }
    els.boxEl.removeAttribute("tabindex");
    els.boxEl.removeAttribute("role");
    els.boxEl.removeAttribute("aria-label");
    delete els.boxEl.dataset.tone;
    els.frame.removeEventListener("pointerdown", onPointerDown);
    els.frame.removeEventListener("pointermove", onPointerMove);
    els.frame.removeEventListener("pointerup", onPointerEnd);
    els.frame.removeEventListener("pointercancel", onPointerEnd);
    doc.removeEventListener("keydown", onKey, true);
    els.save.onclick = null;
    els.cancel.onclick = null;
    els.auto.onclick = null;
    handlers.closed({ saved });
  }

  return { open, close, isOpen: () => session !== null };
}
