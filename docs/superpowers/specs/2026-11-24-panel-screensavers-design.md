# Panel screensavers design

Date: 2026-11-24. Status: decided with the owner 2026-11-24. The owner picked every style
below from two rounds of mockups, then handed over the remaining calls ("just churn through
them, merge them in, don't wait for me"). Calls marked "my call" are the coordinator's.

## Why

The panel's screensaver has one style, Aurora. The owner wants the idle panel to be something
interesting to glance at, day and night alike: things to read (odd facts, quotes from people
they admire), their own music (a wall of album covers), real things (the time, the sky, the
planets, the moon) and generative art. They see these at any hour, so every style needs a
calm night look as well as a day look.

## Mockups

- `2026-11-24-panel-screensavers-mockup-1.html`: Ridiculous facts, Album cover mosaic, Clock,
  Night sky (plus Drift and Motes, which the owner did not keep). Its covers came from the hub
  and are not in the repo, so the mosaic tab shows empty tiles here.
- `2026-11-24-panel-screensavers-mockup-2.html`: Flow field, Contours, Game of Life,
  Harmonograph, Planets, Moon.

Each mockup has Day and Night toggles and is the reference for its style's look. The mockups
run sped up; the real styles run at the speeds below.

## Goals

- Eleven new styles alongside Aurora, each with a day look and a night look.
- Pick any number of styles. The screensaver moves to the next pick each time it starts and,
  optionally, crossfades to the next pick on a timer while it runs.
- Preview a style from the settings.
- The album cover mosaic never shows the same cover twice on screen, drawing on a collection
  of hundreds of covers kept by the hub.

## Non-goals

- No change to when the screensaver shows (the combined rule in the panel-settings design
  stands: with music playing, or paused under 5 minutes, the ambient Now Playing view shows
  instead of any style).
- No internet access from the panel or the hub for these styles. Everything is computed
  locally or shipped in the repo.
- No per-style settings (speed, density). Styles are tuned by eye and fixed.
- No phones: the screensaver stays panel-only.

## Anti-goals

- A misattributed quote, or a "fact" that is false. Every entry carries a source.
- Anything bright, fast or flashing at night.
- The same album cover twice on the screen at once.
- A style that makes the panel sluggish to wake or burns a core all night.
- Album covers or other copyrighted images committed to the repo (it is public).

## Constraints

- Panel: Raspberry Pi 5, 800x480, Chromium kiosk, the existing overlay in the room-picker
  page (`screensaver.js`, `aurora.js`, `screensaver-model.js`). The page is shared with phones;
  all of this stays behind panel mode.
- Settings live on the hub in `lib/snaphouse/panelsettings.py` and are mirrored in
  `panel-settings-model.js`. Both validate; both must change together.
- The repo's rules: tested pure modules (`node --test tests/js/*.test.js`, `uv run pytest
  tests/python`), CI container apply, no em dashes, AGENTS.md kept current.

## Approaches

1. **One module per style in the existing overlay, plugged into a shared style list (chosen).**
   Each style is a small module like `aurora.js` with the same start, stop, hues and night
   interface. Canvas 2D or DOM, as each style needs. It reuses everything the overlay already
   does: the idle rule, the first-tap swallow, the album hues, Preview.
2. GPU shaders for the generative styles. Smoother on paper, but a second drawing stack to
   build and test, and the Pi 5 handles canvas drawing at this size. Kept as a fallback for any
   one style that measures too heavy.
3. Pre-rendered video loops. Cannot follow album colors, the real time, sky or moon, and would
   visibly repeat. Rejected.

## Design

### The style list and rotation

- A style registry module lists every style: id, display name, and a factory returning
  `{ start(), stop(), setHues(h), setNight(bool) }`. Aurora moves behind the same interface.
- Style ids are fixed now, because they are stored on the hub: `aurora`, `facts`, `quotes`,
  `covers`, `clock`, `sky`, `planets`, `moon`, `flow`, `contours`, `life`, `harmonograph`
  (registry order). The hub accepts all twelve from the first PR on; the page skips an id it
  has no module for yet, the "last pick" rule counts only shipped styles, and with no shipped
  style picked the page shows Aurora (my call, from the builder check).
- Settings: `screensaver_styles`, a non-empty list of style ids in the registry's order, and
  `screensaver_change_min`, one of 0 (off), 5, 15, 30, 60; default 15 (owner picked rotation;
  the choices and default are my call). The old `screensaver_style` is read as a one-item list
  when `screensaver_styles` is absent, and is no longer written. Default list: `["aurora"]`.
- Each time the screensaver starts, it shows the next pick after the last one shown (kept in
  memory; a page reload starts at the first pick). While it runs, every `screensaver_change_min`
  minutes it crossfades over about 3 seconds to the next pick. The same rule applies by day and
  by night. Preview never moves the rotation pointer or resets the timer. When the ambient Now
  Playing view takes over and then ends, the same style comes back (the pointer does not move)
  and the timer starts again (my calls).
- Night: a style is told `setNight(true)` during quiet hours (the window the page already
  knows) and switches to its night look, with a crossfade when the hour passes while it runs.

### Settings screens

- The Screensaver page's Style row becomes "Styles", reading "Aurora" for one pick or "6 styles"
  for several, and opens a new `#screensaver-styles` screen.
- That screen is a grid of twelve toggle tiles (4 columns, 3 rows), each with the style's name
  and a check when picked, in the registry's order. The last picked tile cannot be turned off.
  Changes save at once through the existing autosave queue.
- A "Change every" row on the Screensaver page: Off, 5, 15, 30, 60 min. Hidden or disabled when
  only one style is picked.
- Preview on the Styles screen shows the style last tapped on (the first pick if none was
  tapped) full screen in its current day or night look, under the existing Preview rules (tap
  or 15 s ends it, the daemon holds screensaver brightness). Preview on the Screensaver page
  shows the next style in rotation.
- The More tile line reads "<Style> after <wait>" for one pick, "<n> styles after <wait>" for
  several.

### The styles

Speeds are real speeds. Every style keeps any text or bright element moving or relocating at
least every few minutes so nothing burns in. "Album hues" means the style tints itself with
the two hues the page derives from the cover, as Aurora does, with the page's defaults when
nothing has played.

| Style | Day | Night |
|---|---|---|
| Weird facts | One fact at a time on a dark blue field with a faint slow glow, a small "Weird fact" label above it; held about 60 s (longer for long ones), 1.6 s fade, next fact in a new place. Order shuffled, no repeat until the list is exhausted. | Black, dim warm grey text, no label. |
| Quotes | Same frame as Weird facts: the quote, then the person's name below it in a smaller size. Held 45 to 90 s by length. | Same night treatment. |
| Album cover mosaic | Six columns of square covers scrolling slowly upward (about 1 px per second); every 20 to 40 s one tile crossfades to another cover. | Covers darkened and muted, edges fading to black, half the speed. |
| Clock | Large thin 12-hour time without AM/PM, the date below, moving to a new spot every few minutes. | Smaller, dim warm grey on black. |
| Night sky | Deep blue sky, stars turning around a pole above the screen at the real rate (15 degrees an hour), a shooting star at random every few minutes. | Black, fewer and dimmer stars, rarer shooting stars. |
| Planets | The eight planets at their real heliocentric longitudes for now, on tilted rings around the sun, names beside them, the date in a corner. Moves in real time. | Names off, everything dimmed. |
| Moon | A real photo of the moon, shaded to tonight's phase with a soft terminator, its phase name, percent lit and days to the next full or new moon beside it; drifts slowly. | Dimmer photo, dim warm grey text. |
| Flow field | Fine threads of light following a slowly changing noise field, leaving fading trails; album hues. | Fewer, fainter threads. |
| Contours | Topographic contour lines of a slowly changing noise field, every fourth line heavier; album hues. | Fewer, dimmer lines on black. |
| Game of Life | Conway's rules on an 80 by 48 grid, a step about every half second, dying cells leave a fading ghost; reseeds a patch when the population stalls or drops low; album hues. | A step every 1.5 s, dim cells. |
| Harmonograph | A two-pendulum figure drawn over about a minute, held 20 s, faded, then a new random figure; album hues. | Fainter line. |

Details by style:

- **Weird facts.** `facts.json` in the site directory: `[{ "text", "source" }]`, `source` a
  Wikipedia (or other reputable) URL. Odd, surprising facts of the kind Wikipedia's "unusual
  articles" and "Did you know" collect, across history, places, science, language, people and
  objects, not mostly animals. Written in our own words, at most about 160 characters each, at
  least 150 entries to start. Each must be checked against its source.
- **Quotes.** `quotes.json`: `[{ "text", "author", "source" }]`, `source` naming the work,
  speech or interview and its year. Authors: the owner named Albert Camus, Kurt Vonnegut,
  Simone de Beauvoir, Aristotle, Siddhartha Gautama (the Buddha), George Orwell, Rebecca
  Solnit, Amy Goodman, Malcolm X and Che Guevara, "and other existentialist and Marxist
  philosophers and Buddhists and activists" (for example Sartre, Kierkegaard, Marx, Rosa
  Luxemburg, Gramsci, Thich Nhat Hanh, Pema Chodron, James Baldwin, Angela Davis, Howard Zinn;
  my call on the extended list). Only quotes from the sourced sections of Wikiquote or from
  the primary text; never from "Misattributed" or "Disputed" sections. Buddha quotes come from
  a named sutta or the Dhammapada in a named translation. At most about 220 characters, so the
  text fits at 28 px or larger. At least 120 entries to start, spread across the authors.
  Order shuffled, and two quotes by one author never follow each other.
- **Clock.** Local time of the panel.
- **Planets.** Mean longitudes from J2000 elements with circular orbits are accurate enough
  (a few degrees); rings evenly spaced, not to scale.
- **Moon.** A public-domain NASA full-moon photograph (for example from NASA's Scientific
  Visualization Studio or the Lunar Reconnaissance Orbiter), resized to about 600 px and under
  150 KB, committed with a note of its source URL and credit. Phase from the mean synodic month
  against a known new moon; lit on the right while waxing, as seen from the northern
  hemisphere. Libration and the real rise and set are out of scope.
- **Generative styles.** The mockup code is the starting point. Each may draw to a smaller
  canvas scaled up (as Aurora does) if it measures heavy.

### The cover collection (hub)

- The now-playing service keeps a collection of distinct music covers in
  `/var/lib/snaphouse/public/cover/archive/` (named by a hash of the image bytes, so the same
  picture is stored once) and publishes `cover-archive.json`, newest first:
  `{ "covers": [{ "url", "album", "artist", "added_at" }] }`, served beside
  `play-history.json`.
- A cover joins when a music track's cover is found (lookup or the phone's own art), and is
  deduplicated by image hash and by normalized album and artist. Podcasts and audiobooks are
  left out when the kind is known. Cap 1,000 covers, dropping the oldest added (my call;
  about 60 MB at today's sizes, and the hub has 18 GB free).
- On first start with an empty collection, it seeds from the existing lookup cache and the
  history covers. Where a cached cover's kind cannot be told, it is included; a stray podcast
  cover in the mosaic is harmless (my call). A seeded entry whose album and artist are not
  known carries empty strings and is deduplicated by image hash only; the mosaic does not
  need the names (my call).
- The archive is independent of the 50-play history and its pruning.

### The mosaic and its covers

- The page reads `cover-archive.json` when the mosaic starts and every 10 minutes while it
  runs.
- No cover appears twice on screen. A tile swap or a row scrolling in picks covers that are
  not currently visible. With too few covers for six columns, the mosaic uses fewer, larger
  columns (down to three); with fewer than about 12 covers it shows Aurora instead (my call).
- Night look per the table. Album hues are not used; the covers are the color.

### Performance

- Each style is measured on the panel before its merge: Chromium's CPU at or under about 25 %
  of one core, and no visible stutter. A style over that draws at a reduced resolution or a
  lower frame rate (30 fps cap, as Aurora). Animation stops entirely while the overlay is
  hidden, as today.
- The coordinator measures each style on the panel after deploy (top on the kiosk's renderer
  process, with the style forced through Preview or the debug port). A style over budget
  first drops resolution or frame rate; only if that fails does it move to a shader, and that
  change comes back to the coordinator first.

### Delivery

Five pull requests, in order, each merged and deployed to the hub and the panel when its review
is clean and CI passes, under the owner's 2026-11-24 approval:

1. Style registry, Aurora behind it, `screensaver_styles` and `screensaver_change_min` on the
   hub and the page, the Styles screen, rotation and crossfade, Preview, and Clock.
2. Weird facts and Quotes, with their lists.
3. Flow field, Contours, Game of Life, Harmonograph.
4. Night sky, Planets, Moon (with the photo).
5. The hub's cover collection and the Album cover mosaic.

A style's tile appears in the Styles grid only once its module has shipped.

### Docs and tests

- AGENTS.md: the Panel settings and screensaver sections (new settings, styles, the cover
  collection and its endpoint, the data files and their sourcing rule).
- Tests: pytest for the settings validation and migration, and the cover collection (dedupe,
  cap, seed, publish); `tests/js` for the registry, rotation rules, Styles screen model, each
  style's pure math (planet longitudes, moon phase and lit fraction, Life step and reseed,
  marching squares, mosaic no-duplicate selection and column choice), and a data check that
  every fact and quote has a non-empty source and fits its length limit.

## Left to the builder

The module and file names, the exact fonts and sizes within the mockups' look, the noise
function, frame rates within the performance rule, the crossfade mechanics between two styles,
and how the facts and quotes are gathered, as long as every entry is checked against its
source.
