// The panel's settings store: reads this panel's entry from the hub's
// panel-settings.json, saves edits through one autosave queue, and talks to the
// backlight daemon's loopback API so a change shows at once. No DOM. Everything
// outside (fetching, posting, the daemon call, timers) is injected, so the rules
// are tested without a browser. panel.js creates it; the screens only call it.
import { createAutosave } from "./autosave-queue.js";
import { checkPatch, diffPatch, parsePanelSettings } from "./panel-settings-model.js";

const PREVIEW_BRIGHTNESS_S = 3;
const PREVIEW_SAVER_S = 15;

export function createPanelSettings({
  pid,
  fallback,
  getDoc,
  post,
  local,
  onChange = () => {},
  onSaveState = () => {},
  setTimer,
  clearTimer,
}) {
  let current = { ...fallback };
  // What the hub last said (null until the first good read or write).
  let server = null;
  const touched = new Set();
  let queueState = "idle";
  const hasPid = typeof pid === "string" && pid !== "";

  const queue = createAutosave({
    delayMs: 0,
    setTimer,
    clearTimer,
    onState(state) {
      queueState = state;
      onSaveState(state);
    },
    async write(latest) {
      const body = server ? diffPatch(server, latest) : Object.fromEntries([...touched].map((k) => [k, latest[k]]));
      if (Object.keys(body).length === 0) return;
      let reply;
      try {
        reply = await post({ panel: pid, ...body });
      } catch {
        throw new Error("Couldn't reach the house.");
      }
      if (!reply || !reply.ok || !reply.data || !reply.data.settings) {
        throw new Error((reply && reply.data && reply.data.error) || "Couldn't save.");
      }
      server = { ...reply.data.settings };
      // Tell the backlight daemon to read the hub now. A miss is fine: its own
      // read every 10 s still applies the change.
      try {
        await local("/refresh", {});
      } catch {
        /* ignored */
      }
    },
  });
  queue.seed(current);

  const busy = () => queueState === "pending" || queueState === "saving" || queueState === "failed";

  return {
    // A panel without an id in its URL cannot save, so its settings pages stay out.
    available: () => hasPid,
    settings: () => current,
    saveState: () => queueState,

    // Read the hub's document. Never overwrites a queue that is pending, saving or
    // failed, and a missing or malformed document leaves the current values alone.
    async load() {
      let parsed = null;
      try {
        parsed = parsePanelSettings(await getDoc(), pid, current);
      } catch {
        return;
      }
      if (!parsed) return;
      server = { ...parsed };
      if (busy()) return;
      current = parsed;
      queue.seed(current);
      onChange(current);
    },

    // Apply an edit now and queue its write. Returns { ok, error }.
    edit(patch) {
      if (!hasPid) return { ok: false, error: "This panel cannot save settings." };
      const checked = checkPatch(current, patch);
      if (!checked.ok) return { ok: false, error: checked.error };
      current = { ...current, ...checked.patch };
      for (const key of Object.keys(checked.patch)) touched.add(key);
      onChange(current);
      queue.edit(checked.patch, { delayMs: 0 });
      return { ok: true, error: null };
    },

    retry: () => queue.retry(),
    flush: () => queue.flush(),

    // Show a brightness on the real backlight for 3 s, then back to normal.
    async previewBrightness(pct) {
      try {
        await local("/preview", { pct, seconds: PREVIEW_BRIGHTNESS_S });
      } catch {
        /* ignored */
      }
    },

    // Hold the screensaver brightness while the preview shows (15 s at most), or
    // release it when the preview ends.
    async previewScreensaver(on) {
      try {
        await local("/preview", { pct: current.screensaver_pct, seconds: on ? PREVIEW_SAVER_S : 0 });
      } catch {
        /* ignored */
      }
    },
  };
}
