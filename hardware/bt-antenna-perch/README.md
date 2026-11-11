# Bluetooth antenna perch

A small printed cradle that holds a USB Bluetooth adapter on the end of a USB-A
extension cable, so the adapter's antenna sits up and away from the metal and
electrical noise of the machine it serves. The adapter drops into an open trough
with its antenna end clear; it plugs into the extension cable through a notch in
the end wall, and the round cable runs out the back along a recessed channel.
Mount it to a shelf or wall with two screws, or print the screwless clip variant
that hooks over a shelf edge.

The defaults fit a TP-Link UB500 Plus on a common USB 2.0 extension cable. Every
dimension is a top-level variable in `perch.py`: measure your own parts, change
the number, and rebuild.

## Files

- `perch.py` - the parametric model (build123d, built with the `cad` command).
- `perch.stl`, `perch.3mf` - the default (wall-screw) mount, rendered from the
  defaults below. The 3MF is the better choice for Bambu Studio or Bambu
  Connect; the STL works anywhere.

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
  wood or machine screw into a shelf or a wall. This is the shipped `perch.stl`
  and `perch.3mf`. It prints base-down with no supports.
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

That rebuilds both mounts and prints a viewer link and an id for each. Check that
the part prints clean:

```sh
cad model dfm "<id>" --process printing --technology fdm --material pla --layer-height 0.2
```

Re-export the committed files from the default (wall-screw) id:

```sh
cad model export "<id>" perch.stl perch.3mf --tolerance 0.06
```

To ship a shelf-clip instead, set `mount_style = "shelf-clip"` at the top of
`perch.py` (or export the `perch_shelf_clip` model's id) and re-export.
