"""Per-panel screen settings, stored on the hub.

Pure validation and storage, used by the art admin service (the same shape as
``framecontrol.py``). One settings object per panel id; the repo's vars supply the
defaults, so a panel nobody has touched carries them. Stdlib only.

Keys inside a panel's entry:

  brightness_pct      20 to 100, steps of 5
  night_dim_pct       0 to 100, steps of 5
  night_start         "HH:MM", 30 minute steps
  night_end           "HH:MM", 30 minute steps, never equal to night_start
  screensaver_on      bool
  screensaver_wait_s  30, 60, 120, 300 or 600
  screensaver_styles  non-empty list of style ids, registry order, no duplicates
  screensaver_change_min  0 (off), 5, 15, 30 or 60: minutes between styles
  screensaver_pct     5 to 100, steps of 5
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from http import HTTPStatus
from pathlib import Path

VERSION = 1
WAIT_CHOICES = (30, 60, 120, 300, 600)
CHANGE_CHOICES = (0, 5, 15, 30, 60)
# The fixed style ids, in the page's registry order. The hub accepts all twelve from
# the start; the page shows only the ones it has a module for.
STYLES = (
    "aurora", "facts", "quotes", "covers", "clock", "sky",
    "planets", "moon", "flow", "contours", "life", "harmonograph",
)
DEFAULT_STYLES = ["aurora"]
KEYS = (
    "brightness_pct",
    "night_dim_pct",
    "night_start",
    "night_end",
    "screensaver_on",
    "screensaver_wait_s",
    "screensaver_styles",
    "screensaver_change_min",
    "screensaver_pct",
)


class PanelError(Exception):
    """A refused request, with the status and the sentence the page shows."""

    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _is_int(value) -> bool:
    return type(value) is int


def _pct(low: int):
    def check(value) -> bool:
        return _is_int(value) and low <= value <= 100 and value % 5 == 0

    return check


def _styles(value) -> bool:
    if type(value) is not list or not value:
        return False
    if not all(type(v) is str and v in STYLES for v in value):
        return False
    order = [STYLES.index(v) for v in value]
    return all(a < b for a, b in zip(order, order[1:]))


def _time(value) -> bool:
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        return False
    hour, minute = value[:2], value[3:]
    if not (hour.isdigit() and minute.isdigit()):
        return False
    return int(hour) < 24 and int(minute) in (0, 30)


_CHECKS = {
    "brightness_pct": _pct(20),
    "night_dim_pct": _pct(0),
    "night_start": _time,
    "night_end": _time,
    "screensaver_on": lambda v: type(v) is bool,
    "screensaver_wait_s": lambda v: _is_int(v) and v in WAIT_CHOICES,
    "screensaver_styles": _styles,
    "screensaver_change_min": lambda v: _is_int(v) and v in CHANGE_CHOICES,
    "screensaver_pct": _pct(5),
}

_BAD = {
    "brightness_pct": "Brightness must be 20 to 100 in steps of 5.",
    "night_dim_pct": "Night dim must be 0 to 100 in steps of 5.",
    "night_start": "Night starts must be a time on the hour or half hour.",
    "night_end": "Night ends must be a time on the hour or half hour.",
    "screensaver_on": "Screensaver must be on or off.",
    "screensaver_wait_s": "Starts after must be 30 s, 1, 2, 5 or 10 min.",
    "screensaver_styles": "Pick at least one screensaver style from the list.",
    "screensaver_change_min": "Change every must be Off, 5, 15, 30 or 60 min.",
    "screensaver_pct": "Screensaver brightness must be 5 to 100 in steps of 5.",
}


def _bad(message: str) -> PanelError:
    return PanelError(HTTPStatus.BAD_REQUEST, message)


def validate_patch(patch) -> dict:
    """The patch, if every key and value is valid; else PanelError (400)."""
    if not isinstance(patch, dict) or not patch:
        raise _bad("Nothing to change.")
    for key, value in patch.items():
        if key not in _CHECKS:
            raise _bad("Unknown setting.")
        if not _CHECKS[key](value):
            raise _bad(_BAD[key])
    return dict(patch)


def _check_whole(settings: dict) -> None:
    if settings["night_start"] == settings["night_end"]:
        raise _bad("Night cannot start and end at the same time.")


class PanelSettings:
    """Settings for each known panel, persisted in one JSON file."""

    def __init__(self, state_path: Path, panels: list, defaults: dict) -> None:
        self._path = Path(state_path)
        self._panels = list(panels)
        try:
            self._defaults = validate_patch({k: defaults[k] for k in KEYS})
            _check_whole(self._defaults)
        except (KeyError, PanelError) as error:
            raise ValueError(f"bad panel defaults: {error}") from error
        self._lock = threading.Lock()
        self._stored = self._load()

    # -- storage ----------------------------------------------------------- #

    def _load(self) -> dict:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        out = {}
        for panel in self._panels:
            entry = raw.get(panel)
            if not isinstance(entry, dict):
                continue
            good = {k: v for k, v in entry.items() if k in _CHECKS and _CHECKS[k](v)}
            # Migration: the old single style reads as a one-item list.
            if "screensaver_styles" not in good and _CHECKS["screensaver_styles"]([entry.get("screensaver_style")]):
                good["screensaver_styles"] = [entry["screensaver_style"]]
            merged = {**self._defaults, **good}
            if merged["night_start"] == merged["night_end"]:
                good.pop("night_start", None)
                good.pop("night_end", None)
            if good:
                out[panel] = good
        return out

    def _save(self, stored: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".panel-settings-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(stored, handle, sort_keys=True)
                handle.write("\n")
            os.chmod(tmp, 0o644)
            os.replace(tmp, self._path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # -- reads ------------------------------------------------------------- #

    def require_panel(self, panel) -> str:
        if not isinstance(panel, str) or panel not in self._panels:
            raise PanelError(HTTPStatus.NOT_FOUND, "That panel is not known.")
        return panel

    def settings(self, panel: str) -> dict:
        with self._lock:
            return {**self._defaults, **self._stored.get(panel, {})}

    def document(self) -> dict:
        with self._lock:
            return {
                "version": VERSION,
                "panels": {
                    p: {**self._defaults, **self._stored.get(p, {})} for p in self._panels
                },
            }

    # -- writes ------------------------------------------------------------ #

    def update(self, panel, patch) -> dict:
        self.require_panel(panel)
        patch = validate_patch(patch)
        with self._lock:
            merged = {**self._defaults, **self._stored.get(panel, {}), **patch}
            _check_whole(merged)
            stored = {**self._stored, panel: {**self._stored.get(panel, {}), **patch}}
            self._save(stored)
            self._stored = stored
            return merged
