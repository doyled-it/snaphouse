# /// script
# dependencies = ["build123d"]
# ///
"""Printable perch for a USB Bluetooth adapter on an extension cable.

The perch is a small cradle that holds a USB Bluetooth adapter (the default
numbers fit a TP-Link UB500 Plus) upright in a trough so its antenna end points
clear of the desk. The adapter plugs into a USB-A extension cable whose round
lead drops out through a groove at the back, which lets the antenna sit away
from the metal and noise of the machine it serves. Every dimension below is a
top-level parameter: measure your own adapter and cable, change the number, and
rebuild. The README explains how to measure and print it.

FDM 3D printed in PLA or PETG, 0.2 mm layers, no supports by design. The default
(wall-screw) mount prints base-down with no overhang past 45 degrees.

Build and check:
  cad run perch.py
  cad model dfm "<id>" --process printing --technology fdm --material pla --layer-height 0.2
Export the committed artifacts:
  cad model export "<id>" perch.stl perch.3mf
"""

import build123d as bd
from cad import model

# --- Adapter body (the part the cradle grips) -------------------------------
# TP-Link publishes the UB500 Plus envelope as 81 x 42 x 16.8 mm, but that is
# the whole product including the fold-out antenna, and it does not break out
# the bare body. These defaults are a generous body-only estimate; the antenna
# hangs out of the open end of the trough and is not cradled. Re-measure your
# adapter body and set these three. Height uses the published 16.8 mm.
adapter_body_length = 45.0
adapter_body_width = 24.0
adapter_body_height = 16.8

# --- USB-A plug (the metal connector that mates with the extension cable) ----
# A standard USB-A plug shell is about 12 mm wide and 4.5 mm tall. The plug end
# of the trough is relieved to this size so the connector shell clears and the
# body still seats flat on the floor.
usb_a_plug_width = 12.0
usb_a_plug_height = 4.5
usb_a_plug_length = 14.0

# --- Extension cable --------------------------------------------------------
# Outer diameter of the round lead on a common USB 2.0 extension cable. This is
# not a published figure and varies by cable, so the default is generous and the
# clearance below widens the groove further. Measure yours with calipers.
extension_cable_diameter = 4.5

# --- Fit --------------------------------------------------------------------
# Added around every pocket that holds a part, per side. FDM prints run a little
# oversize, so 0.6 mm keeps the adapter a firm slide-in rather than a press fit.
clearance = 0.6

# --- Structure --------------------------------------------------------------
wall_thickness = 3.0      # trough side walls and the plug-end stop
base_thickness = 5.0      # floor under the adapter, and the mounting foot
cradle_wall_height = 11.0 # how far the side walls rise around the body

# --- Mount ------------------------------------------------------------------
# "wall-screw": two countersunk holes through side ears, for fastening the
#   perch down to a shelf or flat against a wall. Prints base-down, supports off.
# "shelf-clip": a hook that straddles the front edge of a shelf, no screws.
#   The hook wraps under the shelf, so this variant needs a little support under
#   the hook; see the README. Use wall-screw for a no-support print.
mount_style = "wall-screw"
screw_hole_diameter = 4.0     # clearance hole for an M4 wood screw
screw_head_diameter = 8.0     # countersink across the flats of the head
mount_ear_width = 11.0        # how far each screw ear sticks out past a wall
shelf_thickness = 25.0        # the shelf lip the clip hooks over
shelf_clip_gap = 2.0          # jaw clearance around that lip
shelf_clip_wall = 3.5         # wall thickness of the hook jaws

MOUNT_STYLES = ("wall-screw", "shelf-clip")


def perch_body(
    body_length=adapter_body_length,
    body_width=adapter_body_width,
    body_height=adapter_body_height,
    plug_width=usb_a_plug_width,
    plug_height=usb_a_plug_height,
    plug_length=usb_a_plug_length,
    cable_diameter=extension_cable_diameter,
    fit=clearance,
    wall=wall_thickness,
    floor=base_thickness,
    wall_height=cradle_wall_height,
    mount=mount_style,
    screw_d=screw_hole_diameter,
    screw_head_d=screw_head_diameter,
    ear_width=mount_ear_width,
    shelf=shelf_thickness,
    clip_gap=shelf_clip_gap,
    clip_wall=shelf_clip_wall,
):
    if mount not in MOUNT_STYLES:
        raise ValueError(f"mount_style must be one of {MOUNT_STYLES}, got {mount!r}")

    inner_w = body_width + 2 * fit
    outer_w = inner_w + 2 * wall
    body_zone = body_length + 2 * fit
    cable_zone = plug_length + cable_diameter + 2 * fit
    length = cable_zone + wall + body_zone
    total_h = floor + wall_height

    # X runs cable end (-X) to antenna end (+X, open). The stop wall sits
    # between the cable zone and the body zone. Work out its center on X.
    stop_x = -length / 2 + cable_zone + wall / 2

    # Floor slab spanning the whole footprint.
    floor_slab = bd.Box(
        length, outer_w, floor,
        align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN),
    )

    # Two side walls over the body zone and the stop wall (not the cable zone),
    # so the adapter is cradled and the cable end stays open on top.
    wall_run = body_zone + wall
    wall_cx = (stop_x - wall / 2 + length / 2) / 2  # midpoint of the walled span
    side = bd.Box(
        wall_run, wall, wall_height,
        align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN),
    )
    wall_y = inner_w / 2 + wall / 2
    walls = (
        bd.Pos(wall_cx, wall_y, floor) * side
        + bd.Pos(wall_cx, -wall_y, floor) * side
    )

    # Stop wall across the trough at the plug end, full height, with a
    # full-height central notch so the USB-A plug and the round cable pass
    # through while the wider adapter body butts against the two shoulders.
    notch_w = max(plug_width, cable_diameter) + 2 * fit
    stop = bd.Box(
        wall, outer_w, wall_height,
        align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN),
    )
    notch = bd.Box(
        wall * 3, notch_w, wall_height + floor,
        align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN),
    )
    notch = bd.Pos(stop_x, 0, floor) * notch
    stop = bd.Pos(stop_x, 0, floor) * stop - notch

    part = floor_slab + walls + stop

    # Connector relief: a shallow recess in the floor at the plug end, so the
    # USB-A plug and the mating extension receptacle nest below the body and let
    # it seat flat. It runs from inside the body zone, across the stop wall, and
    # on into the open cable zone where the receptacle and round cable sit.
    relief_depth = min(plug_height + fit, floor - 2.0)
    relief_w = max(plug_width, cable_diameter) + 2 * fit
    relief_len = plug_length + 2 * fit + cable_zone
    relief = bd.Box(
        relief_len, relief_w, relief_depth * 2,
        align=(bd.Align.MAX, bd.Align.CENTER, bd.Align.CENTER),
    )
    # MAX on X puts its +X face at the reference; place that just inside the
    # body zone, and sink it so it only cuts the top `relief_depth` of the floor.
    relief_x = stop_x + wall / 2 + plug_length * 0.4
    relief = bd.Pos(relief_x, 0, floor) * relief
    part = part - relief

    if mount == "wall-screw":
        part = part + _screw_ears(length, outer_w, floor, ear_width, wall)
        part = part - _screw_holes(length, outer_w, floor, ear_width, screw_d, screw_head_d)
    else:  # shelf-clip
        part = part + _shelf_clip(length, outer_w, floor, shelf, clip_gap, clip_wall)

    part = part.clean()
    part.label = "bt-antenna-perch"
    part.color = "#3b7a57"
    return part


def _screw_ears(length, outer_w, floor, ear_width, wall):
    """Two flat tabs out each side at mid length, as thick as the floor."""
    ear = bd.Box(
        length * 0.42,
        ear_width + wall,  # overlap the body wall so they fuse
        floor,
        align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN),
    )
    ear_y = outer_w / 2 + ear_width / 2
    return bd.Pos(0, ear_y, 0) * ear + bd.Pos(0, -ear_y, 0) * ear


def _screw_holes(length, outer_w, floor, ear_width, screw_d, screw_head_d):
    """Countersunk through holes in the two ears; cones open upward."""
    # Center the hole in the ear, a little out from the body wall.
    hole_y = outer_w / 2 + ear_width * 0.55
    shank = bd.Cylinder(
        screw_d / 2,
        floor * 3,
        align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.CENTER),
    )
    # Countersink: a cone widening to the top face (82 degree head is ~ a cone
    # whose radius grows by its depth; a floor-deep cone is self-supporting).
    cone = bd.Cone(
        screw_head_d / 2,
        screw_d / 2,
        floor,
        align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN),
    )
    one = shank + cone
    holes = bd.Pos(0, hole_y, 0) * one + bd.Pos(0, -hole_y, 0) * one
    return holes


def _shelf_clip(length, outer_w, floor, shelf, clip_gap, clip_wall):
    """A hook straddling a shelf's front edge, hung off the cable-end face.

    The base is the top jaw. A back wall drops below the base at the cable end,
    then an under jaw returns forward, leaving a slot of shelf + clip_gap for the
    shelf lip. The under jaw has to reach below the shelf, so this variant needs
    a little support under the hook; the README explains the tradeoff.
    """
    slot = shelf + clip_gap
    jaw_depth = min(outer_w, 28.0)
    back = bd.Box(
        clip_wall,
        jaw_depth,
        floor + slot + clip_wall,
        align=(bd.Align.MAX, bd.Align.CENTER, bd.Align.MAX),
    )
    back = bd.Pos(-length / 2 + clip_wall, 0, floor) * back
    under = bd.Box(
        clip_wall + length * 0.28,
        jaw_depth,
        clip_wall,
        align=(bd.Align.MAX, bd.Align.CENTER, bd.Align.MAX),
    )
    under = bd.Pos(-length / 2 + clip_wall + length * 0.28, 0, floor - slot) * under
    return back + under


@model
def perch():
    return perch_body()


@model
def perch_shelf_clip():
    return perch_body(mount="shelf-clip")


if __name__ == "__main__":
    perch()
    perch_shelf_clip()
