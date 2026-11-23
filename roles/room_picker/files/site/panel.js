// The wall panel layout. rooms.js fetches this module only when the URL carries
// panel=1, so phones never get the attribute, the stylesheet or any panel node.
export function initPanel(host) {
  const root = document.documentElement;
  root.dataset.panel = "1";
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = "panel.css";
  document.head.appendChild(link);
  return { host };
}
