"""Guards for the printable Bluetooth antenna perch.

The perch is a parametric build123d model (``perch.py``) built with the
text-to-cad ``cad`` command; the geometry itself is checked by the CAD check
scripts that run through ``cad run`` (build123d is not a repo dependency, so it
never runs in CI). These tests guard the committed artifacts with the standard
library only: that the source still exposes every top-level parameter the brief
calls for, that ``mount_style`` defaults to one of the two supported mounts, and
that the rendered ``perch.stl`` and ``perch.3mf`` are well formed so a broken
re-export is caught in review rather than at the printer.
"""

import ast
import math
import struct
import zipfile
from pathlib import Path

PERCH_DIR = Path(__file__).resolve().parents[2] / "hardware" / "bt-antenna-perch"
PERCH_PY = PERCH_DIR / "perch.py"
PERCH_STL = PERCH_DIR / "perch.stl"
PERCH_3MF = PERCH_DIR / "perch.3mf"

# The top-level parameters the brief requires, as module-level assignments.
REQUIRED_PARAMETERS = {
    "adapter_body_length",
    "adapter_body_width",
    "adapter_body_height",
    "usb_a_plug_width",
    "usb_a_plug_height",
    "extension_cable_diameter",
    "clearance",
    "mount_style",
}

SUPPORTED_MOUNTS = {"wall-screw", "shelf-clip"}


def _module_level_assignments():
    """Map every module-level ``name = literal`` in perch.py to its value."""
    tree = ast.parse(PERCH_PY.read_text())
    values = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, SyntaxError):
            value = None
        for target in node.targets:
            if isinstance(target, ast.Name):
                values[target.id] = value
    return values


def test_source_declares_every_parameter():
    assigns = _module_level_assignments()
    missing = REQUIRED_PARAMETERS - assigns.keys()
    assert not missing, f"perch.py is missing top-level parameters: {sorted(missing)}"


def test_numeric_parameters_are_positive():
    assigns = _module_level_assignments()
    for name in REQUIRED_PARAMETERS - {"mount_style"}:
        value = assigns[name]
        assert isinstance(value, (int, float)), f"{name} is not a numeric default"
        assert value > 0, f"{name} default must be positive, got {value}"


def test_mount_style_default_is_supported():
    assigns = _module_level_assignments()
    assert assigns["mount_style"] in SUPPORTED_MOUNTS


def test_stl_is_a_nonempty_binary_mesh():
    data = PERCH_STL.read_bytes()
    assert len(data) >= 84, "binary STL is shorter than its header"
    # A binary STL must not begin with the ASCII "solid" keyword.
    assert not data[:5].lower().startswith(b"solid"), "perch.stl looks like ASCII STL"
    (triangle_count,) = struct.unpack("<I", data[80:84])
    assert triangle_count > 0, "perch.stl has no triangles"
    assert len(data) == 84 + triangle_count * 50, "perch.stl size does not match its triangle count"


def test_3mf_is_a_valid_model_package():
    assert zipfile.is_zipfile(PERCH_3MF), "perch.3mf is not a zip container"
    with zipfile.ZipFile(PERCH_3MF) as package:
        names = package.namelist()
        assert "[Content_Types].xml" in names, "perch.3mf is missing [Content_Types].xml"
        assert "3D/3dmodel.model" in names, "perch.3mf is missing 3D/3dmodel.model"
        model_xml = package.read("3D/3dmodel.model").decode("utf-8", "replace")
    assert "<vertex" in model_xml and "<triangle" in model_xml, "perch.3mf model has no mesh"


def _stl_vertices():
    """Every triangle vertex in the binary STL as (x, y, z) tuples."""
    data = PERCH_STL.read_bytes()
    (count,) = struct.unpack("<I", data[80:84])
    vertices = []
    for i in range(count):
        base = 84 + i * 50
        for j in range(3):
            start = base + 12 + j * 12
            vertices.append(struct.unpack("<3f", data[start : start + 12]))
    return vertices


def _default_bounding_box():
    """The wall-screw envelope derived from perch.py's default parameters.

    These must track the model: if a parameter changes and the STL is not
    re-exported, the measured box below stops matching and this test fails.
    """
    p = _module_level_assignments()
    length = (
        p["usb_a_plug_length"]
        + p["extension_cable_diameter"]
        + p["wall_thickness"]
        + p["adapter_body_length"]
        + 4 * p["clearance"]
    )
    # Outer trough width plus a screw ear on each side; each ear is
    # mount_ear_width + wall_thickness wide, centred mount_ear_width / 2 out.
    outer_w = p["adapter_body_width"] + 2 * p["clearance"] + 2 * p["wall_thickness"]
    width = outer_w + 2 * p["mount_ear_width"] + p["wall_thickness"]
    height = p["base_thickness"] + p["cradle_wall_height"]
    return length, width, height


def test_stl_bounding_box_matches_parameters():
    """The shipped STL's envelope must match the current default parameters, so
    a parameter edit without a re-export is caught here rather than at the
    printer."""
    xs = [v[0] for v in _stl_vertices()]
    ys = [v[1] for v in _stl_vertices()]
    zs = [v[2] for v in _stl_vertices()]
    measured = (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))
    expected = _default_bounding_box()
    for axis, got, want in zip("xyz", measured, expected):
        assert abs(got - want) < 0.3, (
            f"perch.stl {axis} span is {got:.2f} mm, expected {want:.2f} mm from "
            "perch.py defaults; re-export the STL after changing a parameter"
        )


def test_stl_countersink_opens_upward():
    """A countersink must widen toward the top face where the screw head sits.

    The screw ears are the only void in the part, so on a face plane the vertex
    nearest the hole axis is the hole rim. The rim radius at the top face
    (z = base_thickness) must be clearly larger than at the bottom face (z = 0).
    This fails if the countersink cone is inverted.
    """
    p = _module_level_assignments()
    floor = p["base_thickness"]
    outer_w = p["adapter_body_width"] + 2 * p["clearance"] + 2 * p["wall_thickness"]
    hole_y = outer_w / 2 + p["mount_ear_width"] * 0.55

    def rim_radius(z_target):
        radii = []
        for x, y, z in _stl_vertices():
            if abs(x) < 6 and hole_y - 6.5 < y < hole_y + 6.5 and abs(z - z_target) < 0.2:
                radii.append(math.hypot(x, y - hole_y))
        assert radii, f"no STL vertices found on the z={z_target:.1f} face near the hole"
        return min(radii)

    bottom_r = rim_radius(0.0)
    top_r = rim_radius(floor)
    assert top_r > bottom_r + 1.0, (
        f"countersink is inverted in perch.stl: top-face rim radius {top_r:.2f} "
        f"must exceed bottom-face rim radius {bottom_r:.2f}"
    )
