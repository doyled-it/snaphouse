// Pure rules for the two banners at the top of every tab. No DOM.
//
// The health banner is for a machine that stopped working or reports a failure.
// A machine that was never built is not trouble; it is listed in Settings under
// "Not set up yet". Which is which comes from the hub, not from this page's
// memory: house-status.json carries each machine's inventory `installed` flag
// (vars/hosts.yml), so a machine that dies stays flagged after every reload.

// entries: { host, installed?, status } where status is the machine's parsed
// status.json or null when the hub could not read it. `installed` is false only
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
        troubled.push(entry.host);
      }
      continue;
    }
    if (status.verify !== "pass" || status.commit !== status.main_commit) {
      troubled.push(entry.host);
    }
  }
  return { troubled, notSetUp };
}

// The health line, or null when nothing is troubled. It never names a machine.
export function healthMessage(troubled) {
  if (troubled.length === 0) {
    return null;
  }
  return "A part of the house isn't responding. Check that it has power and Wi-Fi.";
}

// connection is "up" or "down" for the audio server. The page disables the room
// controls while this returns text.
export function speakerBanner(connection) {
  return connection === "down" ? "Can't reach the speakers right now. Retrying." : null;
}
