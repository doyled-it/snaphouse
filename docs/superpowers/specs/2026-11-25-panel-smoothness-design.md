# Panel smoothness and color

Date: 2026-11-25. Status: approved by the owner after a live before/after mockup.

## Why

The owner finds motion on the wall panel (800x480, 7 inch, Raspberry Pi 5) choppy, and wants
the generative screensavers more colorful. A measurement on the panel ruled out the hardware:
Chromium runs fully on the GPU (ANGLE on V3D 7.1, GPU compositing and rasterization on), and the
main thread holds 60 fps at idle and while switching screens, with no long tasks. The choppiness
is designed in: low redraw caps, a progress bar that steps once a second, coarse fade steps, and
hard cuts.

## Goals

1. Motion that glides: every visible movement on the panel runs at up to 60 fps.
2. More color in Aurora, Flow field, Contours, Game of Life and Harmonograph, and never grey.
3. Remove the Clock's black box.
4. Stay inside the screensaver CPU budget (about 25% of one core of Chromium on the panel), measured.

Non-goals: Planets, Moon and the star rotation in Night sky (they move under 0.3 px a second, so
redraw rate makes no visible difference; leave them as they are). Weird facts and Quotes (already
fade). Layout or type changes to the screens. Canvas resolution changes.

Anti-goals: a style that looks smoother but costs the panel more than the budget; colors so loud
the panel reads as a light at night (the night looks keep their dimming).

## Decisions

### Motion (approved in the mockup)

- **Progress bars** (the Now playing bar and the ambient view's line) glide between the once a
  second updates: a 1 s linear transition on the transform, reset without a transition when the
  track changes or the position jumps backwards or by more than a few seconds.
- **Switching screens** (Music, Climate, Garage, More and the screens under More): a 150 ms
  opacity crossfade. Taps must not wait for it.
- **Screensaver redraw caps:** Aurora 30 to 60; Flow field 30 to 60; Contours 12 to up to 60;
  Harmonograph 15 to up to 60; Game of Life 10 to 60; Night sky shooting stars 24 to 60; Album
  cover mosaic 60 while a cover crossfades (its idle scroll tick may stay low). Each new rate is
  measured on the panel; a style over budget steps down (45, then 30) before anything else.
- **Game of Life fades:** dying cells fade in 48 levels (today 6) and new cells fade in over
  about 0.35 s instead of appearing at full brightness. Generations still step on today's timing.
- **Clock:** remove the `background: #000` box from `.saver-clock` (style.css). The clock
  already glides to new spots over 6 s; keep that.

### Color (approved in the mockup: "Richer album colors")

- `tintColor` (aurora-math.js) gets the richer mode as the only mode for these five styles:
  chroma `0.19 * max(0.8, chromaScale(chroma))`, and the two album hues spread at least 110
  degrees apart (keeping the direction of the shorter way from h1 to h2; wider spreads stay as
  they are). Check where else `tintColor` is used before changing it in place; if anything else
  depends on today's output, add a separate function instead.
- Flow field's trails are faint, so they get brighter alpha with the new colors; tune by eye
  against the mockup and the panel screenshot.
- **Last album's colors persist:** when the track clears, the screensaver keeps the hues of the
  last cover it had (rooms.js `followCover` resets them to `DEFAULT_HUES` today). The page
  background tint (`applyCoverColor`) keeps today's behavior. With no album since the page
  loaded, the default hues (255 to 285, blue to violet) at the richer chroma.
- Night looks keep their lower lightness.

### Tools

- `grim` (Wayland screenshots) is installed on the panel by the `panel` role, so each change can be
  checked on the real screen: `sudo -u kiosk XDG_RUNTIME_DIR=/run/user/999 WAYLAND_DISPLAY=wayland-0 grim <file>`.

## Approaches considered

- Raise every redraw cap to 60: simplest, but costs CPU for styles whose motion is sub-pixel.
- Move slow drifts to CSS transforms on the compositor: the original idea, dropped once the
  measurement showed Planets, Moon and the star field move under a pixel a second.
- Chosen: raise caps only where motion is visible, measure each against the budget, and fix the
  non-frame-rate stepping (fade levels, progress bar, hard cuts) directly.

## Delivery

Three PRs, in order, each merged and deployed before the next:

1. Motion outside the screensaver: progress bar glide, screen crossfade, Clock box removed,
   `grim` in the panel role.
2. Screensaver redraw caps and Game of Life fades, with CPU measured per style.
3. Richer colors, brighter Flow trails, last album's colors kept, with CPU and a panel screenshot
   per style.

## Left to the builder

Exact easing curves, how the crossfade is wired into panel.js screen switching, the reset rule
for the progress glide, Flow's trail alpha, and the step-down rate for any style over budget.
