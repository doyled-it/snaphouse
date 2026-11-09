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
slider per room, what is playing, and a per-machine health line. It also has an
"On the frame" section showing the piece currently on the nightstand frame and a
"Recently shown" list of what came before it; the hub republishes the frame's
art history same-origin, so the section appears only when the frame has shown
something. Open it in any browser on the home network.

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

## Part of a multi-repo system

`snaphouse` is one of several repos. A feature usually spans repos; before calling
one done, cover its full cross-repo surface (API contract, config/env, deploy,
docs). Start cross-repo work from the hub repo with the siblings cloned. See the
hub's `docs/cross-repo-features.md` and `CONTRIBUTING.md`.
