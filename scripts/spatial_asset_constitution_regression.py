"""Real-Blender regression for reusable Spatial asset origin constitution."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import bpy


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "extension"))

import spatial
from alepou_blender_bridge import state
from spatial_blender import compile_script
from spatial_core import build_compile_plan


def make_scene(radius: float = 138) -> spatial.Scene:
    scene = spatial.Scene("asset_constitution_regression", units="mm")
    frame = scene.frame("base_frame", origin=(-1050, 0, 780))
    base = scene.cylinder(
        "base_body",
        radius=radius,
        length=360,
        axis="Z",
        segments=64,
        frame=frame,
        center=(0, 0, -560),
    )
    base.anchor("asset_origin", position=(0, 0, -180), direction=(1, 0, 0), up=(0, 0, 1))
    root = scene.assembly("lamp", children=(base,))
    scene.asset(
        root=root,
        origin="base_body.asset_origin",
        center_axes=("X", "Y"),
        ground_axis="Z",
        up="Z",
        forward="X",
    )
    return scene


def apply(scene: spatial.Scene) -> dict[str, object]:
    compiled = compile_script(build_compile_plan(scene.resolve(), mode="update"))
    namespace: dict[str, object] = {"__name__": "__spatial_asset_constitution_regression__"}
    exec(compile(compiled.source, "<spatial-asset-constitution-regression>", "exec"), namespace, namespace)
    return namespace["RESULT"]  # type: ignore[return-value]


bpy.ops.wm.read_factory_settings(use_empty=True)
initial = apply(make_scene())
root = bpy.data.objects["SP_lamp"]
base = bpy.data.objects["SP_base_body"]
root_pointer = root.as_pointer()
base_pointer = base.as_pointer()

if tuple(round(float(value), 8) for value in root.matrix_world.translation) != (0.0, 0.0, 0.0):
    raise AssertionError(f"Asset root is not at the world origin: {tuple(root.matrix_world.translation)}")
bounds = state.bounds_for_object(base)["authored"]["world"]
if bounds["center"] != [0.0, 0.0, 0.18]:
    raise AssertionError(bounds)
if bounds["min"][2] != 0.0:
    raise AssertionError(bounds)

summary = state.scene_summary()
asset = summary["spatialAsset"]
if asset["root"] != "lamp" or asset["originWorld"] != [0.0, 0.0, 0.0]:
    raise AssertionError(asset)
if not bool(root.get("spatial.asset_root")):
    raise AssertionError("Asset root marker is missing")

updated = apply(make_scene(140))
if bpy.data.objects["SP_lamp"].as_pointer() != root_pointer:
    raise AssertionError("Asset root object identity changed")
if bpy.data.objects["SP_base_body"].as_pointer() != base_pointer:
    raise AssertionError("Asset base object identity changed")
if updated["updated"] != ["base_body"]:
    raise AssertionError(updated)

print(
    "SPATIAL_ASSET_CONSTITUTION_REGRESSION="
    + json.dumps(
        {
            "asset": asset,
            "bounds": bounds,
            "initial": initial,
            "updated": updated,
        },
        sort_keys=True,
    )
)
