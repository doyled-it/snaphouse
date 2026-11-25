"""Tests for lib/snaphouse/framecrop.py: crop validation and the automatic box.

Pictures are generated here (no owner photos, nothing committed). The golden
test pins the hub's port of nightstand's ``detail_crop`` window choice to the
window the frame's own function cuts today.
"""

from __future__ import annotations

import math

import pytest
from PIL import Image, ImageDraw

from snaphouse import framecrop
from snaphouse.framecrop import CropError, auto_crop, auto_window, validate_crop

W, H = 2000, 1200  # 5:3 exactly


def _box(x, y, w, h_px_aspect=True, img=(W, H)):
    """A 5:3 box with the given x, y and width fraction."""
    iw, ih = img
    h = w * iw / ih / (800 / 480)
    return {"x": x, "y": y, "w": w, "h": h}


# --------------------------------------------------------------------------- #
# validate_crop
# --------------------------------------------------------------------------- #


def test_valid_box_is_returned_with_h_snapped_to_the_exact_aspect():
    crop = validate_crop({"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5004}, 2000, 1200)
    assert crop["x"] == 0.1 and crop["y"] == 0.1 and crop["w"] == 0.5
    # w*W / (h*H) == 800/480 exactly after the snap.
    assert math.isclose(crop["w"] * 2000 / (crop["h"] * 1200), 800 / 480, rel_tol=1e-6)


def test_full_image_box_on_a_5_3_picture_is_valid():
    crop = validate_crop({"x": 0, "y": 0, "w": 1, "h": 1}, 2000, 1200)
    assert crop == {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        "x",
        {},
        {"x": 0, "y": 0, "w": 1},
        {"x": 0, "y": 0, "w": 1, "h": 1, "z": 2},
        {"x": True, "y": 0, "w": 1, "h": 1},
        {"x": "0", "y": 0, "w": 1, "h": 1},
        {"x": 0, "y": 0, "w": None, "h": 1},
        {"x": float("nan"), "y": 0, "w": 0.5, "h": 0.5},
        {"x": 0, "y": float("inf"), "w": 0.5, "h": 0.5},
        {"x": -0.1, "y": 0, "w": 0.5, "h": 0.5},
        {"x": 0, "y": 1.1, "w": 0.5, "h": 0.5},
        {"x": 0.6, "y": 0, "w": 0.5, "h": 0.5},  # x + w > 1
        {"x": 0, "y": 0.6, "w": 0.5, "h": 0.5},  # y + h > 1
        {"x": 0, "y": 0, "w": 0, "h": 0},
        {"x": 0, "y": 0, "w": 0.5, "h": 0.2},  # wrong aspect
        {"x": 0, "y": 0, "w": 0.5, "h": 0.55},  # wrong aspect
    ],
)
def test_bad_values_are_rejected_with_a_message(bad):
    with pytest.raises(CropError) as caught:
        validate_crop(bad, 2000, 1200)
    assert caught.value.message
    assert chr(0x2014) not in caught.value.message


def test_aspect_just_inside_one_percent_is_snapped_not_rejected():
    good_h = 0.5 * 2000 / 1200 / (800 / 480)  # exact h for w=0.5
    crop = validate_crop({"x": 0, "y": 0, "w": 0.5, "h": good_h * 1.009}, 2000, 1200)
    assert math.isclose(crop["h"], good_h, rel_tol=1e-9)
    with pytest.raises(CropError):
        validate_crop({"x": 0, "y": 0, "w": 0.5, "h": good_h * 1.02}, 2000, 1200)


def test_snapped_box_that_leaves_the_image_is_rejected():
    # h is within 1% but snapping up pushes y + h past 1.
    exact_h = 0.5 * 2000 / 1200 / (800 / 480)
    with pytest.raises(CropError):
        validate_crop({"x": 0, "y": 1 - exact_h * 0.995, "w": 0.5, "h": exact_h * 0.995}, 2000, 1200)


def test_minimum_size_is_400_px_of_the_upright_width():
    # 400 / 2000 = 0.2 of the width is the smallest allowed box.
    ok = validate_crop(_box(0, 0, 0.2), 2000, 1200)
    assert ok["w"] == 0.2
    with pytest.raises(CropError):
        validate_crop(_box(0, 0, 0.19), 2000, 1200)


def test_picture_narrower_than_the_minimum_only_allows_the_whole_width():
    # A 300 px wide picture can never hold a 400 px box: the whole width is the
    # only sensible box, anything smaller is rejected.
    iw, ih = 300, 500
    full = {"x": 0, "y": 0.2, "w": 1, "h": 300 / 500 / (800 / 480)}
    assert validate_crop(full, iw, ih)["w"] == 1.0
    with pytest.raises(CropError):
        validate_crop(_box(0, 0, 0.5, img=(iw, ih)), iw, ih)


def test_small_wide_picture_allows_the_largest_box_that_fits():
    # 400x200: no 5:3 box 400 px wide fits (it would be 240 px tall), so the
    # largest box that does fit (the full height) is the minimum.
    full_height = {"x": 0.1, "y": 0, "w": 200 * 5 / 3 / 400, "h": 1}
    assert validate_crop(full_height, 400, 200)["h"] == pytest.approx(1.0, abs=1e-5)
    with pytest.raises(CropError):
        validate_crop(_box(0, 0, 0.5, img=(400, 200)), 400, 200)


# The page's editor (roles/room_picker/files/site/crop-model.js) clamps to the same
# smallest and largest width. tests/js/crop-model.test.js carries this table; keep
# the two in step.
PAGE_RULE_TABLE = [
    # (W, H, smallest width fraction, largest width fraction)
    (2000, 1500, 0.2, 1.0),
    (1500, 2000, 400 / 1500, 1.0),
    (300, 800, 1.0, 1.0),
    (400, 200, 1 / 1.2, 1 / 1.2),
    (4032, 3024, 400 / 4032, 1.0),
]


@pytest.mark.parametrize("iw, ih, smallest, largest", PAGE_RULE_TABLE)
def test_hub_accepts_the_page_clamp_limits_and_rejects_just_outside(iw, ih, smallest, largest):
    for w in (smallest, largest):
        box = _box(0, 0, w, img=(iw, ih))
        assert validate_crop(box, iw, ih)["w"] == pytest.approx(w, abs=1e-6)
    if smallest > 1e-3 and smallest < largest:
        with pytest.raises(CropError):
            validate_crop(_box(0, 0, smallest * 0.95, img=(iw, ih)), iw, ih)


def test_zero_sized_image_is_rejected():
    with pytest.raises(CropError):
        validate_crop({"x": 0, "y": 0, "w": 1, "h": 1}, 0, 100)


# --------------------------------------------------------------------------- #
# Golden: the hub's window equals the window nightstand's detail_crop cuts
# --------------------------------------------------------------------------- #

# The pictures are generated by ``_picture`` below. The expected numbers were
# produced by running nightstand's ``detail_crop`` (src/nightstand/screens/
# common.py) on the same pictures with Image.crop spied to record the cut:
# for each name, (scaled width, scaled height, left, top) of the window in the
# cover-scaled picture (800x480 window). To regenerate: in the nightstand repo
# run a script that builds each picture with ``_picture`` (copy it as is), wraps
# ``Image.Image.crop`` to capture ``box`` and ``self.size`` while calling
# ``detail_crop(image.convert("RGB"), 800, 480)``, and print (size, left, top).
GOLDEN = {
    "landscape": ((1600, 900), (853, 480), (53, 0)),
    "wide": ((2400, 800), (1440, 480), (0, 0)),
    "portrait": ((600, 1000), (800, 1333), (0, 853)),
    "near_5_3": ((1000, 590), (814, 480), (7, 0)),
    "uniform": ((900, 900), (800, 800), (0, 157)),
    "square_busy_top": ((1000, 1000), (800, 800), (0, 0)),
}


def _noise_block(draw, box, seed):
    x0, y0, x1, y1 = box
    for cy in range(y0, y1, 8):
        for cx in range(x0, x1, 8):
            v = ((cx * 73856093) ^ (cy * 19349663) ^ (seed * 83492791)) & 0xFF
            draw.rectangle((cx, cy, cx + 7, cy + 7), fill=(v, (v * 7) & 0xFF, (v * 13) & 0xFF))


def _picture(name):
    sizes = {name: golden[0] for name, golden in GOLDEN.items()}
    w, h = sizes[name]
    image = Image.new("RGB", (w, h), (200, 200, 200))
    draw = ImageDraw.Draw(image)
    if name == "landscape":
        _noise_block(draw, (1100, 200, 1500, 700), 1)
    elif name == "wide":
        _noise_block(draw, (100, 100, 500, 700), 2)
    elif name == "portrait":
        _noise_block(draw, (100, 700, 500, 950), 3)
    elif name == "near_5_3":
        _noise_block(draw, (700, 100, 950, 500), 4)
    elif name == "square_busy_top":
        _noise_block(draw, (200, 40, 800, 300), 5)
    return image


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_auto_window_matches_the_window_nightstand_cuts(name):
    size, scaled, offset = GOLDEN[name]
    image = _picture(name)
    assert image.size == size
    assert auto_window(image) == (scaled[0], scaled[1], offset[0], offset[1])


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_auto_crop_is_that_window_as_fractions_of_the_picture(name):
    size, scaled, offset = GOLDEN[name]
    box = auto_crop(_picture(name))
    assert set(box) == {"x", "y", "w", "h"}
    sw, sh = scaled
    assert box["x"] == pytest.approx(offset[0] / sw, abs=1e-6)
    assert box["y"] == pytest.approx(offset[1] / sh, abs=1e-6)
    assert box["w"] == pytest.approx(800 / sw, abs=1e-6)
    assert box["h"] == pytest.approx(480 / sh, abs=1e-6)
    assert 0 <= box["x"] and box["x"] + box["w"] <= 1 + 1e-9
    assert 0 <= box["y"] and box["y"] + box["h"] <= 1 + 1e-9
    # The box is 5:3 in picture pixels, within rounding of the scaled size.
    aspect = box["w"] * size[0] / (box["h"] * size[1])
    assert aspect == pytest.approx(800 / 480, rel=0.01)


def test_auto_crop_is_none_when_the_picture_cannot_be_measured():
    assert auto_crop(None) is None
    assert auto_crop(Image.new("RGB", (1, 1))) is not None  # tiny but measurable
