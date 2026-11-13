# /// script
# dependencies = ["build123d"]
# ///
"""Geometry regression checks for the Bluetooth antenna perch.

build123d is not a repo dependency, so these checks run through ``cad run``
(not CI). They call ``perch_body`` and measure what it builds, so a parameter
or placement change is caught here before the artifacts are re-exported. The
standard-library CI guard (``tests/python/test_bt_antenna_perch.py``) pins the
shipped STL against the same parameters, so a stale re-export fails there.

Run:
  cad run check_perch.py
"""

import math

import build123d as bd

import perch


def _single_solid(part, label):
    solids = part.solids()
    assert len(solids) == 1, f"{label}: expected one solid, got {len(solids)}"


def _column_min_z(part, x, y):
    """Lowest z where the part has material in a thin column at (x, y)."""
    probe = bd.Pos(x, y, 0) * bd.Box(0.4, 0.4, 400, align=(bd.Align.CENTER,) * 3)
    inter = part & probe
    assert inter.volume > 0, f"no material in column at ({x:.2f}, {y:.2f})"
    return inter.bounding_box().min.Z


def _hole_radius(part, cx, cy, z):
    """Radius of the screw void at height z, by scanning a thin disk outward.

    Returns the largest radius that is still (almost) all air: the hole edge.
    """
    step = 0.05
    for i in range(2, 180):
        r = i * step
        disk = bd.Pos(cx, cy, z) * bd.Cylinder(r, 0.2, align=(bd.Align.CENTER,) * 3)
        material = (part & disk).volume
        full = math.pi * r * r * 0.2
        if material > 0.05 * full:
            return round(r - step, 3)
    raise AssertionError(f"no hole found at ({cx:.2f}, {cy:.2f}, z={z:.2f})")


def check_wall_screw_single_solid():
    part = perch.perch_body()
    _single_solid(part, "wall-screw")
    print(f"wall-screw: 1 solid, volume {part.volume:.0f} mm3, bbox {part.bounding_box().size}")


def check_countersink_opens_upward():
    """A countersink must widen toward the top face where the screw head sits.

    So the screw void radius at the top face (z = floor) must be clearly larger
    than at the bottom face (z = 0). This would fail with the cone inverted.
    """
    part = perch.perch_body()
    floor = perch.base_thickness
    outer_w = perch.adapter_body_width + 2 * perch.clearance + 2 * perch.wall_thickness
    hole_y = outer_w / 2 + perch.mount_ear_width * 0.55

    r_bottom = _hole_radius(part, 0.0, hole_y, 0.3)
    r_top = _hole_radius(part, 0.0, hole_y, floor - 0.3)
    print(f"countersink: radius at z=0 is {r_bottom} mm, at top face is {r_top} mm")
    assert r_top > r_bottom + 1.0, (
        "countersink is inverted: the hole must be wider at the top face "
        f"(screw head) than at z=0, got top {r_top} <= bottom {r_bottom}"
    )
    # The shank radius sets the floor; the head radius sets the top.
    assert abs(r_bottom - perch.screw_hole_diameter / 2) < 0.4, (
        f"bottom radius {r_bottom} should be the shank radius "
        f"{perch.screw_hole_diameter / 2}"
    )
    assert abs(r_top - perch.screw_head_diameter / 2) < 0.4, (
        f"top radius {r_top} should be the head radius "
        f"{perch.screw_head_diameter / 2}"
    )


def check_shelf_clip_slot_gap():
    """The shelf-clip slot must clear shelf_thickness + shelf_clip_gap, and the
    back wall must not hang below the under jaw."""
    part = perch.perch_body(mount="shelf-clip")
    _single_solid(part, "shelf-clip")

    floor = perch.base_thickness
    inner_w = perch.adapter_body_width + 2 * perch.clearance
    outer_w = inner_w + 2 * perch.wall_thickness
    body_zone = perch.adapter_body_length + 2 * perch.clearance
    cable_zone = (
        perch.usb_a_plug_length + perch.extension_cable_diameter + 2 * perch.clearance
    )
    length = cable_zone + perch.wall_thickness + body_zone
    clip_wall = perch.shelf_clip_wall
    expected_slot = perch.shelf_thickness + perch.shelf_clip_gap

    x_back = -length / 2 + clip_wall * 0.5
    x_under = -length / 2 + clip_wall + (length * 0.28) * 0.4

    # Under jaw top is the highest material below the base underside (z = 0).
    below = bd.Pos(x_under, 0, -200 - 0.3) * bd.Box(
        0.4, 0.4, 400, align=(bd.Align.CENTER,) * 3
    )
    under_jaw_top = (part & below).bounding_box().max.Z
    slot_gap = 0.0 - under_jaw_top
    print(f"shelf-clip: slot gap {slot_gap:.2f} mm (want {expected_slot:.2f} mm)")
    assert abs(slot_gap - expected_slot) < 0.5, (
        f"shelf-clip slot gap is {slot_gap:.2f} mm, expected "
        f"{expected_slot:.2f} mm (shelf + gap)"
    )

    back_min_z = _column_min_z(part, x_back, 0.0)
    under_min_z = _column_min_z(part, x_under, 0.0)
    print(
        f"shelf-clip: back wall bottom {back_min_z:.2f} mm, "
        f"under jaw bottom {under_min_z:.2f} mm"
    )
    assert abs(back_min_z - under_min_z) < 0.3, (
        f"back wall ({back_min_z:.2f}) hangs below the under jaw "
        f"({under_min_z:.2f})"
    )


def check_coupon_single_solid_with_one_hole():
    part = perch.perch_coupon_body()
    _single_solid(part, "coupon")
    size = part.bounding_box().size
    assert size.X == perch.coupon_length, f"coupon length {size.X}"
    floor = perch.base_thickness
    outer_w = perch.adapter_body_width + 2 * perch.clearance + 2 * perch.wall_thickness
    hole_y = outer_w / 2 + perch.mount_ear_width * 0.55
    r_bottom = _hole_radius(part, 0.0, hole_y, 0.3)
    r_top = _hole_radius(part, 0.0, hole_y, floor - 0.3)
    assert r_top > r_bottom + 1.0, "coupon countersink is inverted"
    print(f"coupon: 1 solid, {size.X} x {size.Y:.1f} x {size.Z} mm, hole {r_bottom} to {r_top} mm")


def check_invalid_mount_rejected():
    try:
        perch.perch_body(mount="glue")
    except ValueError:
        print("invalid mount_style rejected")
        return
    raise AssertionError("invalid mount_style was not rejected")


if __name__ == "__main__":
    check_wall_screw_single_solid()
    check_countersink_opens_upward()
    check_shelf_clip_slot_gap()
    check_coupon_single_solid_with_one_hole()
    check_invalid_mount_rejected()
    print("ALL CHECKS PASSED")
