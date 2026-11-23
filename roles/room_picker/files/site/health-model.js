// Pure rules for the two banners at the top of every tab. No DOM.
//
// The health banner is for a machine that stopped working or reports a failure.
// A machine that was never built is not trouble; it is listed in Settings under
// "Not set up yet". Which is which comes from the hub, not from this page's
// memory: house-status.json carries each machine's inventory `installed` flag
// (vars/hosts.yml), so a machine that dies stays flagged after every reload.

// entries: { host, installed?, status } where status is the machine's parsed
// status.json or null when the hub could not read it. `troubled` is a list of
// { host, reason } with reason "unreachable", "verify" or "behind". `installed` is false only
// for a machine the inventory marks as not built yet.
export function classifyMachines(entries) {
  const troubled = [];
  const notSetUp = [];
  for (const entry of entries) {
    const status = entry.status;
    if (!status) {
      if (entry.installed === false) {
        notSetUp.push(entry.host);
      } else {
        troubled.push({ host: entry.host, reason: "unreachable" });
      }
      continue;
    }
    if (status.verify !== "pass") {
      troubled.push({ host: entry.host, reason: "verify" });
    } else if (status.commit !== status.main_commit) {
      troubled.push({ host: entry.host, reason: "behind" });
    }
  }
  return { troubled, notSetUp };
}

// The health line, or null when nothing is troubled. It never names a machine
// but picks its words by cause: any unreachable machine wins, otherwise the
// machine is answering and is checking itself or catching up.
export function healthMessage(troubled) {
  if (troubled.length === 0) {
    return null;
  }
  if (troubled.some((t) => t.reason === "unreachable")) {
    return "A part of the house isn't responding. Check that it has power and Wi-Fi.";
  }
  return "A part of the house is having trouble. It usually sorts itself out in a few minutes.";
}

// The plain reason shown beside a machine in Settings.
export function reasonLabel(reason) {
  if (reason === "unreachable") return "Not responding";
  if (reason === "behind") return "Updating";
  return "Self-check failed";
}

// connection is "up" or "down" for the audio server. The page disables the room
// controls while this returns text.
export function speakerBanner(connection) {
  return connection === "down" ? "Can't reach the speakers right now. Retrying." : null;
}

// One row per machine for the wall panel's House health: problems first (in
// the order given), then healthy machines, then machines not set up yet. The
// verdicts come from classifyMachines; nothing new is decided here. state is
// "attention", "ok" or "none".
export function healthRows(entries) {
  const { troubled, notSetUp } = classifyMachines(entries);
  const bad = new Set(troubled.map((t) => t.host));
  const idle = new Set(notSetUp);
  return [
    ...troubled.map((t) => ({ host: t.host, state: "attention", text: reasonLabel(t.reason) })),
    ...entries.filter((e) => !bad.has(e.host) && !idle.has(e.host)).map((e) => ({ host: e.host, state: "ok", text: "OK" })),
    ...notSetUp.map((host) => ({ host, state: "none", text: "Not set up yet" })),
  ];
}
