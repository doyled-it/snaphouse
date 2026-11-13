// The Listen tab's rules, pure (no DOM, no network of its own).
//
// A "room" here is { id, on, reachable, volume }. The page maps each row from
// roomsFromStatus (which says `connected`) to this shape before calling in.
// Every plan is a list of { id, on } power writes. Volumes are never part of a
// plan, so Only here and Everything off leave every slider where it was.

const MAX_NAME = 64;

// Only here: the target on, every other reachable room off. Rooms that are
// already right get no write. An unreachable target cannot be turned on, so the
// plan is empty.
export function onlyHerePlan(rooms, id) {
  const target = rooms.find((r) => r.id === id);
  if (!target || !target.reachable) return [];
  const plan = [];
  for (const room of rooms) {
    if (!room.reachable) continue;
    const want = room.id === id;
    if (Boolean(room.on) !== want) plan.push({ id: room.id, on: want });
  }
  return plan;
}

// Everything off: every reachable room that is on goes off.
export function everythingOffPlan(rooms) {
  return rooms
    .filter((r) => r.reachable && r.on)
    .map((r) => ({ id: r.id, on: false }));
}

// What a row shows. `rooms` (the whole list) lets the row know whether it is
// already the only one on, in which case "Only here" would do nothing.
export function roomState(room, rooms = [room]) {
  if (!room.reachable) {
    return { label: "Not responding", disabled: true, showOnlyHere: false };
  }
  const label = room.on ? `${Math.round(room.volume)}%` : "Off";
  const othersOn = rooms.some((r) => r.id !== room.id && r.reachable && r.on);
  const alreadyOnly = Boolean(room.on) && !othersOn;
  return { label, disabled: false, showOnlyHere: !alreadyOnly };
}

// The line shown where the track would be when nothing plays.
export function howToText(btName) {
  if (typeof btName === "string" && btName.length > 0) {
    return `Nothing is playing. Connect your phone to "${btName}" over Bluetooth, or pick the house in AirPlay, then play something.`;
  }
  return "Nothing is playing. Connect your phone over Bluetooth or AirPlay, then play something.";
}

// Read the Bluetooth speaker's name from bluetooth.json. `fetchJson(url)`
// resolves to the parsed document. Anything unusable is "no name" (null).
export async function loadBluetoothName(fetchJson) {
  let doc;
  try {
    doc = await fetchJson("bluetooth.json");
  } catch {
    return null;
  }
  if (!doc || typeof doc !== "object" || Array.isArray(doc)) return null;
  const name = doc.name;
  if (typeof name !== "string") return null;
  if (name.trim().length === 0 || name.length > MAX_NAME) return null;
  return name;
}

// The rooms as they will be once `plan` lands: the plan overlaid on the latest
// known rooms. Reachability and volume stay as last reported. Undo diffs against
// this, so it reverts what was sent even before the status echo arrives.
export function applyPlan(rooms, plan) {
  const want = new Map(plan.map((s) => [s.id, s.on]));
  return rooms.map((r) => (want.has(r.id) ? { ...r, on: want.get(r.id) } : r));
}
