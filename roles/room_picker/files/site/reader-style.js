// The Weird facts and Quotes screensaver styles: one entry at a time, held, faded out,
// then the next one in a new place so nothing burns in. DOM text and an opacity
// transition only. The list is fetched from the site (facts.json or quotes.json) the
// first time the style starts, so a phone never loads it. The rules are in
// reader-model.js; the look is in style.css (.saver-reader).

import { FADE_MS, createDeck, holdMs, parseEntries, pickPlace, textSize } from "./reader-model.js";

const RETRY_MS = 30000;
const FILES = { facts: "facts.json", quotes: "quotes.json" };

function createReaderStyle(kind, { doc, win, setTimeout, clearTimeout, random, fetch: fetchFn }) {
  const get = fetchFn || ((url) => win.fetch(url));
  const node = doc.createElement("div");
  node.className = `saver-reader is-${kind}`;
  const glow = doc.createElement("div");
  glow.className = "saver-reader-glow";
  const box = doc.createElement("div");
  box.className = "saver-reader-box";
  const textNode = doc.createElement("div");
  textNode.className = "saver-reader-text";
  if (kind === "facts") {
    const label = doc.createElement("div");
    label.className = "saver-reader-label";
    label.textContent = "Weird fact";
    box.append(label);
  }
  box.append(textNode);
  let byNode = null;
  if (kind === "quotes") {
    byNode = doc.createElement("div");
    byNode.className = "saver-reader-by";
    box.append(byNode);
  }
  node.append(glow, box);

  let running = false;
  let timer = null;
  let deck = null;
  let loading = false;
  let place = null;

  function clearTimer() {
    if (timer !== null) clearTimeout(timer);
    timer = null;
  }

  function show() {
    timer = null;
    if (!running || !deck) return;
    const entry = deck.next();
    if (!entry) return;
    textNode.textContent = entry.text;
    if (byNode) byNode.textContent = entry.author || "";
    box.style.setProperty("--rd-size", `${textSize(kind, entry.text.length)}px`);
    place = pickPlace(random, place);
    box.style.setProperty("--rd-x", place.x.toFixed(3));
    box.style.setProperty("--rd-y", place.y.toFixed(3));
    box.classList.add("is-in");
    timer = setTimeout(hide, holdMs(kind, entry.text));
  }

  function hide() {
    box.classList.remove("is-in");
    timer = setTimeout(show, FADE_MS);
  }

  function load() {
    if (loading || deck) return;
    loading = true;
    Promise.resolve()
      .then(() => get(FILES[kind]))
      .then((res) => {
        if (!res || !res.ok) throw new Error("no list");
        return res.json();
      })
      .then((doc2) => {
        const entries = parseEntries(doc2);
        if (entries.length === 0) throw new Error("empty list");
        deck = createDeck(entries, random, kind === "quotes" ? (e) => e.author : undefined);
        loading = false;
        if (running && timer === null) show();
      })
      .catch(() => {
        loading = false;
        if (running && timer === null) timer = setTimeout(() => { timer = null; load(); }, RETRY_MS);
      });
  }

  return {
    node,
    start() {
      if (running) return;
      running = true;
      if (deck) show();
      else load();
    },
    stop() {
      if (!running) return;
      running = false;
      clearTimer();
      box.classList.remove("is-in");
    },
    setHues(h) {
      if (h && Number.isFinite(h.h1)) node.style.setProperty("--rd-h", h.h1);
    },
    setNight(on) {
      node.classList.toggle("is-night", Boolean(on));
    },
  };
}

export const createFactsStyle = (env) => createReaderStyle("facts", env);
export const createQuotesStyle = (env) => createReaderStyle("quotes", env);
