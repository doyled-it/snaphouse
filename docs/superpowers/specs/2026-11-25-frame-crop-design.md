# Frame crop: choose what each picture shows on the e-ink frames

Status: design. Roadmap issue `frame-crop` (frame epic), ahead of star-song.

Labels used below: **Owner** marks a decision the owner made. **Proposed** marks a choice the
design author made and the owner approved as part of the whole. **Builder's call** marks
something left to the builder.

## Intent and why

The owner likes the pictures in the frames' gallery (Ente albums, museum art, their uploads), but
many phone photos lose the main subject on the frame. The frames are 800x480 (5:3, wider than a
phone's 4:3 and far wider than a portrait photo), and today the frame chooses the crop itself:
`detail_crop` in nightstand scales the picture to cover the panel and slides toward the busiest
region. Ente portrait photos rotate (Ente folders are exempt from the landscape rule), so they get
a wide strip cut out of a tall photo, often missing the subject.

**Owner:** let them set the crop for every picture in the gallery, and let the gallery show at a
glance whether a picture's crop needs fixing.

Crosses two repos:

- **snaphouse** (this repo): crop storage on the hub, the API, the `crop` field in
  `art-index.json`, the page (viewer overlay, box editor, cropped thumbnails, button labels and
  frame picker), tests, docs, the cross-repo contract entry.
- **nightstand** (frame app): apply the saved crop when rendering, count a cropped piece as
  eligible for rotation, redraw when the shown piece's crop changes.

## Goals

1. **Owner:** the Gallery viewer shows the whole picture with the frame's crop drawn on it: an
   outline, a light dim outside it, and a label, "Your crop" for a saved crop or "Automatic" for
   the frame's own choice.
2. **Owner:** an Edit crop button opens a box editor on the same whole-picture view: a 5:3 box
   with corner handles that moves and resizes over the fixed picture. Save, Cancel, Automatic
   (removes the saved crop). A preview shows the 800x480 result in the panel's real colors.
3. **Owner:** one crop per picture, used by every frame. Ente pictures can be cropped too; the
   crop is a note on the hub, never an edit to the picture.
4. **Owner:** a portrait picture with a saved crop joins rotation. Today that matters only for
   non-Ente portraits (Ente portraits already rotate); uncropped non-Ente portraits stay off, as
   today, and their viewer label says "Automatic · not on the frames until cropped".
5. **Owner:** gallery thumbnails show the frame's view (the crop applied).
6. **Owner:** if a frame is showing the picture when its crop is saved, that frame redraws with the
   new crop right away.
7. **Owner:** rename the viewer's two frame buttons so they say what they do and which frame:
   "Show now on <frame>" (rotation carries on) and "Pin to <frame>" (stays until unpinned), plus a
   one-line hint under them.
8. **Owner:** choose the frame from the viewer. **Proposed:** a small frame picker beside the
   buttons, shared with the Frame tab's selection (changing either changes both).

## Non-goals

- Editing the image file (rotate, filters, exposure). The crop is metadata only.
- Different crops per frame. Both frames are 800x480; a future frame of another shape reuses the
  same box (see Design) and can get its own crops later if needed.
- A crop editor on the 800x480 wall panel. **Proposed:** the editor is for phone and desktop;
  the panel's viewer shows the overlay but no Edit crop button (too small to drag handles well).
- Changes to how the frame picks pieces, intervals or folders (that is `per-frame-gallery`).

## Anti-goals (a failure even if it works)

- A saved crop that the frame ignores, or a frame that shows a different crop from the preview.
- Losing a crop when a picture is moved to another folder, trashed and restored, or when Ente
  re-syncs.
- Any write to an Ente picture or album.
- Personal photos, names or addresses in the public repos (tests use generated images).
- Breaking the frame when a crop is missing, malformed or out of range: it falls back to
  `detail_crop`.

## Constraints

- Frames: Inky Impression 800x480, six colors; render path `screens/art.py` → `detail_crop` →
  `palette.dither_to_panel`. The page preview uses `simulate_panel` for the panel look.
- Pieces and ids come from `art-index.json` (built by `lib/snaphouse/artlib.py`). Ente pieces are
  `ente-<album>/<file_id>.jpg` and read-only (`ENTE_READONLY_MESSAGE`). Moves change a piece's
  id (the page follows with `movedPieceId`).
- Rotation eligibility lives on the frame (`ArtLibrary.eligible`, `min_aspect`, Ente exempt) and
  is mirrored in the index's `landscape` flag on the hub.
- Writes go through the art admin service (`/api/art/...`, `X-Snaphouse` header guard).
- The library is also editable over SMB; a rename done there is outside the API (see Design).

## Approaches

**A. A crop box stored on the hub; frames and thumbnails apply it (Owner picked).** One rectangle
per picture, as fractions of the image, published with the piece in `art-index.json`. The frame
cuts that box instead of guessing; the page draws it. Small change on the frame, and it still
works for a frame of another size later.

**B. The hub renders a cropped 800x480 copy per picture.** The frames barely change, but the hub
stores and regenerates a copy per picture, tied to today's screen. Not picked.

Rejected: tap-to-center (a focal point only). Simpler, but it cannot zoom, so a portrait photo
could only slide a full-width strip up or down.

**Owner:** of the two editor mockups, the box editor (picture fixed, box moves and resizes), not
the move-the-photo editor. The approved look is the mockup at
`mockups/frame-crop/` (outside git), default variant `?crop=box`, with the viewer overlay.

## Design

### The crop value

`crop = {"x": float, "y": float, "w": float, "h": float}`: the box's left, top, width and height
as fractions of the image's width and height (0 to 1). The box's pixel aspect is the frame's 5:3,
so `w * image_width / (h * image_height)` equals 800/480 within rounding. The hub validates on
save: all in [0, 1], `x + w <= 1`, `y + h <= 1`, a minimum size (**Builder's call**, for example
the box covers at least 400 px of the image's width so the frame is not upscaling a tiny region
badly), aspect within 1% of 5:3 (the hub snaps `h` to the exact aspect).

### Storage on the hub

**Proposed:** one `crops.json` in the art library's state directory, keyed by piece id, written
atomically under the admin lock. The library code updates the key when a piece moves (new id),
keeps it through trash and restore, and drops it when the piece is purged from trash. Ente crops
are keyed by Ente id and survive re-syncs; a crop whose picture no longer exists is pruned on the
maintenance pass. A rename over SMB loses the crop (the old id no longer exists); documented, not
fixed. **Builder's call:** a sidecar file per image instead, if it keeps moves simpler; the Ente
cache must still not be written.

### API

- `POST /api/art/crop` with `{"id": "<piece id>", "crop": {x, y, w, h}}` saves; with
  `"crop": null` removes ("Automatic"). Same header guard as the other art writes. 400 with a
  short message on a bad value; 404 for an unknown id.
- After a save the service republishes the index (and wakes the thumbnail pass).

### Index

Each piece in `art-index.json` gains `"crop": {x, y, w, h}` or `null`. The `landscape` flag
becomes true for a piece with a crop, so the hub's view of eligibility matches the frame's.
Contract entry updated.

### Frame (nightstand)

- `sync_index` already mirrors the pieces; keep each piece's crop beside its file (**Builder's
  call** on the local format; a small JSON map in the art dir or the state dir).
- `screens/art.py`: with a valid crop, cut that box from the image and resize to 800x480 (Lanczos)
  before dithering; otherwise `detail_crop` as today.
- `ArtLibrary.eligible`: a piece with a crop is eligible whatever its aspect.
- Redraw: when the shown piece's crop changes (compare the synced crop with the one it was drawn
  with), redraw on the next cycle, the way commands redraw now, including during quiet hours as
  commands do today.

### Page (snaphouse)

- **Viewer overlay:** the viewer shows the whole picture (contain, not cover), with the box (`crop`, else `auto_crop`)
  outline, a light dim outside it, and the label. The automatic box is the index's `auto_crop`
  (settled item 5), so the page and the frame agree by construction.
- **Box editor:** opens from Edit crop on the same view (no jump); corner handles move and resize
  with the aspect locked; drag inside to move; works with touch and mouse at 390 px and desktop
  widths; Save, Cancel, Automatic; preview image in panel colors from a hub endpoint or the page
  (**Builder's call**; the mockup ran nightstand's `dither_to_panel` + `simulate_panel`).
  Not shown on the wall panel.
- **Thumbnails:** the grid shows the crop applied (CSS from the fractions over the existing
  thumbnail, or a cropped thumbnail from the hub; **Builder's call**).
- **Buttons:** "Show now on <frame name>" and "Pin to <frame name>", with a hint line, for example
  "Rotation carries on" / "Stays until you unpin it". Behavior unchanged.
- **Frame picker:** beside the buttons; the list of frames from the frame control document; it
  reads and writes the same selection as the Frame tab.
- Ente pictures: Edit crop works (no "change it in Ente" message for crops).

### Tests

- Hub: crop validation, save, remove, move keeps the crop, trash and restore keep it, purge drops
  it, Ente re-sync keeps it, index carries it, `landscape` true with a crop.
- Frame: crop applied gives exactly the box (golden image on a generated picture), bad crop falls
  back, eligibility with a crop, redraw on crop change.
- Page: model tests for overlay geometry, aspect lock and clamping, label choice, button labels
  and picker state.

## Settled after the builder check

1. **"Rotate" means the rotation schedule**, never turning the image. The crop box is always a 5:3
   landscape region of the upright picture.
2. **Pixel grid:** fractions are relative to the upright image (after `ImageOps.exif_transpose`),
   the grid the hub already measures. The frame applies `exif_transpose` before cropping, so an
   SMB file that still carries an orientation tag crops the same on both sides.
3. **Eligibility:** a piece with a saved crop is eligible on the frame whatever its aspect and
   whatever `min_aspect` is set to. The hub's `landscape` flag mirrors that for the page and is
   informational; the frame decides from the synced crop.
4. **Folder switches win:** a crop never puts a piece into rotation when its folder's rotation
   switch is off. Ente pieces stay exempt as today; the crop only changes which part shows.
5. **One algorithm for the automatic box, on the hub.** **Proposed:** move the box computation of
   `detail_crop` (the window position, not the dither) into snaphouse, run it on the thumbnail
   pass, and publish each piece's `auto_crop` in the index beside `crop`. The frame then always
   cuts the box the hub publishes (`crop` if set, else `auto_crop`) and keeps its own
   `detail_crop` only as the fallback for a piece with neither. The page draws the same box, so
   the viewer, the preview and the frame always agree. A golden test on generated images pins the
   moved algorithm to today's frame output before nightstand stops computing it.
6. **Redraw:** the frame records the box it drew (in its state directory). When the shown piece's
   effective box changes (a crop saved, edited or removed), it redraws as soon as it sees the new
   index, on the same fast path commands use, pinned or not and in quiet hours too. Removing a crop
   redraws with the automatic box.
7. **Ids:** an Ente picture keeps its id across re-syncs; if Ente gives a picture a new file id it
   is a new picture and starts automatic. A move through the API moves the file first, then
   re-keys `crops.json` under the same lock; a failed move leaves the crop on the old id.
8. **Deploy order, no version bump:** `crop` and `auto_crop` are additive fields that older frames
   ignore. The hub PR lands and deploys first, then nightstand, then the page. The contract entry
   in `docs/cross-repo-features.md` and nightstand's docs change in the same PR set.
9. **Show button:** today Show on the frame is offered for landscape pieces and Ente pieces only.
   A piece with a saved crop gets it too (and Pin), since the frame can now show it well.

## Left to the builder

- Minimum crop size, rounding, the storage form, how the automatic box reaches the page, how the
  preview and thumbnails are produced (each marked above).
- PR split. Likely: (1) hub storage, API and index; (2) nightstand apply, eligibility and redraw;
  (3) page overlay, editor, thumbnails, buttons and picker, built against the approved mockup.
