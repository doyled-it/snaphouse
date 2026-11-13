# /// script
# dependencies = ["build123d"]
# ///
"""Printable perch for a USB Bluetooth adapter on an extension cable.

The perch is a small cradle that holds a USB Bluetooth adapter (the default
numbers fit a TP-Link UB500 Plus) in a horizontal trough, with its antenna end
pointing out of the open end of the trough and clear of the desk. The adapter
plugs into a USB-A extension cable through a full-height notch in the end wall;
the plug and the round lead sit in a recessed relief channel in line with the
body, so the body seats flat. This keeps the antenna away from the metal and
noise of the machine it serves. Every dimension below is a top-level parameter:
measure your own adapter and cable, change the number, and rebuild. The README
explains how to measure and print it.

FDM 3D printed in PLA or PETG, 0.2 mm layers. The default (wall-screw) mount
prints base-down with no supports and no overhang past 45 degrees; the
shelf-clip mount needs a little support under the hook (see the README).

Build and check:
  cad run perch.py
  cad run check_perch.py
  cad model dfm "<id>" --process printing --technology fdm --material pla --layer-height 0.2
Export the committed artifacts:
  cad model export "<id>" perch.stl perch.3mf --tolerance 0.06
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
# The adapter sits in an open-top trough and is held by gravity, so the perch is
# meant for a horizontal surface; the mount keeps it from sliding.
# "wall-screw": two countersunk holes through side ears, for fastening the perch
#   down onto a shelf, desk, or the top of a cabinet. Prints base-down,
#   supports off.
# "shelf-clip": a hook that straddles the front edge of a shelf, no screws.
#   The hook wraps under the shelf, so this variant needs a little support under
#   the hook; see the README. Use wall-screw for a no-support print.
mount_style = "wall-screw"
screw_hole_diameter = 4.5     # clearance hole for an M4 screw (M4 wants ~4.5 mm)
screw_head_diameter = 8.0     # countersink across the flats of the head
mount_ear_width = 11.0        # how far each screw ear sticks out past a wall
shelf_thickness = 25.0        # the shelf lip the clip hooks over
shelf_clip_gap = 2.0          # jaw clearance around that lip
shelf_clip_wall = 3.5         # wall thickness of the hook jaws

MOUNT_STYLES = ("wall-screw", "shelf-clip")

# --- Test-fit coupon --------------------------------------------------------
# A thin slice of the perch: the adapter pocket cross-section (floor plus both
# side walls) and one screw ear with its countersunk hole. It reuses every
# parameter above, so it tests the same numbers as the full part. Prints
# base-down, no supports, in well under 15 minutes.
coupon_length = 12.0  # slice thickness along the trough; > screw_head_diameter


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

    # Connector relief: a shallow recess in the top of the floor at the plug
    # end, so the USB-A plug and the mating extension receptacle nest below the
    # body and let it seat flat. It runs from inside the body zone, across the
    # stop wall (where the full-height notch has already cleared the centre),
    # and on into the open cable zone where the receptacle and round cable sit.
    relief_depth = min(plug_height + fit, floor - 2.0)
    relief_w = max(plug_width, cable_diameter) + 2 * fit
    relief_len = plug_length + 2 * fit + cable_zone
    # The cut box bottom sits at floor - relief_depth (the floor below the
    # recess stays intact) and its top a small overshoot above the floor top,
    # so the top face cuts cleanly. That overshoot is the same width as the
    # stop-wall notch, so it removes no extra material from the stop wall.
    overshoot = 0.5
    relief = bd.Box(
        relief_len, relief_w, relief_depth + overshoot,
        align=(bd.Align.MAX, bd.Align.CENTER, bd.Align.MAX),
    )
    # MAX on X puts its +X face at the reference; place that just inside the
    # body zone. MAX on Z puts the box top at floor + overshoot.
    relief_x = stop_x + wall / 2 + plug_length * 0.4
    relief = bd.Pos(relief_x, 0, floor + overshoot) * relief
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


def _screw_holes(length, outer_w, floor, ear_width, screw_d, screw_head_d, sides=(1, -1)):
    """Countersunk through holes in the ears; cones open upward.

    ``sides`` picks the ears: (1, -1) is both, (1,) is the +Y ear only."""
    # Center the hole in the ear, a little out from the body wall.
    hole_y = outer_w / 2 + ear_width * 0.55
    shank = bd.Cylinder(
        screw_d / 2,
        floor * 3,
        align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.CENTER),
    )
    # Countersink: a cone that widens to the top face where the screw head
    # seats. bd.Cone(bottom_radius, top_radius, height) with Align.MIN spans
    # z=0..floor, so the shank radius is at the bottom face and the wider head
    # radius at the top. A top-widening cut leaves an upward-facing slope, so it
    # prints without support base-down.
    cone = bd.Cone(
        screw_d / 2,
        screw_head_d / 2,
        floor,
        align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN),
    )
    one = shank + cone
    holes = bd.Pos(0, sides[0] * hole_y, 0) * one
    for side in sides[1:]:
        holes = holes + bd.Pos(0, side * hole_y, 0) * one
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
    # The base underside (the top jaw) is at z=0, so the under jaw top sits at
    # -slot to leave a clear gap of shelf + clip_gap. Its bottom then lands at
    # -slot - clip_wall, the same as the back wall bottom, so nothing hangs
    # below the jaw.
    under = bd.Pos(-length / 2 + clip_wall + length * 0.28, 0, -slot) * under
    return back + under


def perch_coupon_body(
    body_width=adapter_body_width,
    fit=clearance,
    wall=wall_thickness,
    floor=base_thickness,
    wall_height=cradle_wall_height,
    screw_d=screw_hole_diameter,
    screw_head_d=screw_head_diameter,
    ear_width=mount_ear_width,
    thickness=coupon_length,
):
    """Test-fit coupon: the pocket cross-section and one countersunk ear.

    Same width, wall, floor and screw numbers as ``perch_body``; the adapter
    pocket is open at both ends so the adapter body can be slid or dropped in.
    The ear is on the +Y side, as in the full part.
    """
    inner_w = body_width + 2 * fit
    outer_w = inner_w + 2 * wall
    align = (bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
    slab = bd.Box(thickness, outer_w, floor, align=align)
    side = bd.Box(thickness, wall, wall_height, align=align)
    wall_y = inner_w / 2 + wall / 2
    part = slab + bd.Pos(0, wall_y, floor) * side + bd.Pos(0, -wall_y, floor) * side
    ear = bd.Box(thickness, ear_width + wall, floor, align=align)
    part = part + bd.Pos(0, outer_w / 2 + ear_width / 2, 0) * ear
    part = part - _screw_holes(
        thickness, outer_w, floor, ear_width, screw_d, screw_head_d, sides=(1,)
    )
    part = part.clean()
    part.label = "bt-antenna-perch-coupon"
    part.color = "#c98a2b"
    return part


@model
def perch():
    return perch_body()


@model
def perch_shelf_clip():
    return perch_body(mount="shelf-clip")


@model
def perch_coupon():
    return perch_coupon_body()


if __name__ == "__main__":
    perch()
    perch_shelf_clip()
    perch_coupon()
