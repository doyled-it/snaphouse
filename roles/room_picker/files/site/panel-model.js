// The wall panel's layout rules, pure (no DOM, no network). panel.js draws.

// Panel mode is the kiosk URL's panel=1, the same flag the screensaver reads.
export function isPanel(search) {
  return new URLSearchParams(search || "").get("panel") === "1";
}
