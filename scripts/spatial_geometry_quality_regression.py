"""Real-Blender regression for Spatial tessellation and shading vocabulary."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import bpy


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import spatial
from spatial_blender import compile_script
from spatial_core import build_compile_plan


def make_scene(mechanical_segments: int) -> spatial.Scene:
    scene = spatial.Scene("quality_regression", units="m")
    scene.cylinder("faceted", radius=1, length=0.5, segments=8, shading="flat", center=(-2, 0, 0))
    scene.cylinder(
        "mechanical",
        radius=1,
        length=0.5,
        segments=mechanical_segments,
        shading=spatial.Shading.smooth_by_angle(30),
        center=(2, 0, 0),
    )
    scene.sphere("round", radius=1, segments=32, rings=16, shading="smooth", center=(0, 3, 0))
    return scene


def apply(scene: spatial.Scene) -> dict[str, object]:
    compiled = compile_script(build_compile_plan(scene.resolve(), mode="update"))
    namespace: dict[str, object] = {"__name__": "__spatial_quality_regression__"}
    exec(compile(compiled.source, "<spatial-quality-regression>", "exec"), namespace, namespace)
    return namespace["RESULT"]  # type: ignore[return-value]


bpy.ops.wm.read_factory_settings(use_empty=True)
initial = apply(make_scene(48))
faceted = bpy.data.objects["SP_faceted"]
mechanical = bpy.data.objects["SP_mechanical"]
round_object = bpy.data.objects["SP_round"]
mechanical_pointer = mechanical.as_pointer()

if len(faceted.data.vertices) != 16:
    raise AssertionError(f"Eight-segment cylinder has {len(faceted.data.vertices)} vertices")
if any(polygon.use_smooth for polygon in faceted.data.polygons):
    raise AssertionError("Flat shading produced smooth polygons")
if len(mechanical.data.vertices) != 96:
    raise AssertionError(f"48-segment cylinder has {len(mechanical.data.vertices)} vertices")
if not all(polygon.use_smooth for polygon in mechanical.data.polygons):
    raise AssertionError("Smooth-by-angle did not smooth polygons")
sharp_edges = sum(1 for edge in mechanical.data.edges if edge.use_edge_sharp)
if sharp_edges != 96:
    raise AssertionError(f"Expected 96 sharp cap edges, found {sharp_edges}")
if not all(polygon.use_smooth for polygon in round_object.data.polygons):
    raise AssertionError("Smooth sphere has flat polygons")

parameters = json.loads(str(mechanical["spatial.parameters"]))
if parameters["segments"] != 48:
    raise AssertionError(parameters)
if parameters["shading"] != {"angleDegrees": 30.0, "mode": "smooth_by_angle"}:
    raise AssertionError(parameters)

updated = apply(make_scene(64))
mechanical_after = bpy.data.objects["SP_mechanical"]
if mechanical_after.as_pointer() != mechanical_pointer:
    raise AssertionError("Quality update replaced the stable Blender object")
if len(mechanical_after.data.vertices) != 128:
    raise AssertionError(f"64-segment cylinder has {len(mechanical_after.data.vertices)} vertices")
if updated["updated"] != ["mechanical"]:
    raise AssertionError(updated)

print(
    "SPATIAL_GEOMETRY_QUALITY_REGRESSION="
    + json.dumps(
        {
            "facetedVertices": len(faceted.data.vertices),
            "mechanicalVertices": len(mechanical_after.data.vertices),
            "sharpEdges": sum(1 for edge in mechanical_after.data.edges if edge.use_edge_sharp),
            "initial": initial,
            "updated": updated,
        },
        sort_keys=True,
    )
)
