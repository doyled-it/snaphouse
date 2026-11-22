# Panel screensaver design

Date: 2026-11-22. Status: approved by the owner 2026-11-22.

## Intent

The control panel (host `panel`, 7 inch 800x480 touchscreen running the room
picker in a cage + Chromium kiosk) sits on a wall in rooms that are dark at
night. The owner wants it to show a slow, endless, gently moving visual when
nobody is using it. The main reason is that the panel should barely be there in
a dark room at night. The second reason is that, day or night, it should be
something pleasing to glance at instead of a static grid of room switches. The
owner said today's night dim (15 percent backlight) "didn't dim all that much".

## Goals

- When the panel has been idle, a full-screen moving visual covers the room
  picker: after about 5 minutes idle by day, and after 1 minute idle during the
  quiet hours (the same window and timeout the backlight already uses,
  `panel_backlight` in vars/all.yml: 22:00 to 06:00, `dim_timeout_s` 60).
- The visual is the "Aurora" style the owner picked on the panel from a
  throwaway demo on 2026-11-22: slow flowing ribbons of light low on the screen,
  fading up into black. Most of the screen stays near black.
- Colors follow the music: the two hues the page already derives from the
  now-playing cover (cover-color.js, `--h` and the second hue, chroma 0 when
  nothing is playing) tint the aurora, crossfading over a few seconds when the
  track changes. With nothing playing it uses the page's default hues.
- The first tap on the screensaver only dismisses it, day and night. It never
  reaches a room switch. The controls then behave normally until the next idle.
- At night the backlight drops much lower than today. The level is tuned by eye
  with the owner in a dark room; nothing is committed until the owner confirms.

## Non-goals

- No screensaver on phones or any browser other than the panel kiosk.
- No audio-reactive analysis (the panel has no audio input). "Visualizer" here
  means it moves and takes the music's colors.
- No clock, weather, or other information on the screensaver for now.
- No other styles or a style picker in the UI. The demo's Drift and Motes stay
  in the spike only.

## Anti-goals

- A tap that toggles a room while the screensaver is up, or a waking tap at
  night that needs two more taps before the controls work.
- Anything bright, fast, flashing, or busy at night.
- Making the room picker slower or heavier for phones.
- Burning the Pi 3 or Pi 5 at full CPU all night.

## Constraints

- Panel hardware: a Raspberry Pi 3 today, moving to the office Pi 5 soon (owner
  chose the swap on 2026-11-21). It must run acceptably on both. Measured on the
  Pi 3 with the demo: Aurora at 51 fps average, p95 frame 33 ms, about 40
  percent CPU; Drift at 60 fps.
- The room picker is shared with phones. Panel-only behavior must be switched on
  only by the panel.
- The backlight daemon (lib/snaphouse/backlight.py, snaphouse #184) currently
  holds an evdev grab while dim so the waking tap never reaches Chromium. With a
  page overlay that already swallows the first tap, that grab would make night
  wake take two dismissing taps.
- Household addresses stay out of the repo (panel_url comes from local.yml).
- The repo's rules: tests for the page modules (node-test) and the Python
  daemon (pytest), CI container apply, no em dashes.

## Approaches considered

1. Overlay inside the room-picker page (picked by the owner). The page fades a
   full-screen canvas over the controls when idle. It already knows the album
   hues, it is one renderer, and switching is instant. Costs: the page grows a
   panel-only mode and idle logic.
2. A separate screensaver page the kiosk navigates to and from. Simpler page
   code, but a navigation takes seconds on these boards with a blank flash, and
   the album colors would have to be fetched again.
3. A native renderer outside the browser, swapped in by the kiosk. Smoothest
   motion, but two programs fighting over one screen and a second rendering
   stack to maintain.

## Decided design

### Panel mode

- The kiosk opens the page with a query parameter that turns on panel mode, for
  example `?panel=1`, plus the timing it needs: day idle minutes, night idle
  seconds, and the quiet-hours window, all templated from `panel_backlight`
  (and a new `panel_screensaver` block for the day idle time) in vars/all.yml.
  The panel role appends these to `panel_url` in snaphouse-kiosk.env.j2, so the
  hub address still comes only from local.yml.
- Without the parameter (phones, other browsers) nothing changes: no overlay, no
  canvas, no timers.

### The overlay

- A full-screen layer above everything in the page, black, with a small
  offscreen canvas (about 200 px wide) scaled up with CSS, as in the demo.
  Canvas 2D, one requestAnimationFrame loop, capped at 30 fps (the motion is
  slow enough that 30 fps looks the same and halves the work).
- It appears with a slow fade (about 2 seconds) when the idle timer fires. The
  idle timer resets on any pointer or key event the page sees.
- While it is up, it takes every pointer event. The first pointerdown starts a
  quick fade out (about 300 ms) and is swallowed, including its pointerup and
  click, so nothing underneath reacts.
- The animation stops completely (no rAF) while the overlay is hidden and while
  the page is hidden.
- Hues: the overlay reads the same hue values the page applies from the cover,
  and crossfades to new ones over about 3 seconds.
- The Aurora renderer comes from the spike
  (/Users/mdoyle/projects/smart-home/screensaver-spike/demo.html, not in the
  repo) and is rewritten as a tested module in the site directory.

### Night, backlight and the waking tap

- The backlight daemon keeps the schedule: full by day, dim level at night after
  the idle timeout, full again on touch. It keeps watching the touchscreen
  without a grab when the screensaver is on.
- A new switch in `panel_backlight`, for example `grab_wake_tap`, turns the
  evdev grab off when the screensaver handles the first tap. The panel role sets
  it from whether the screensaver is enabled. The grab code from #184 stays for
  a panel without the screensaver.
- Because both use the same quiet hours and the same 60 second timeout, at night
  the overlay fades in as the backlight dims, and one tap both brightens the
  screen and dismisses the overlay.
- The night dim level (`dim_pct`, 15 today) is lowered after an on-panel session
  with the owner in a dark room, stepping the backlight down while Aurora runs.
  Values near the bottom of the range may switch the backlight off on this
  display, so the session also finds the lowest level that still shows the
  aurora.

## Calls made after the builder check

These were settled by level 1 (not by the owner) so the plan has no guesses.
The owner can overrule any of them.

- Night dim level ships separately. The screensaver and the grab switch land
  with `dim_pct` unchanged at 15. The dark-room tuning session happens on the
  live panel (stepping the sysfs backlight by hand while Aurora runs), and the
  confirmed number lands in its own one-line PR. No local.yml override.
- Quiet-hours edges: the page re-evaluates its rule once a minute, not only on
  idle. At 22:00 a panel already idle for 60 seconds or more shows the overlay
  at once (the backlight dims at the same moment). At 06:00 an overlay that is
  up stays up and simply runs at day brightness; the day rule only decides when
  it next appears. This matches today's backlight, which already returns to
  full at 06:00.
- Clock: the page uses the browser's local time, the same system clock the
  daemon reads, from `HH:MM` strings passed in the URL. Before NTP syncs both are
  wrong in the same way, so they still agree.
- What the overlay swallows: every pointer, touch and key event while it is up,
  including a touch's follow-up click. The idle timer resets on any input the
  page sees, so a drag in progress or a finger held down keeps the overlay away.
  Toasts, sheets and dialogs stay as they were under it.
- Degraded page: with the screensaver on, the daemon does not grab. If the page
  failed to load (the kiosk wait page or an error page) a night tap lands on
  that page, which has nothing to toggle. The grab stays for panels without the
  screensaver.
- CPU pass mark at the 30 fps cap with the overlay up: at most 20 percent total
  CPU on the Pi 3 and 10 percent on the Pi 5 (from `top`), else cut further
  (smaller canvas, or 15 fps at night).
- URL: the role builds the kiosk URL from `panel_url` plus the query, putting
  the query before any `#` fragment and merging with any query already there.
  A change to the kiosk env file restarts the kiosk (add a handler if the role
  lacks one), since the page reads the parameters once at load.

## Left to the builder

- Exact query parameter names and how the page parses them.
- How the aurora module is split for testing (pure math versus canvas drawing).
- Exact fade timings within the ranges above.
- Whether day idle is 5 minutes exactly or rounded to the timer granularity.

## Checks after it ships

- On the panel: the overlay appears after 5 minutes by day and 1 minute at
  night; the first tap only dismisses; the next tap works; a phone shows no
  overlay.
- Frame rate on the panel board with the overlay up stays at the 30 fps cap,
  with CPU well under the demo's 40 percent on the Pi 3.
- Night: one tap brightens and dismisses together.
