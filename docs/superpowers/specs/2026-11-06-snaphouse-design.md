# snaphouse: design

Status: draft for owner review, 2026-11-06.
Companion repo: `nightstand` (the bedside e-ink frame). The seams between the two are in
`docs/cross-repo-features.md`.

## What this is and why

Three rooms play the same song in sync. Anyone in the house starts music from their own
device, picks which rooms play from a web page, and walks between rooms without hearing
an echo.

The repo is the whole system written down. When an SD card dies, the owner flashes a new
one, runs one command, and that room comes back configured with no hand steps. After
bootstrap, every machine keeps itself current: it checks this repo on a timer and applies
whatever `main` says. Changing the system means merging a change, nothing more.

The repo is public on GitHub (`doyled-it/snaphouse`).

## The house

| Room | Machine | Hostname | Plays through |
|---|---|---|---|
| Living room | ZimaBlade (Intel, wired to router) | `zimablade` | Vizio soundbar + sub |
| Office | Raspberry Pi 5, 1 GB, official case + fan | `pi-office` | aux-only speakers via USB audio adapter (Adafruit #1475) |
| Bedroom | Raspberry Pi 5, 1 GB, mounted behind the e-ink frame | `pi-bedroom` | JBL Bluetooth speaker, which is only on sometimes |

The ZimaBlade is also the hub: it receives music from phones and computers, runs
`snapserver`, and hosts the room-picker page. It has two USB Bluetooth dongles (CSR8510)
on a powered mini hub: dongle 1 receives from Android, dongle 2 sends to the Vizio.

Hostnames are my call. The handoff's `pi-jbl` / `pi-aux` names no longer fit, because the
frame moved to the JBL room.

## Who sends music

| Sender | App | Path into the house |
|---|---|---|
| Owner's Android phone | Apple Music | Bluetooth A2DP to dongle 1 on the hub |
| Partner's iPhone | Apple Music | AirPlay to the hub |
| Owner's Mac mini, MacBook | Apple Music | AirPlay to the hub |

The hub shows up as one AirPlay speaker and one Bluetooth speaker, both named "House".
Apple devices use AirPlay because it needs no pairing, sounds better, and carries album
art. Android has no AirPlay, so it uses Bluetooth, which carries track title, artist,
and album (AVRCP) but no art.

Snapserver gets both as separate sources and plays whichever is active, using a meta
stream (Snapcast's priority/merge stream type) in front of them. Nobody switches inputs by
hand. If both send at once, the earlier one keeps playing; the builder may pick a different
tie-break if Snapcast's meta stream makes another one natural.

## Architecture

```
Android ── Bluetooth A2DP ──► dongle 1 ─► BlueALSA ─┐
iPhone / Macs ── AirPlay ──► shairport-sync ────────┤
                                                     ▼
                                 snapserver on zimablade (meta stream "House")
                                   │   JSON-RPC + HTTP on the LAN
          ┌────────────────────────┼─────────────────────────────┐
          ▼                        ▼                             ▼
  snapclient (local)       snapclient on pi-office       snapclient on pi-bedroom
  ─► dongle 2 Bluetooth    ─► USB audio adapter          ─► Bluetooth ─► JBL
  ─► Vizio soundbar        ─► aux speakers
```

Snapserver timestamps the audio and every client plays it at the same instant. Each
room's speaker adds its own delay (Bluetooth adds the most), and a per-room latency offset
cancels it. Tuning those offsets is what makes three rooms sound like one.

## Goals

1. Synced playback in all three rooms from any of the four sender devices.
2. A room-picker web page on the hub: one big on/off toggle and a volume slider per room.
   It works in a phone or desktop browser and can be saved to an Android or iPhone home
   screen. LAN only.
3. Track metadata (title, artist, album, and album art when the source sends it) published
   through snapserver's API, for the `nightstand` frame and the room-picker page.
4. Rebuild any machine from a fresh SD card (or fresh OS install, for the hub) with one
   bootstrap command, plus the one pairing step that needs a human.
5. Every machine updates itself from `main` on a timer. A bad change is undone by
   reverting it on `main`.
6. Survives reboots and power cuts with no manual steps, including the bedroom JBL being
   switched off and on.
7. Per-room latency offsets live in the repo, so a rebuilt machine keeps its tuning.
8. A shared art folder on the hub that `nightstand` reads from (see the cross-repo doc).

## Non-goals (this version)

- A shared play queue between household members. Today the queue belongs to whichever
  device is playing. An Apple Music collaborative playlist covers most of the need. A real
  shared queue needs a server-side Apple Music player on the hub, which is a separate
  research item.
- Control from outside the house.
- Home Assistant. It may land on the hub later; nothing here should make that harder.
- Other services (Spotify Connect, Chromecast, Plex).
- Proton Drive as an art source. Its Linux options (rclone's reverse-engineered backend,
  possibly an official Proton CLI) were unreliable or unverified as of November 2026.

## Anti-goals (would count as failure even if the audio works)

- Anything in the public repo that identifies the household: Wi-Fi credentials, Bluetooth
  MAC addresses, IP addresses, usernames, real names, home location. The repo ships
  examples of local files, never the real ones.
- Any opening from the internet into the home network: no tunnels, no port forwards, no
  cloud-hosted control page.
- Telemetry or third-party calls beyond what the machines need: GitHub for updates, the OS
  package mirrors, and nothing else.
- Bluetooth that stays open to pairing. Pairing happens only inside a short window the
  owner opens on purpose (see Pairing).
- An update that can silently take the house offline with no easy way back.

## Constraints

- The ZimaBlade's OS is unknown and has to be checked first (see Build order). CasaOS is
  Debian-based, so it can be kept and configured directly. ZimaOS is read-only and
  container-only, so the plan is to replace it with plain Debian stable, but only after
  the owner confirms, because that wipes the disk.
- The Pis run current Raspberry Pi OS Lite 64-bit, flashed with Raspberry Pi Imager with
  hostname, user, SSH key, and 5 GHz Wi-Fi preset. They stay off 2.4 GHz, which
  interferes with Bluetooth audio.
- 1 GB of RAM per Pi. Heavy services belong on the hub.
- The owner is privacy-conscious and prefers small, correct, reliable changes over clever
  ones. Each step is verified before the next is built on it.
- Personal or identifying information never goes into web requests, request headers, or
  search queries, including requests made by agents working on this repo.

## How it's built (decided)

### Configuration management: Ansible in pull mode

Each machine runs `ansible-pull` against this repo from a systemd timer, every 15 minutes
by default. There is no control machine. A machine's hostname picks its playbook or role
set: `zimablade` is the hub and living-room client, `pi-office` is a wired client, and
`pi-bedroom` is a Bluetooth client plus the `nightstand` host.

### Local, untracked settings

Everything identifying or machine-specific lives in one untracked file per machine,
written at bootstrap (path is the builder's call, for example `/etc/snaphouse/local.yml`).
The repo ships a commented `local.example.yml` showing its shape. Expected contents: the
Bluetooth MACs of paired speakers (JBL, Vizio), the hub's address if mDNS is unavailable,
and anything else that would identify the house.

### Bootstrap

From a freshly flashed Pi (or a fresh hub OS) the owner SSHes in, clones the public repo,
and runs one script. It installs Ansible, asks for or writes the local settings file, runs
the first apply, and turns on the update timer. From then on the machine runs itself.

### Pairing

Pairing needs a human to put a device into pairing mode, so it stays a manual command, for
example `snaphouse pair`. Inbound pairing (an Android phone pairing to the hub) works by
opening a short window, about two minutes, during which the hub is discoverable and
auto-accepts. Outside that window it is neither. Outbound pairing (hub to Vizio,
`pi-bedroom` to JBL) scans, pairs, trusts, and records the speaker's MAC in the local
settings file. The handoff called for an always-on auto-accept agent; the window is my
call, for security.

### Bluetooth speakers that come and go

The JBL is off most of the time. `pi-bedroom` must reconnect automatically whenever the
JBL powers on, and must not log errors in a loop or wedge snapclient while it is off. The
same applies to the Vizio if it drops the link.

### Room picker

A small static page served from the hub on the LAN. It talks to snapserver's JSON-RPC API
directly. Per room it shows an on/off toggle (mute or unmute that client) and a volume
slider, and it shows what's playing. It has no login. Anyone on the home network can use
it, which is acceptable here. Snapweb (snapserver's built-in UI) stays available for
latency tuning.

### Latency offsets

The repo holds each room's latency offset. Ansible applies it to the matching client. The
tuning loop: adjust in Snapweb while walking the house, then copy the final numbers into
the repo. A rebuilt machine gets them back.

### Art share

The hub exposes one folder two ways on the LAN: a writable SMB share, so the owner and
partner can drop images in from a Mac or phone, and a read-only path that `pi-bedroom`
pulls from. The pull mechanism (an rsync daemon module, HTTP, or NFS) is the builder's
call; it must be read-only and need no credentials stored in the repo.

### Self-update safety

- `main` is protected. Changes land by PR, and CI must pass first.
- CI runs `ansible-lint`, a syntax check of every playbook, and tests of the roles that can
  run in a container (packages, config files, service units). Bluetooth and audio
  hardware can't be tested in CI. They are covered by an on-device check script (next
  bullet).
- Each machine has a `snaphouse verify` command that checks its services are running and
  its audio path is wired. The update timer runs it after each apply and logs the result.
- Rollback is `git revert` on `main`. Machines pick it up on the next timer tick.

### Living room output: Bluetooth to the Vizio, unless optical is free

The plan of record is Bluetooth from dongle 2, because the Vizio's aux input is in use.
If the soundbar has a free optical (TOSLINK) or HDMI ARC input, a cheap USB-to-optical
adapter is tighter and more reliable, and it frees dongle 2. This is checked on site
during the build. The role should treat the output device as a setting so either path
works.

## Build order

Each step is verified before the next. Steps marked (owner) need the owner present.

1. Find the hub on the LAN, log in (credentials are in the owner's password manager), and
   find out which OS it runs. If it's ZimaOS, stop and get the owner's go-ahead to
   install Debian. If it's CasaOS, list the Docker apps and get the owner's OK before
   removing any.
2. Scaffold the repo: Ansible layout, bootstrap script, CI, local settings example,
   `verify` command.
3. Hub input: BlueALSA sink on dongle 1, shairport-sync, and snapserver with the meta
   stream. Pair the owner's phone (owner). Confirm that audio from Android and from an
   Apple device each reaches snapserver, and that track metadata shows in
   `Server.GetStatus`.
4. Hub output: local snapclient to the Vizio, over Bluetooth on dongle 2 or optical,
   whichever was chosen. First room working.
5. Flash both Pis (owner, or with the owner's Imager settings). Bootstrap `pi-office`:
   remove the old Bluetooth receiver from the speakers, wire the USB adapter to aux, start
   snapclient. Second room.
6. Bootstrap `pi-bedroom`: pair the JBL (owner), start snapclient over Bluetooth, and
   confirm it recovers when the JBL is switched off and on. Third room.
7. Room-picker page.
8. Tune latency in Snapweb (owner walks the house), then commit the offsets.
9. Art share on the hub.
10. Turn on self-update on all three machines. Reboot each one and confirm it comes back
    with no manual steps. Make a trivial change on `main` and watch it roll out.

The physical frame assembly and the display software belong to `nightstand`. They can
start in parallel with step 5.

## Left to the builder

- Ansible layout (roles, inventory-by-hostname mechanics, variable files).
- The exact snapserver stream config, including whether shairport-sync runs under
  snapserver's own AirPlay source or standalone into a pipe, as long as AirPlay metadata
  and cover art reach the stream's properties.
- How AVRCP metadata from BlueZ gets into snapserver's stream properties (a stream control
  script is the likely route).
- Room-picker technology, as long as it stays a small static page with no build step that
  needs a server-side runtime.
- Art share mechanics, within the rules above.
- Update timer interval, within reason.
- Test tooling for roles.

## Open items checked during the build

- ZimaBlade OS (step 1).
- Whether the Vizio has a free optical or ARC input (step 4).
- Whether the installed snapserver version supports the meta stream, AirPlay metadata, and
  stream control scripts as described. If not, pick the closest working version from the
  Snapcast project and record the choice.
