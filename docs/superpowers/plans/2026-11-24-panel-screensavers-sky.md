# Panel screensavers PR 4: Night sky, Planets, Moon

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline) to implement this plan task by task.

**Goal:** Ship the `sky`, `planets` and `moon` screensaver styles.

**Architecture:** Pure model plus thin style on `canvas-style.js`, as for the generative styles. Models take an injected random and clock. Redraw rates are low (sky 5 fps, 24 during a shooting star; planets 1 fps; moon 2 fps and only when something changed).

**Tech Stack:** plain ES modules, `node --test`.

**Spec:** `docs/superpowers/specs/2026-11-24-panel-screensavers-design.md`

## Global Constraints

- No em dashes. No household location: local time of the panel only.
- Real speeds: stars 15 degrees an hour; planets in real time; moon drifts slowly.
- Night: sky black with fewer, dimmer stars and rarer shooting stars; planets names off and dim; moon dimmer photo and dim warm grey text.
- Only third-party image: one public-domain NASA moon image, credit kept in the repo.
- Loops stop completely while stopped.

## Review Focus

- Planet labels never overlap at any date (sweep test).
- Moon and its text never leave the screen while drifting (sweep test).
- Phase at published new and full moons within a day.
- `setNight` snaps after a stop (canvas-style fix).
- The moon image stays under 150 KB and its credit file exists.

## Tasks

1. `canvas-style.js`: `stop()` clears `started`, so `setNight` snaps (test in `canvas-style.test.js`).
2. `sky-model.js` (`SKY`, `rotationAt`, `makeStars`, `starPosition`, `createShooter`) then `sky-style.js`.
3. `planets-model.js` (`PLANETS`, `longitudeAt`, `ringLayout`, `placeLabels`, `dateLabel`) then `planets-style.js`.
4. `moon-model.js` (`phaseAt`, `litFraction`, `phaseName`, `nextEvent`, `nextText`, `terminatorU`, `shade`, `driftAt`) then `moon-style.js`, `moon.jpg`, `MOON-CREDIT.md`.
5. Registry, `SHIPPED_IDS`, tests for the lists, AGENTS.md, role copy check.
