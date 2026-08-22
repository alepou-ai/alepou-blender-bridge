"""Prove diagnostic framing follows target scale without a world-unit floor."""

from __future__ import annotations

import json
import importlib
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
import bpy

if os.environ.get("ALEPOU_TEST_INSTALLED_EXTENSION") == "true":
    capture = importlib.import_module("bl_ext.user_default.alepou_blender_bridge.capture")
else:
    sys.path.insert(0, str(REPO / "extension"))
    capture = importlib.import_module("alepou_blender_bridge.capture")


def proxy(name: str, dimensions: tuple[float, float, float]):
    bpy.ops.mesh.primitive_cube_add()
    value = bpy.context.object
    value.name = name
    value.dimensions = dimensions
    bpy.context.view_layer.update()
    return value


bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)

phone = proxy("PhoneScaleProxy", (0.2032, 0.225425, 0.085725))
authored_scale = tuple(phone.scale)
authored_dimensions = tuple(phone.dimensions)

with tempfile.TemporaryDirectory(prefix="alepou-diagnostic-scale-") as directory:
    root = Path(directory)
    front = capture.diagnostic_capture(
        {
            "target": {"object": phone.name},
            "view": "front",
            "projection": "orthographic",
            "mode": "studio",
            "resolution": 128,
        },
        root,
        "small-front",
    )
    custom = capture.diagnostic_capture(
        {
            "target": {"object": phone.name},
            "direction": [1.0, -1.0, 0.6],
            "projection": "perspective",
            "mode": "clay",
            "resolution": 128,
        },
        root,
        "small-custom",
    )

    assert Path(front["path"]).is_file()
    assert Path(custom["path"]).is_file()
    assert 0.20 < front["orthoScale"] < 0.30, front
    assert front["cameraDistance"] < 1.0, front
    assert custom["cameraDistance"] < 1.0, custom
    assert custom["view"] == "custom", custom
    assert tuple(phone.scale) == authored_scale
    assert tuple(phone.dimensions) == authored_dimensions

    large_phone = proxy("LargePhoneScaleProxy", tuple(component * 10.0 for component in authored_dimensions))
    large = capture.diagnostic_capture(
        {
            "target": {"object": large_phone.name},
            "view": "front",
            "projection": "orthographic",
            "mode": "studio",
            "resolution": 128,
        },
        root,
        "large-front",
    )
    ratio = large["orthoScale"] / front["orthoScale"]
    assert abs(ratio - 10.0) < 1e-5, (front, large)

    print(json.dumps({
        "status": "passed",
        "smallOrthoScale": front["orthoScale"],
        "smallCameraDistance": front["cameraDistance"],
        "customPerspectiveDistance": custom["cameraDistance"],
        "largeToSmallFramingRatio": ratio,
        "authoredScalePreserved": tuple(phone.scale) == authored_scale and tuple(phone.dimensions) == authored_dimensions,
    }, indent=2))
