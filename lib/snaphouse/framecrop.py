"""Frame crop: validate a saved crop box and compute the automatic one.

A crop is ``{"x", "y", "w", "h"}``: the left, top, width and height of a 5:3 box
as fractions of the UPRIGHT picture (after ``ImageOps.exif_transpose``). The
frames are 800x480, so the box keeps that aspect in picture pixels:
``w * width / (h * height)`` equals 800/480. Design:
``docs/superpowers/specs/2026-11-25-frame-crop-design.md``.

Two things live here, both pure (no files, no HTTP):

* ``validate_crop`` checks a box a person drew and snaps ``h`` to the exact
  aspect, or raises ``CropError`` with a short message the page can show.
* ``auto_window`` and ``auto_crop`` are the hub's port of the window choice in
  nightstand's ``detail_crop`` (``src/nightstand/screens/common.py``): scale the
  picture to cover 800x480, then slide the window along the overflowing axis
  toward the busiest edge energy. Only the position is ported, not the dither.
  ``tests/python/test_framecrop.py`` pins it to the window the frame cuts.
"""

from __future__ import annotations

import math
from typing import Optional

from PIL import Image, ImageFilter

#: The frames' panel, in pixels. The box aspect is PANEL_WIDTH / PANEL_HEIGHT.
PANEL_WIDTH = 800
PANEL_HEIGHT = 480

#: A saved box must be within this relative error of 5:3 in picture pixels; ``h``
#: is then snapped to the exact value.
ASPECT_TOLERANCE = 0.01

#: A saved box must cover at least this many pixels of the upright picture's
#: width, so the frame is never blowing up a tiny region. A picture too small
#: for that can only be cropped to the largest box that fits.
MIN_BOX_PIXELS = 400

#: Slack for float comparisons on the unit interval.
_EPS = 1e-9

#: Stored precision: six decimals is far below one pixel of any picture.
_DECIMALS = 6

# Constants of nightstand's detail_crop, kept identical.
_ASPECT_TOLERANCE = 0.1
_ANALYSIS_SHORT_SIDE = 160

_KEYS = ("x", "y", "w", "h")


class CropError(ValueError):
    """A crop that cannot be saved. ``message`` is safe to show on the page."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _number(value) -> Optional[float]:
    """The value as a float, or ``None`` for a bool, a string, NaN or infinity."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def validate_crop(crop, width: int, height: int) -> dict:
    """Check ``crop`` against an upright picture of ``width`` x ``height``.

    Returns the box to store (all four numbers rounded, ``h`` snapped to the
    exact 5:3 aspect). Raises ``CropError`` for a wrong type, a value outside
    [0, 1], a box that leaves the picture, a box too small, or a wrong aspect.
    """
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        raise CropError("That picture cannot be cropped.")
    if not isinstance(crop, dict) or set(crop) != set(_KEYS):
        raise CropError("The crop needs x, y, w and h.")
    values = {}
    for key in _KEYS:
        number = _number(crop[key])
        if number is None:
            raise CropError("The crop values must be numbers.")
        if number < -_EPS or number > 1 + _EPS:
            raise CropError("The crop must stay inside the picture.")
        values[key] = min(max(number, 0.0), 1.0)
    x, y, w, h = values["x"], values["y"], values["w"], values["h"]
    if x + w > 1 + _EPS or y + h > 1 + _EPS:
        raise CropError("The crop must stay inside the picture.")

    # The smallest box is 400 px wide, or the largest box that fits when the
    # picture is too small for that (a narrow picture: its whole width; a short,
    # wide one: its full height).
    max_w = min(1.0, height * PANEL_WIDTH / PANEL_HEIGHT / width)
    min_w = min(MIN_BOX_PIXELS / width, max_w)
    if w < min_w - 2e-6:  # stored precision is six decimals
        raise CropError("That crop is too small.")

    exact_h = w * width / height * PANEL_HEIGHT / PANEL_WIDTH
    if exact_h <= 0 or abs(h - exact_h) > ASPECT_TOLERANCE * exact_h:
        raise CropError("The crop must keep the frame's shape.")
    h = exact_h
    x, y, w, h = (round(v, _DECIMALS) for v in (x, y, w, h))
    if y + h > 1 + 1e-6:
        raise CropError("The crop must stay inside the picture.")
    h = min(h, round(1 - y, _DECIMALS))
    return {"x": x, "y": y, "w": w, "h": h}


# --------------------------------------------------------------------------- #
# The automatic box: a port of nightstand's detail_crop window choice
# --------------------------------------------------------------------------- #


def auto_window(image: Image.Image) -> tuple[int, int, int, int]:
    """The window ``detail_crop`` cuts, in the cover-scaled picture.

    Returns ``(scaled_width, scaled_height, left, top)``: the picture scaled to
    cover 800x480 and the window's top-left corner in that scaled picture. The
    picture is converted to RGB first, as the frame does.
    """
    image = image.convert("RGB")
    width, height = PANEL_WIDTH, PANEL_HEIGHT
    source_width, source_height = image.size
    scale = max(width / source_width, height / source_height)
    scaled = image.resize(
        (round(source_width * scale), round(source_height * scale)),
        Image.Resampling.LANCZOS,
    )
    scaled_width, scaled_height = scaled.size
    horizontal_overflow = scaled_width - width
    vertical_overflow = scaled_height - height

    if abs(source_width / source_height - width / height) <= _ASPECT_TOLERANCE:
        return scaled_width, scaled_height, horizontal_overflow // 2, vertical_overflow // 2

    if horizontal_overflow >= vertical_overflow:
        energies = _axis_energy(scaled, axis=0)
        left = _best_offset(energies, width, scaled_width, horizontal_overflow)
        top = vertical_overflow // 2
    else:
        energies = _axis_energy(scaled, axis=1)
        top = _best_offset(energies, height, scaled_height, vertical_overflow)
        left = horizontal_overflow // 2
    return scaled_width, scaled_height, left, top


def auto_crop(image: Optional[Image.Image]) -> Optional[dict]:
    """The automatic box for an upright picture, as picture fractions, or ``None``.

    ``None`` when the picture is missing or cannot be measured. The box is the
    ``auto_window`` expressed against the scaled picture's size, so the frame
    cutting this box from the original and resizing it to 800x480 shows what
    ``detail_crop`` shows today.
    """
    if image is None:
        return None
    try:
        scaled_width, scaled_height, left, top = auto_window(image)
    except (ValueError, ZeroDivisionError, OSError):
        return None
    if scaled_width <= 0 or scaled_height <= 0:
        return None
    return {
        "x": round(left / scaled_width, _DECIMALS),
        "y": round(top / scaled_height, _DECIMALS),
        "w": round(min(PANEL_WIDTH, scaled_width) / scaled_width, _DECIMALS),
        "h": round(min(PANEL_HEIGHT, scaled_height) / scaled_height, _DECIMALS),
    }


def _axis_energy(scaled: Image.Image, axis: int) -> list[int]:
    """Edge energy per column (axis 0) or per row (axis 1) of ``scaled``."""
    scaled_width, scaled_height = scaled.size
    short = min(scaled_width, scaled_height)
    ratio = _ANALYSIS_SHORT_SIDE / short
    small = scaled.convert("L").resize(
        (max(round(scaled_width * ratio), 3), max(round(scaled_height * ratio), 3)),
        Image.Resampling.BOX,
    )
    edges = small.filter(ImageFilter.FIND_EDGES)
    interior = edges.crop((1, 1, edges.width - 1, edges.height - 1))
    if axis == 0:
        line = interior.resize((interior.width, 1), Image.Resampling.BOX)
    else:
        line = interior.resize((1, interior.height), Image.Resampling.BOX)
    return list(line.tobytes())


def _best_offset(energies: list[int], window: int, scaled_span: int, overflow: int) -> int:
    """The window offset in scaled coordinates with the most edge energy.

    Ties go to the window whose center sits nearest the energy centroid, so a
    uniform picture stays centered. The small-space offset is mapped back
    proportionally onto the real ``overflow``.
    """
    bins = len(energies)
    window_bins = max(1, min(bins, round(window / scaled_span * bins)))
    slide = bins - window_bins
    if slide <= 0:
        return overflow // 2

    prefix = [0]
    for value in energies:
        prefix.append(prefix[-1] + value)

    total = prefix[-1]
    if total > 0:
        centroid = sum(i * e for i, e in enumerate(energies)) / total
    else:
        centroid = (bins - 1) / 2

    best_k = 0
    best_score = -1
    best_gap = float("inf")
    for k in range(slide + 1):
        score = prefix[k + window_bins] - prefix[k]
        gap = abs(k + window_bins / 2 - centroid)
        if score > best_score or (score == best_score and gap < best_gap):
            best_score = score
            best_gap = gap
            best_k = k
    return round(best_k / slide * overflow)
