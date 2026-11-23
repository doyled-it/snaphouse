// The panel's More screen and its sub-screens: Frames, Gallery, Bluetooth and
// House health. Bluetooth and health move the page's existing sections in, so
// the existing pairing, devices and health code keeps drawing them.
import { galleryHash, pieceIdFromHash } from "./gallery-viewer.js";
import { galleryPage, moreTiles, showButtons } from "./panel-gallery-model.js";
import { button, el, icon, setText } from "./panel-dom.js";

const TILE_ICONS = {
  frames: "M4 5h16v14H4zM8 15l3-3 2 2 3-3",
  gallery: "M3 3h8v8H3zM13 3h8v8h-8zM3 13h8v8H3zM13 13h8v8h-8z",
  bluetooth: "M7 7l10 10-5 4V3l5 4L7 17",
  health: "M12 21s-7-4.5-7-10a4 4 0 0 1 7-2.5A4 4 0 0 1 19 11c0 5.5-7 10-7 10z",
};

export function createMoreScreens(panel) {
  const host = panel.host;

  // ---- More: four tiles
  const more = el("section", "p-more");
  const tileNodes = new Map();
  let tileKey = "";
  function renderMore() {
    const frames = host.frames();
    const tiles = moreTiles({
      frames: host.framesVisible() ? frames.length : null,
      pictureCount: host.library().length,
      troubled: host.health().troubled.length,
    });
    const key = tiles.map((t) => t.id).join(",");
    if (key !== tileKey) {
      tileKey = key;
      more.replaceChildren();
      tileNodes.clear();
      for (const t of tiles) {
        const b = button("p-tile", "", () => {
          window.location.hash = t.id === "bluetooth" ? "#settings" : `#${t.id}`;
        });
        const title = el("span", "p-tile-title");
        const line = el("span", "p-tile-line");
        b.append(icon(TILE_ICONS[t.id], 44), title, line);
        tileNodes.set(t.id, { b, title, line });
        more.appendChild(b);
      }
    }
    for (const t of tiles) {
      const n = tileNodes.get(t.id);
      setText(n.title, t.title);
      setText(n.line, t.line);
      n.b.dataset.warn = t.warn ? "true" : "false";
    }
  }
  panel.register("more", more, renderMore);

  // ---- Frames
  const frame = el("section", "p-frame");
  const tabs = el("div", "p-frame-tabs");
  const frameBody = el("div", "p-frame-body");
  const mirror = el("div", "p-mirror");
  const mirrorImg = el("img", "p-mirror-img");
  mirrorImg.alt = "What the frame is showing now";
  const mirrorOff = el("div", "p-mirror-off", "Not responding");
  mirror.append(mirrorImg, mirrorOff);
  const actions = el("div", "p-frame-actions");
  const back = button("p-big", "Back", () => host.frameCommand("previous"));
  const nextBtn = button("p-big p-accent", "Next picture", () => host.frameCommand("next"));
  const keep = button("p-big", "Keep this one", () => host.keep());
  actions.append(back, nextBtn, keep);
  frameBody.append(mirror, actions);
  frame.append(tabs, frameBody);
  let tabsKey = "";
  function renderFrame() {
    const rows = host.frames();
    const selected = host.selectedFrame();
    const key = JSON.stringify([rows, selected]);
    tabs.hidden = rows.length < 2;
    if (key !== tabsKey) {
      tabsKey = key;
      tabs.replaceChildren();
      for (const row of rows) {
        const b = button("p-frame-tab", "", () => {
          window.location.hash = `#frame/${row.id}`;
        });
        b.append(el("span", "p-frame-tab-name", row.label));
        if (row.offline) b.append(el("span", "p-frame-tab-flag", "Not responding"));
        if (row.id === selected) b.setAttribute("aria-current", "true");
        tabs.appendChild(b);
      }
    }
    const src = host.mirrorSrc();
    mirrorImg.hidden = !src;
    mirrorOff.hidden = Boolean(src);
    if (src && mirrorImg.getAttribute("src") !== src) mirrorImg.src = src;
    setText(keep, host.pinned() ? "Unpin" : "Keep this one");
  }
  panel.register("frame", frame, renderFrame);

  // ---- Gallery
  const gallery = el("section", "p-gallery");
  const grid = el("div", "p-gallery-grid");
  const pager = el("div", "p-pager");
  const prev = button("p-pager-btn", "Previous", () => {
    galleryAt = Math.max(0, galleryAt - 1);
    renderGallery();
  });
  const pagerLabel = el("div", "p-pager-label");
  const nextPage = button("p-pager-btn", "Next", () => {
    galleryAt += 1;
    renderGallery();
  });
  pager.append(prev, pagerLabel, nextPage);
  const viewer = el("div", "p-viewer");
  const viewerImg = el("img", "p-viewer-img");
  const viewerSide = el("div", "p-viewer-side");
  const viewerTitle = el("div", "p-viewer-title");
  const viewerArtist = el("div", "p-viewer-artist");
  const viewerNote = el("div", "p-viewer-note");
  const viewerButtons = el("div", "p-viewer-buttons");
  viewerSide.append(viewerTitle, viewerArtist, viewerNote, viewerButtons);
  viewer.append(viewerImg, viewerSide);
  gallery.append(grid, pager, viewer);
  let galleryAt = 0;
  let gridKey = "";
  let viewerKey = "";

  function renderGallery() {
    const pieces = host.library();
    const id = pieceIdFromHash(window.location.hash);
    const piece = id ? pieces.find((p) => p.id === id) : null;
    viewer.hidden = !piece;
    grid.hidden = Boolean(piece);
    pager.hidden = Boolean(piece);
    if (piece) {
      renderViewer(piece);
      return;
    }
    const page = galleryPage(pieces, galleryAt);
    galleryAt = page.page;
    const key = JSON.stringify(page.items.map((p) => [p.id, p.thumb, p.title]));
    if (key !== gridKey) {
      gridKey = key;
      grid.replaceChildren();
      for (const p of page.items) {
        const b = button("p-piece", "", () => {
          window.location.hash = galleryHash(p.id);
        });
        const img = el("img", "p-piece-img");
        img.alt = "";
        if (p.thumb) img.src = p.thumb;
        b.append(img, el("span", "p-piece-title", p.title));
        grid.appendChild(b);
      }
    }
    setText(pagerLabel, page.label);
    prev.disabled = !page.hasPrev;
    nextPage.disabled = !page.hasNext;
  }

  function renderViewer(piece) {
    const frames = host.frames();
    const shown = showButtons(piece, frames);
    const key = JSON.stringify([piece.id, shown]);
    if (viewerImg.getAttribute("src") !== piece.url) viewerImg.src = piece.url;
    setText(viewerTitle, piece.title);
    setText(viewerArtist, piece.artist || "");
    setText(viewerNote, shown.note || "");
    if (key !== viewerKey) {
      viewerKey = key;
      viewerButtons.replaceChildren();
      for (const s of shown.buttons) {
        viewerButtons.appendChild(button("p-big p-accent", s.label, () => host.showOnFrame(s.frameId, piece.id)));
      }
      viewerButtons.appendChild(
        button("p-big", "Back to gallery", () => {
          window.location.hash = "#gallery";
        }),
      );
    }
  }
  panel.register("gallery", gallery, renderGallery);

  // ---- Bluetooth: the page's own pairing and devices sections, at panel sizes
  const bluetooth = el("section", "p-bluetooth");
  for (const id of ["pairing", "devices"]) {
    const section = document.getElementById(id);
    if (section) bluetooth.appendChild(section);
  }
  panel.register("settings", bluetooth, () => {});

  // ---- House health: the page's existing lists
  const health = el("section", "p-health");
  const allGood = el("div", "p-health-good", "All good");
  health.appendChild(allGood);
  for (const id of ["needs-attention", "not-set-up"]) {
    const section = document.getElementById(id);
    if (section) health.appendChild(section);
  }
  panel.register("health", health, () => {
    allGood.hidden = host.health().troubled.length > 0 || host.health().notSetUp.length > 0;
  });
}
