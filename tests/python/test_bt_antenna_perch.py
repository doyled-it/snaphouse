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
