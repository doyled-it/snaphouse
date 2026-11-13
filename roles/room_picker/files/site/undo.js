// Undo helper and toast logic, pure (clock, timers and rendering injected).

// How long a toast lingers before it clears itself.
export const TOAST_MS = 8000;

// Remember which rooms were on, by id, before a bulk action.
export function snapshotRooms(rooms) {
  return new Map(rooms.map((r) => [r.id, Boolean(r.on)]));
}

// The writes that put the house back: only rooms still present and reachable
// whose on/off differs from the snapshot. Volumes are never part of a plan.
export function undoPlan(snapshot, current) {
  const plan = [];
  for (const room of current) {
    if (!snapshot.has(room.id) || !room.reachable) continue;
    const was = snapshot.get(room.id);
    if (Boolean(room.on) !== was) plan.push({ id: room.id, on: was });
  }
  return plan;
}

// One toast at a time: a new show replaces the previous one and its timer.
// render receives { visible, text, undo } where undo is null or a function.
export function createToaster({ setTimer, clearTimer, render }) {
  let timer = null;

  function dismiss() {
    if (timer !== null) clearTimer(timer);
    timer = null;
    render({ visible: false, text: "", undo: null });
  }

  function show(text, onUndo) {
    if (timer !== null) clearTimer(timer);
    let used = false;
    const undo = onUndo
      ? () => {
          if (used) return;
          used = true;
          dismiss();
          onUndo();
        }
      : null;
    render({ visible: true, text, undo });
    timer = setTimer(dismiss, TOAST_MS);
  }

  return { show, dismiss };
}
