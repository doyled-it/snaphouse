# Room picker redesign and art polish: design

Date: 2026-11-09. Repos: `snaphouse` (hub and page) and `nightstand` (frame).

## Why

After the first morning with the art manager, the owner found:

- The library grid is so long that everything below it is hard to reach.
- The grid shows no title or artist, although every piece has both.
- There is no way to delete a piece from the art history or delete what is on the
  frame right now, which is exactly when a bad piece is noticed.
- Tapping a song, episode, or book in the play history does nothing; the owner
  wants an "open with" choice of app.
- 113 of 172 pieces are too tall to rotate. They were fetched on the first night,
  before the landscape rule existed.
- The page looks like a generic dark card kit. The owner asked for a redesign
  using the frontend-design skill and status-app best practices.

## Decided by the owner

- **Tall pieces:** move the 83 clearly tall pieces (aspect below 0.9) to the
  trash (restorable for 30 days) and lower the rotation cutoff from 1.3 to 1.1,
  so the 24 slightly wide pieces rotate too (83 in rotation). The 6 roughly square
  pieces stay in the library but do not rotate.
- **Delete from history and current:** deleting moves the artwork to the trash.
  Deleting what is on the frame also advances the frame to the next piece (one
  redraw).
- **Play history tap:** an "open with" choice of apps.
- **Navigation:** a hamburger menu at the top that jumps to each section of one
  page; the library collapsed and paged.
- **Redesign direction "frame mirror":** the top of the page is a live copy of
  exactly what the e-ink frame shows, with Back, Next, and a menu for the
  current piece beneath it; then now playing; then the rooms as a plain list.
- **Theme:** follow the phone's light or dark setting.

## Visual system

Grounded in the frame's own hardware: the six inks the Spectra 6 panel prints,
as they look on the panel (`nightstand/src/nightstand/palette.py`, `PANEL`).

| Name | Hex | Role (light) |
|---|---|---|
| Paper | `#E9E7E0` | page background |
| Ink | `#2B292A` | text, icons, rules |
| Blue | `#3C4678` | primary accent: links, selected controls, focus ring |
| Red | `#AD443C` | live audio only (a room playing, the progress fill) and destructive actions |
| Yellow | `#CEBC58` | highlights and warnings (the "too tall" badge) |
| Green | `#4A6E4B` | success confirmations |

Dark theme (`prefers-color-scheme: dark`): Ink becomes the background and Paper
the text, dimmed for a bedroom at night (no pure white; body text around 85%
paper); the accents keep their hue with lightness raised just enough for 4.5:1
contrast on text. No pure black, no tinted near-black.

Principles (the builder applies the frontend-design skill; these pin its choices):

- One memorable element: the frame mirror. It is shown at the frame's true 5:3
  ratio on a slightly raised paper mat, so the page reads as a window onto the
  bedside frame. Everything else is quiet.
- No card kit. Sections are separated by space and a single hairline in Ink at
  low opacity; rooms are a list with rows, not boxes.
- Typography: one bundled OFL family served by the hub (no external font
  requests), chosen deliberately for small-screen legibility (not Inter, which is
  the frame's own face, and not a system default), with a clear type scale.
  Sentence case everywhere; no all-caps labels, no eyebrow labels, no middle-dot
  meta strings, no arrows appended to button text.
- Motion only in answer to an action (a drawer opening, a row confirming a
  delete); `prefers-reduced-motion` respected.
- Copy is plain and specific: "Next piece", "Delete this piece", "Moved to trash.
  Undo", "The frame takes about 30 seconds to change".
- Quality floor: responsive from 360 px phones up to a laptop, touch targets at
  least 44 px, visible keyboard focus, AA contrast in both themes.

## Page structure

Top bar: a hamburger button and the house name; the button opens a drawer
listing the sections (Frame, Now playing, Rooms, Library, Art history, Play
history, Rotation, Upload, Trash); choosing one scrolls there and closes the
drawer. The drawer is a real dialog (focus trapped, Escape closes).

1. **Frame:** the mirror image (`/frame.png`, with "Updated 2:38" from its
   timestamp), the current piece's title and artist (linking to the museum page
   when there is one), Back, Next, and a menu with "Show in library" and "Delete
   this piece". When now playing holds the frame, the mirror shows that, which is
   the truth.
2. **Now playing:** as today (art, title, show or artist, progress, rooms), in the
   new visual system.
3. **Rooms:** one row per room: name, on/off, volume slider.
4. **Library:** collapsed by default with a count ("172 pieces, 83 in rotation");
   expanded, it shows a folder filter and a paged grid (24 per page) whose tiles
   show the thumbnail, title, and artist, with the per-piece menu as today.
5. **Art history:** each entry with title, artist, when it was shown, and Delete.
6. **Play history:** each entry tappable; the tap opens an "Open in" sheet.
7. **Rotation:** interval and folder toggles. 8. **Upload.** 9. **Trash.**

### "Open in" sheet

The real Android share sheet needs a secure (https) page and the hub serves plain
http on the LAN, so the sheet is a short list of links, each opening the app
Android associates with it (call):

- Songs: Apple Music, Spotify, YouTube Music, web search.
- Podcasts (kind `long_form` with a podcast genre or a show): Apple Podcasts,
  Spotify, Pocket Casts, web search.
- Books (audiobook genre): libro.fm, web search.

Each link is a search for the title plus the show or artist, URL-encoded. The
builder verifies each service's current search URL shape before using it and
drops any service without a stable public search URL.

## Data changes

- **Frame mirror:** after every successful push the frame writes the image it
  sent, passed through `simulate_panel` so it looks like the panel, as
  `frame.png` (800x480 PNG, atomic, 0644) plus `frame.json`
  (`{"updated_at": ..., "screen": "art" | "now_playing" | ...}`) into its public
  directory. The hub's 60 s house-status timer republishes both same-origin as
  `/frame.png` and `/frame.json`, with the same hardening it uses for the art
  history (size cap, type check, atomic write); a missing file hides the mirror.
- **Art history ids:** each art-history entry gains `id` (the piece id relative to
  the art folder), additive to the pinned contract; the page deletes by `id` and
  falls back to `file` for older entries.
- **Rotation cutoff:** the hub's control file gains `settings.min_aspect` (default
  1.1), the hub's `landscape` flag uses it, and the frame follows it (local
  config is the fallback). One source of truth for every frame.
- **Tall pieces:** after deploy, the controller moves the 83 pieces with aspect
  below 0.9 to the trash through the API and checks the counts.

## Non-goals

- HTTPS on the LAN (would unlock the real share sheet; a later project).
- Editing the history file's past entries beyond adding `id` going forward.
