# snaphouse

Synced whole-house audio on Snapcast: a ZimaBlade hub and two Raspberry Pis that play the same music in three rooms, configured by Ansible and kept current from this repo.

The design is in `docs/superpowers/specs/2026-11-06-snaphouse-design.md`.

## Part of a multi-repo system

`snaphouse` is one of several repos. A feature usually spans repos; before calling
one done, cover its full cross-repo surface (API contract, config/env, deploy,
docs). Start cross-repo work from the hub repo with the siblings cloned. See the
hub's `docs/cross-repo-features.md` and `CONTRIBUTING.md`.
