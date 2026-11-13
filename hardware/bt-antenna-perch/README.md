# Bluetooth antenna perch

A small printed cradle that holds a USB Bluetooth adapter on the end of a USB-A
extension cable, so the adapter's antenna sits up and away from the metal and
electrical noise of the machine it serves. The adapter drops into an open trough
with its antenna end clear; it plugs into the extension cable through a notch in
the end wall, and the plug and round cable sit in a recessed channel in line
with the body. The adapter is held by gravity in the open-top trough, so the
perch is meant for a horizontal surface. Screw it down to a shelf, desk, or the
top of a cabinet, or print the screwless clip variant that hooks over a shelf
edge.

The defaults fit a TP-Link UB500 Plus on a common USB 2.0 extension cable. Every
dimension is a top-level variable in `perch.py`: measure your own parts, change
the number, and rebuild.

## Files

- `perch.py` - the parametric model (build123d, built with the `cad` command).
- `check_perch.py` - geometry regression checks (single solid, countersink
  orientation, shelf-clip slot gap), run with `cad run` rather than in CI
  because build123d is not a repo dependency.
- `perch.stl`, `perch.3mf` - the default (wall-screw) mount, rendered from the
  defaults below. The 3MF is the better choice for Bambu Studio or Bambu
  Connect; the STL works anywhere.
- `perch-coupon.stl`, `perch-coupon.3mf` - the test-fit coupon (see the print
  sheet below). Same parameters as the perch, built by `perch_coupon_body`.
- `perch.intent.json`, `perch-coupon.intent.json` - the measurable intents, the
  prior-art record and the reference-hardware tier for each mesh, written for the
  printable-parts checks.
- `sliced/perch-coupon.gcode.3mf`, `sliced/perch.gcode.3mf` - ready to open in
  Bambu Studio (File, Open). Sliced with OrcaSlicer 2.5.0 for the Bambu Lab X2D.
  Bambu Studio re-slices for your X2D itself, so check the settings it shows.

## Print sheet (Bambu Lab X2D, first print)

Print the coupon first, then the perch. Never skip the coupon: the adapter
numbers are an estimate, not a measurement (see the next section).

| | Coupon | Perch |
|---|---|---|
| File | `perch-coupon.3mf` (or `sliced/perch-coupon.gcode.3mf`) | `perch.3mf` (or `sliced/perch.gcode.3mf`) |
| Size | 12 x 43.7 x 16 mm | 68.9 x 56.2 x 16 mm |
| Time (sliced estimate) | about 9 minutes | about 20 minutes |

- Printer: Bambu Lab X2D, 0.4 mm nozzle.
- Filament: PLA (Bambu PLA Basic profile). Any colour.
- Layer height: 0.20 mm (the 0.20mm Standard profile), 3 wall loops, 15 percent
  infill.
- Orientation: as modeled, flat floor on the plate. The adapter pocket opens
  upward and the countersinks face up, so the head seat prints on 45 degree
  slopes with no bridging. Do not rotate it.
- Supports: none. Brim: automatic is fine.

Slicing note: OrcaSlicer 2.5.0 was used with the Bambu Lab X2D 0.4 nozzle machine
profile, the 0.20mm Standard @BBL X2D process and Bambu PLA Basic @BBL X2D 0.4
nozzle, all taken from the v2.5.0 source tree (the installed app packs its
profiles in a form the headless helper cannot read). Nothing was sent to a
printer.

### What to check on the coupon

1. Lay the UB500 Plus on its side so the body (not the antenna) is square to the
   pocket, then slide it through the pocket. It should go in with light friction:
   moves with a gentle push, does not rock or fall through. It must not need force.
2. Put an M4 countersunk (flat head) screw in the ear hole. The head should sit
   flush with the top face, not proud and not sunk below it. The shank should turn
   freely in the hole.
3. Measure the pocket with calipers: it should read 25.2 mm between the walls.
   The adapter body width is 24 mm (an estimate), so the real gap per side is
   about 0.6 mm.

If it is wrong, change one parameter at the top of `perch.py` and reprint the
coupon (the perch follows from the same numbers):

| Symptom | Parameter | Change |
|---|---|---|
| Adapter will not go in, or needs force | `clearance` | add 0.1 (try 0.7, then 0.8) |
| Adapter rocks or slides freely | `clearance` | subtract 0.1 (try 0.5) |
| Adapter is the wrong width even at the extremes | `adapter_body_width` | set to your caliper reading |
| Screw shank is tight in the hole | `screw_hole_diameter` | add 0.2 |
| Screw head sits proud | `screw_head_diameter` | add 0.2 |
| Screw head sinks below the face | `screw_head_diameter` | subtract 0.2 |

The coupon cannot test the body length or the plug end of the trough. Check
those on the first perch print and set `adapter_body_length` and the plug
numbers from there.

## Reference hardware: unverified estimate

The UB500 Plus is a tier 5 reference: a padded estimate from TP-Link's published
81 x 42 x 16.8 mm envelope, which includes the fold-out antenna. Only the height
is published. The coupon is therefore mandatory before the perch. This design is
kept over the existing Printables "USB Extension Cord WiFi Dongle Stand" (265738)
and its M4 remix (1191970), searched on 2026-11-12, because it is sized to the
UB500 Plus. Nothing was copied from either.

## Print settings (Bambu Lab X2D)

- Material: PLA or PETG. PETG if the perch will sit somewhere warm or in sun.
- Layer height: 0.2 mm.
- Walls: 3 or more perimeters. Infill 15 to 20 percent is plenty; this is a
  light-duty holder.
- Supports: none. The default wall-screw mount is modeled so nothing overhangs
  past 45 degrees when printed base-down (trough opening up), and the screw
  countersinks open upward, so they print clean without support. Orient the flat
  base on the plate and slice with supports off.
- Brim: optional. A small brim helps the two screw ears stay stuck for the first
  layer, but is not needed on a clean plate.

After slicing, open the `.gcode.3mf` in Bambu Connect (or the `.3mf`/`.stl` in
Bambu Studio), pick the printer and filament, and check the first layer.

## Dimensions and where they come from

The adapter envelope TP-Link publishes for the UB500 Plus is 81 x 42 x 16.8 mm,
but that figure is the whole product including its fold-out antenna, and the spec
page does not break out the bare body. So the body length and width defaults are
a deliberately generous body-only estimate, and the `clearance` variable adds
room on top; the published 16.8 mm is used for the body height. The antenna is
not cradled: it hangs out of the open end of the trough and you angle it by hand.

| Variable | Default (mm) | Source |
|---|---|---|
| `adapter_body_length` | 45.0 | Generous body-only estimate; antenna excluded. Re-measure yours. |
| `adapter_body_width` | 24.0 | Generous body-only estimate. Re-measure yours. |
| `adapter_body_height` | 16.8 | TP-Link published height (81 x 42 x **16.8** mm). |
| `usb_a_plug_width` | 12.0 | Standard USB-A plug shell width. |
| `usb_a_plug_height` | 4.5 | Standard USB-A plug shell height. |
| `usb_a_plug_length` | 14.0 | Typical USB-A plug plus a little of the mating shell. |
| `extension_cable_diameter` | 4.5 | Not published; generous for a common USB 2.0 lead. Measure yours. |
| `clearance` | 0.6 | Added around every pocket, per side, for a firm slide-in fit. |

Where a number is not published (the body length and width, the cable diameter)
the default is generous and `clearance` widens the fit further, so a first print
should hold the adapter even if your part differs a little. Tighten the fit by
re-measuring and reducing the body numbers.

### How to measure

Use calipers, or a ruler and a steady hand:

- Adapter body: length from the base of the antenna to the shoulder where the
  USB-A plug begins, the width across the body, and the height. Leave the antenna
  out of these numbers.
- USB-A plug: the width and height of the metal plug shell (about 12 x 4.5 mm on
  every USB-A plug, so you rarely need to change these).
- Extension cable: the outer diameter of the round lead, not the connector.

Set each number in the top block of `perch.py` and rebuild.

## Mount styles

`mount_style` selects how the perch fastens:

- `"wall-screw"` (default): two countersunk holes through side ears, for an M4
  wood or machine screw down into a horizontal surface (a shelf, desk, or the
  top of a cabinet). The hole is a 4.5 mm clearance hole and the countersink
  widens toward the top face so the screw head seats flush. This is the shipped
  `perch.stl` and `perch.3mf`. It prints base-down with no supports.
- `"shelf-clip"`: a screwless hook that straddles the front edge of a shelf (set
  `shelf_thickness` to your shelf). Because the hook has to wrap under the shelf
  lip, this variant cannot print entirely support-free: when printed cradle-up
  it needs a little support under the hook, which sits on the hidden underside
  and snaps off. Use `wall-screw` if you want a no-support print.

## Rebuild and re-export

The model is built with the text-to-cad `cad` command (installed at
`~/.local/bin/cad`), not OpenSCAD:

```sh
cad run perch.py
```

That rebuilds both mounts and prints a viewer link and an id for each. Run the
geometry checks, then check that the part prints clean:

```sh
cad run check_perch.py
cad model dfm "<id>" --process printing --technology fdm --material pla --layer-height 0.2
```

Re-export the committed files from the default (wall-screw) id:

```sh
cad model export "<id>" perch.stl perch.3mf --tolerance 0.06
```

To ship a shelf-clip instead, set `mount_style = "shelf-clip"` at the top of
`perch.py` (or export the `perch_shelf_clip` model's id) and re-export.
