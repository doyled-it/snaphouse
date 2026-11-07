# snaphouse

Synced whole-house audio on Snapcast: a ZimaBlade hub and two Raspberry Pis that play the same music in three rooms, configured by Ansible and kept current from this repo.

The design is in `docs/superpowers/specs/2026-11-06-snaphouse-design.md`.

## The room picker

The hub serves a room-picker page on the LAN: one on/off toggle and a volume
slider per room, what is playing, and a per-machine health line. Open it in any
browser on the home network.

Reach it by the hub's IP address, not `house.local`. Android Chrome often
cannot resolve `.local` mDNS names, so a phone may fail to load the page by name.
Give the hub a fixed address with a DHCP reservation on the router and open the
page at that address (and add it to the home screen there). The address is the
household's, so it is not written down in this repo; it lives with the router
and, if a role ever needs it, in the untracked `/etc/snaphouse/local.yml`. The
page itself fetches only same-origin paths, so once it loads it needs no further
name resolution from the phone: the hub polls every machine for the health line.

## Part of a multi-repo system

`snaphouse` is one of several repos. A feature usually spans repos; before calling
one done, cover its full cross-repo surface (API contract, config/env, deploy,
docs). Start cross-repo work from the hub repo with the siblings cloned. See the
hub's `docs/cross-repo-features.md` and `CONTRIBUTING.md`.
