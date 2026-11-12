# snaphouse

Synced whole-house audio on Snapcast: a ZimaBlade hub and two Raspberry Pis that play the same music in three rooms, configured by Ansible and kept current from this repo.

The design is in `docs/superpowers/specs/2026-11-06-snaphouse-design.md`.

## Bootstrapping a machine

On a freshly flashed machine, clone this repo and run `sudo ./bootstrap.sh`. It
installs the tools, writes the local settings file, runs the first apply, and
turns on the self-update timer.

A machine selects its config by hostname, so it must already be named after its
inventory key in `vars/hosts.yml` (`house`, `office`, or `nightstand`). When you
repurpose a machine that still carries another name (the ZimaBlade shipped as
`casaos`), name it during bootstrap:

```
sudo ./bootstrap.sh --host office
```

`--host` validates the name against `vars/hosts.yml`, then sets the hostname
(and the `127.0.1.1` line in `/etc/hosts`) before the first apply. It is
idempotent: run it again and a matching hostname is left untouched. Without
`--host`, the current hostname is used unchanged.

## The room picker

The hub serves a room-picker page on the LAN: one on/off toggle and a volume
slider per room, what is playing, and a per-machine health line. It also has a
Frame screen that mirrors the piece a frame is showing and lists what came before
it. With more than one frame (the nightstand and the office), a switcher at the
top of the Frame screen chooses which frame to look at and control; a frame whose
mirror is missing (offline, or not yet installed) is marked, and the other frame
is unaffected. Its Back, Next, pin, and art history all act on the chosen frame,
and Settings carries a per-frame quiet-hours section alongside the shared
settings. The hub republishes each frame's art history same-origin, so a frame's
section appears only once it has shown something. On the 7-inch touch panel the
same page lays out in two columns in landscape (now playing beside the rooms),
chosen by a media query. Open it in any browser on the home network.

Open it at `http://house.local/`. On Android this works once the browser has the
local network permission (see below); if a phone or app still cannot resolve
`.local` names, use the hub's IP address instead, and give the hub a fixed address
with a DHCP reservation on the router so that bookmark keeps working. The address
is the household's, so it is not written down in this repo. The
page itself fetches only same-origin paths, so once it loads it needs no further
name resolution from the phone: the hub polls every machine for the health line.

If the page will not load on a phone even by IP address ("Unable to connect"),
check these, in this order:

- **Android's per-app local network permission.** Recent Android versions block
  apps, browsers included, from reaching devices on the home network until
  allowed: Settings, Apps, the browser, Permissions, then "Nearby devices" or
  "Local network", set to Allow.
- **A VPN with a kill switch.** It can block local traffic. Turn on the VPN
  app's "allow LAN connections" option (and reconnect the VPN), or make sure
  Android's own "Block connections without VPN" is off. The same applies to a
  laptop: if nothing on the home network answers, check its VPN first.
- **HTTPS-only browser modes.** The page is plain `http` on the LAN. Type the
  `http://` address explicitly and accept the browser's "not secure" prompt once.

## The control panel

One of the machines is a wall or counter control panel: a Raspberry Pi 3 with the
official 7 inch touch display, running the room picker full screen so you can
toggle rooms and set volume without a phone. It boots straight into the picker.
There is no login and no desktop: a single-app compositor (cage) runs Chromium in
kiosk mode on the hub's page, and the service restarts it if it ever exits. After
a power cut the panel comes back on its own, waits for the hub to answer before
opening the page (so it never flashes an error), and shows the picker once the hub
is up, even if that takes a moment.

The screen dims during the quiet hours and wakes to full on a touch, returning to
the low level after a minute idle; during the day it stays full. The backlight
levels, the idle timeout, and the quiet-hours window are in `vars/all.yml`
(`panel_backlight`). The tap that wakes a dimmed screen also reaches Chromium and
can toggle a room, so confirm during bring-up whether the first touch should wake
only; today it does both.

The hub address the panel opens is the one piece of per-home config it needs, and
like every other address it stays out of the repo: set `panel_url` in the
machine's untracked `/etc/snaphouse/local.yml` (see `local.example.yml`). Left
unset, the panel uses the hub's `http://house.local/` mDNS name; set it to the
hub's reserved IP if `.local` is not usable at boot on this network. If the
display is mounted turned, set `panel_rotation` (0, 90, 180, or 270) for that host
in `vars/hosts.yml`; it rotates the picture and the touch together, so a tap lands
where it looks.

## Bluetooth

The hub receives audio from a phone over Bluetooth through a long-range Realtek USB adapter
(an RTL8761BU, TP-Link UB500 Plus class) on a short USB extension cable. It was chosen for range
to the balcony: the extension lets the adapter sit clear of the hub's case and cabling. The
`bluetooth` role installs `firmware-realtek` (from Debian's `non-free-firmware` component,
which it enables in the apt sources if needed) wherever a receive adapter is configured;
without that firmware the adapter stays down.

To swap adapters, plug in the new one, set `bt_receive_adapter` in the untracked
`/etc/snaphouse/local.yml` to its address (see `local.example.yml`), let the next update apply,
then run `snaphouse pair phone` to pair the phone to it. A different chipset may need a
different firmware package.

## Now playing

The hub runs a small service, `snaphouse-nowplaying`, that is the single source of
now-playing data for the whole house. It follows the House stream through snapserver
and publishes two files the room-picker page and the bedside frame both read:
`now-playing.json` (the current track, with a ticking progress bar driven by a position
anchor) and `play-history.json` (the last 50 plays). For a track that arrives without
cover art, such as from Bluetooth, it looks one up from public catalogues (Apple's podcast
search, Open Library, MusicBrainz and the Cover Art Archive) in the background, carrying
only the track, show, book, or artist names. Both files are served same-origin on the LAN,
so the page and the frame never resolve another machine's name to show what is playing.

## Managing the art

The bedside frame rotates through an art library the hub keeps at `/srv/snaphouse/art`,
shared over SMB so you can drop images in from a computer. A second hub service,
`snaphouse-artadmin`, lets you manage that library and the rotation from the room-picker page
instead: upload photos or artwork, sort them into folders and choose which folders rotate,
delete a piece (recoverable from a trash folder for 30 days), set how often the frame changes,
and skip to the next piece, go back, or show a chosen one now. It runs on loopback behind
nginx, every change needs a request only the page can make, and uploads are stripped of all
metadata (so a photo's location never leaves your phone). The service publishes a library
index and a small control file the page and the frame both read, kept current within a minute
even when you add files over the share directly.

## Part of a multi-repo system

`snaphouse` is one of several repos. A feature usually spans repos; before calling
one done, cover its full cross-repo surface (API contract, config/env, deploy,
docs). Start cross-repo work from the hub repo with the siblings cloned. See the
hub's `docs/cross-repo-features.md` and `CONTRIBUTING.md`.
