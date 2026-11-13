"""Guards for the perch's test-fit coupon and the printable-parts records.

Standard library only (build123d is not a repo dependency). The coupon is a thin
slice of the perch: the adapter pocket cross-section (floor plus both side walls)
and one screw ear with its countersunk M4 hole. These tests pin the shipped
coupon files to ``perch.py``'s parameters, and pin the intent files that record
the prior-art search, the reference hardware tier, and the measurable claims.
"""

import json
import math
import struct
import zipfile
from pathlib import Path

import pytest

PERCH_DIR = Path(__file__).resolve().parents[2] / "hardware" / "bt-antenna-perch"
PERCH_PY = PERCH_DIR / "perch.py"
COUPON_STL = PERCH_DIR / "perch-coupon.stl"
COUPON_3MF = PERCH_DIR / "perch-coupon.3mf"
PERCH_INTENT = PERCH_DIR / "perch.intent.json"
COUPON_INTENT = PERCH_DIR / "perch-coupon.intent.json"


def _params():
    import ast

    values = {}
    for node in ast.parse(PERCH_PY.read_text()).body:
        if isinstance(node, ast.Assign):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, SyntaxError):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    values[target.id] = value
    return values


def _stl_vertices(path):
    data = path.read_bytes()
    (count,) = struct.unpack("<I", data[80:84])
    assert len(data) == 84 + count * 50, f"{path.name} size does not match its triangles"
    out = []
    for i in range(count):
        base = 84 + i * 50
        for j in range(3):
            start = base + 12 + j * 12
            out.append(struct.unpack("<3f", data[start : start + 12]))
    return out


def _span(vertices):
    return tuple(
        max(v[a] for v in vertices) - min(v[a] for v in vertices) for a in range(3)
    )


def test_coupon_source_declares_its_parameters():
    p = _params()
    assert p["coupon_length"] > 0
    # A 15 minute print: keep the slice thin.
    assert p["coupon_length"] <= 16


def test_coupon_files_exist_and_are_well_formed():
    assert _stl_vertices(COUPON_STL)
    assert zipfile.is_zipfile(COUPON_3MF)
    with zipfile.ZipFile(COUPON_3MF) as package:
        assert "3D/3dmodel.model" in package.namelist()


def test_coupon_bounding_box_matches_parameters():
    p = _params()
    outer_w = p["adapter_body_width"] + 2 * p["clearance"] + 2 * p["wall_thickness"]
    expected = (
        p["coupon_length"],
        outer_w + p["mount_ear_width"] + p["wall_thickness"] / 2,
        p["base_thickness"] + p["cradle_wall_height"],
    )
    measured = _span(_stl_vertices(COUPON_STL))
    for axis, got, want in zip("xyz", measured, expected):
        assert abs(got - want) < 0.3, (
            f"perch-coupon.stl {axis} span {got:.2f} mm, expected {want:.2f} mm; "
            "re-export the coupon after changing a parameter"
        )


def test_coupon_countersink_opens_upward():
    p = _params()
    floor = p["base_thickness"]
    outer_w = p["adapter_body_width"] + 2 * p["clearance"] + 2 * p["wall_thickness"]
    hole_y = outer_w / 2 + p["mount_ear_width"] * 0.55
    verts = _stl_vertices(COUPON_STL)
    cx = (max(v[0] for v in verts) + min(v[0] for v in verts)) / 2

    def rim(z_target):
        radii = [
            math.hypot(x - cx, y - hole_y)
            for x, y, z in verts
            if abs(z - z_target) < 0.2 and math.hypot(x - cx, y - hole_y) < 6.5
        ]
        assert radii, f"no vertices near the hole at z={z_target}"
        return min(radii)

    assert rim(floor) > rim(0.0) + 1.0


def _load(path):
    return json.loads(path.read_text())


@pytest.mark.parametrize("path", [PERCH_INTENT, COUPON_INTENT])
def test_intent_files_have_the_pre_modeling_records(path):
    data = _load(path)
    art = data["prior_art"]
    assert art["decision"] == "design_new"
    assert art["reason"].strip()
    urls = " ".join(f["url"] for f in art["found"])
    assert "265738" in urls and "1191970" in urls
    (device,) = data["devices"]
    assert device["reference_tier"] == 5
    assert device["unverified"] is True
    assert device["source"].strip()
    assert any(i["check"]["type"] == "bbox" for i in data["intents"])


def test_intent_files_name_their_meshes_and_have_no_identifying_data():
    assert _load(PERCH_INTENT)["mesh"] == "perch.stl"
    assert _load(COUPON_INTENT)["mesh"] == "perch-coupon.stl"
    for path in (PERCH_INTENT, COUPON_INTENT):
        text = path.read_text()
        assert chr(0x2014) not in text


def test_coupon_pocket_width_is_body_plus_clearance_per_side():
    """The open-top pocket is the gap between the two wall tops at z = 16."""
    p = _params()
    top = p["base_thickness"] + p["cradle_wall_height"]
    ys = sorted({round(v[1], 2) for v in _stl_vertices(COUPON_STL) if abs(v[2] - top) < 0.01})
    inner = [y for y in ys if abs(y) < 14]
    width = max(inner) - min(inner)
    want = p["adapter_body_width"] + 2 * p["clearance"]
    assert abs(width - want) < 0.1, f"pocket {width:.2f} mm, expected {want:.2f} mm"


def test_sliced_project_files_ship_and_readme_has_a_print_sheet():
    for name in ("perch-coupon.gcode.3mf", "perch.gcode.3mf"):
        with zipfile.ZipFile(PERCH_DIR / "sliced" / name) as package:
            assert "Metadata/project_settings.config" in package.namelist()
    readme = (PERCH_DIR / "README.md").read_text()
    assert "## Print sheet" in readme and "What to check on the coupon" in readme
    assert chr(0x2014) not in readme
