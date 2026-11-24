"""Tests for the hub's per-panel screen settings (lib/snaphouse/panelsettings.py)."""

from __future__ import annotations

import json
from http import HTTPStatus

import pytest

from snaphouse.panelsettings import PanelError, PanelSettings

DEFAULTS = {
    "brightness_pct": 100,
    "night_dim_pct": 15,
    "night_start": "22:00",
    "night_end": "06:00",
    "screensaver_on": True,
    "screensaver_wait_s": 60,
    "screensaver_styles": ["aurora"],
    "screensaver_change_min": 15,
    "screensaver_pct": 40,
}


def _make(tmp_path, panels=("panel",)):
    return PanelSettings(tmp_path / "state" / "panel-settings.json", list(panels), DEFAULTS)


def test_document_carries_defaults_for_every_panel(tmp_path):
    ps = _make(tmp_path, ("panel", "hall"))
    assert ps.document() == {"version": 1, "panels": {"panel": DEFAULTS, "hall": DEFAULTS}}


def test_update_merges_only_given_keys_and_persists(tmp_path):
    ps = _make(tmp_path)
    ps.update("panel", {"brightness_pct": 70})
    again = _make(tmp_path)
    assert again.settings("panel") == {**DEFAULTS, "brightness_pct": 70}


def test_update_returns_full_settings(tmp_path):
    out = _make(tmp_path).update("panel", {"night_dim_pct": 0})
    assert out == {**DEFAULTS, "night_dim_pct": 0}


def test_settings_are_per_panel(tmp_path):
    ps = _make(tmp_path, ("panel", "hall"))
    ps.update("hall", {"brightness_pct": 50})
    assert ps.settings("panel")["brightness_pct"] == 100


def test_unknown_panel_is_404(tmp_path):
    with pytest.raises(PanelError) as err:
        _make(tmp_path).update("nope", {"brightness_pct": 70})
    assert err.value.status == HTTPStatus.NOT_FOUND


@pytest.mark.parametrize(
    "patch",
    [
        {"brightness_pct": 15},
        {"brightness_pct": 105},
        {"brightness_pct": 22},
        {"night_dim_pct": 7},
        {"night_dim_pct": -5},
        {"night_dim_pct": 105},
        {"screensaver_pct": 0},
        {"screensaver_pct": 3},
        {"screensaver_wait_s": 45},
    ],
)
def test_rejects_out_of_range_and_off_step_values(tmp_path, patch):
    ps = _make(tmp_path)
    with pytest.raises(PanelError) as err:
        ps.update("panel", patch)
    assert err.value.status == HTTPStatus.BAD_REQUEST
    assert ps.settings("panel") == DEFAULTS


@pytest.mark.parametrize(
    "patch",
    [
        {"brightness_pct": 70.0},
        {"brightness_pct": True},
        {"brightness_pct": "70"},
        {"brightness_pct": None},
        {"screensaver_on": 1},
        {"screensaver_on": "yes"},
        {"screensaver_wait_s": "60"},
    ],
)
def test_rejects_wrong_types(tmp_path, patch):
    with pytest.raises(PanelError):
        _make(tmp_path).update("panel", patch)


def test_rejects_unknown_key_and_empty_patch(tmp_path):
    ps = _make(tmp_path)
    with pytest.raises(PanelError):
        ps.update("panel", {"colour": "red"})
    with pytest.raises(PanelError):
        ps.update("panel", {})


@pytest.mark.parametrize("value", ["25:00", "22:15", "7:00", "22:00:00", 2200, "", "ab:cd"])
def test_rejects_bad_time_and_off_step_time(tmp_path, value):
    with pytest.raises(PanelError):
        _make(tmp_path).update("panel", {"night_start": value})


def test_rejects_equal_start_and_end_after_merge(tmp_path):
    ps = _make(tmp_path)
    with pytest.raises(PanelError) as err:
        ps.update("panel", {"night_start": "06:00"})
    assert err.value.status == HTTPStatus.BAD_REQUEST
    assert ps.settings("panel")["night_start"] == "22:00"


def test_start_and_end_may_both_change_in_one_patch(tmp_path):
    out = _make(tmp_path).update("panel", {"night_start": "06:00", "night_end": "07:30"})
    assert (out["night_start"], out["night_end"]) == ("06:00", "07:30")


def test_failed_write_stores_nothing(tmp_path):
    ps = _make(tmp_path)
    with pytest.raises(PanelError):
        ps.update("panel", {"brightness_pct": 70, "night_dim_pct": 7})
    assert not (tmp_path / "state" / "panel-settings.json").exists()
    assert ps.settings("panel") == DEFAULTS


def test_corrupt_state_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "state" / "panel-settings.json"
    path.parent.mkdir()
    path.write_text("{not json")
    assert _make(tmp_path).settings("panel") == DEFAULTS


def test_invalid_stored_values_fall_back_per_key(tmp_path):
    path = tmp_path / "state" / "panel-settings.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"panel": {"brightness_pct": 7, "night_dim_pct": 30}}))
    out = _make(tmp_path).settings("panel")
    assert out["brightness_pct"] == 100 and out["night_dim_pct"] == 30


def test_state_is_written_atomically(tmp_path):
    ps = _make(tmp_path)
    ps.update("panel", {"brightness_pct": 70})
    assert [p.name for p in (tmp_path / "state").iterdir()] == ["panel-settings.json"]


def test_stored_panel_not_in_inventory_is_ignored(tmp_path):
    path = tmp_path / "state" / "panel-settings.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"gone": {"brightness_pct": 70}}))
    assert set(_make(tmp_path).document()["panels"]) == {"panel"}


ALL_IDS = [
    "aurora", "facts", "quotes", "covers", "clock", "sky",
    "planets", "moon", "flow", "contours", "life", "harmonograph",
]


def test_styles_accept_any_ordered_subset_of_the_twelve(tmp_path):
    ps = _make(tmp_path)
    assert ps.update("panel", {"screensaver_styles": ["clock", "life"]})["screensaver_styles"] == ["clock", "life"]
    assert ps.update("panel", {"screensaver_styles": ALL_IDS})["screensaver_styles"] == ALL_IDS


@pytest.mark.parametrize(
    "value",
    [[], ["nope"], ["clock", "aurora"], ["aurora", "aurora"], "aurora", ("aurora",), [1], None, ["aurora", None]],
)
def test_bad_styles_store_nothing(tmp_path, value):
    ps = _make(tmp_path)
    with pytest.raises(PanelError) as err:
        ps.update("panel", {"screensaver_styles": value, "brightness_pct": 70})
    assert err.value.status == HTTPStatus.BAD_REQUEST
    assert ps.settings("panel") == DEFAULTS


@pytest.mark.parametrize("value", [0, 5, 15, 30, 60])
def test_change_min_accepts_the_choices(tmp_path, value):
    assert _make(tmp_path).update("panel", {"screensaver_change_min": value})["screensaver_change_min"] == value


@pytest.mark.parametrize("value", [1, 10, -5, 15.0, True, "15", None])
def test_change_min_rejects_everything_else(tmp_path, value):
    with pytest.raises(PanelError):
        _make(tmp_path).update("panel", {"screensaver_change_min": value})


def test_screensaver_style_is_no_longer_a_writable_key(tmp_path):
    with pytest.raises(PanelError):
        _make(tmp_path).update("panel", {"screensaver_style": "aurora"})


def test_defaults_are_aurora_and_fifteen_minutes(tmp_path):
    s = _make(tmp_path).settings("panel")
    assert s["screensaver_styles"] == ["aurora"]
    assert s["screensaver_change_min"] == 15
    assert "screensaver_style" not in s


def test_old_single_style_reads_as_a_one_item_list(tmp_path):
    path = tmp_path / "state" / "panel-settings.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"panel": {"screensaver_style": "aurora", "brightness_pct": 70}}))
    s = _make(tmp_path).settings("panel")
    assert s["screensaver_styles"] == ["aurora"]
    assert "screensaver_style" not in s
    assert s["brightness_pct"] == 70


def test_old_style_is_ignored_when_the_list_is_present(tmp_path):
    path = tmp_path / "state" / "panel-settings.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"panel": {"screensaver_style": "aurora", "screensaver_styles": ["clock"]}}))
    assert _make(tmp_path).settings("panel")["screensaver_styles"] == ["clock"]


def test_a_save_drops_the_old_style_key(tmp_path):
    path = tmp_path / "state" / "panel-settings.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"panel": {"screensaver_style": "aurora"}}))
    ps = _make(tmp_path)
    ps.update("panel", {"brightness_pct": 70})
    assert "screensaver_style" not in json.loads(path.read_text())["panel"]


def test_a_bad_default_fails_at_construction(tmp_path):
    with pytest.raises(ValueError):
        PanelSettings(tmp_path / "s.json", ["panel"], {**DEFAULTS, "brightness_pct": 3})
