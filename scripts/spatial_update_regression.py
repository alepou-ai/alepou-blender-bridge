"""Real-Blender regression for Spatial hierarchy updates and material retention."""

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


def make_scene(*, parent_x: float, child_length: float) -> spatial.Scene:
    scene = spatial.Scene("hierarchy_regression", units="m")
    moving_frame = scene.frame("moving_frame", origin=(parent_x, 0, 0))
    child = scene.box(
        "a_child",
        size=(child_length, 1, 1),
        frame=moving_frame,
        center=(child_length / 2, 0, 0),
    )
    # The child deliberately sorts before its moving parent. This reproduced the
    # desk-lamp double-transform defect in the original backend.
    scene.assembly("z_parent", frame=moving_frame, children=(child,))
    return scene


def apply(scene: spatial.Scene) -> dict[str, object]:
    compiled = compile_script(build_compile_plan(scene.resolve(), mode="update"))
    namespace: dict[str, object] = {"__name__": "__spatial_regression__"}
    exec(compile(compiled.source, "<spatial-regression>", "exec"), namespace, namespace)
    return namespace["RESULT"]  # type: ignore[return-value]


def close(actual: float, expected: float, label: str) -> None:
    if abs(actual - expected) > 1e-6:
        raise AssertionError(f"{label}: expected {expected}, got {actual}")


bpy.ops.wm.read_factory_settings(use_empty=True)

initial = apply(make_scene(parent_x=0.0, child_length=2.0))
child = bpy.data.objects["SP_a_child"]
parent = bpy.data.objects["SP_z_parent"]
child_pointer = child.as_pointer()
material = bpy.data.materials.new("ExternalFinish")
child.data.materials.append(material)

close(parent.matrix_world.translation.x, 0.0, "initial parent X")
close(child.matrix_world.translation.x, 1.0, "initial child X")

updated = apply(make_scene(parent_x=2.0, child_length=3.0))
child_after = bpy.data.objects["SP_a_child"]
parent_after = bpy.data.objects["SP_z_parent"]

if child_after.as_pointer() != child_pointer:
    raise AssertionError("Spatial update replaced the stable Blender object")
if child_after.parent is not parent_after:
    raise AssertionError("Spatial update did not restore the desired parent")
close(parent_after.matrix_world.translation.x, 2.0, "updated parent X")
close(child_after.matrix_world.translation.x, 3.5, "updated child X")
if list(child_after.data.materials) != [material]:
    raise AssertionError("Spatial mesh replacement lost external material slots")
if updated["updated"] != ["z_parent", "a_child"]:
    # Runtime result order is parent-before-child by design.
    raise AssertionError(f"Unexpected update order: {updated['updated']}")

repeated = apply(make_scene(parent_x=2.0, child_length=3.0))
close(child_after.matrix_world.translation.x, 3.5, "repeated child X")
if repeated["updated"]:
    raise AssertionError(f"Idempotent update unexpectedly changed entities: {repeated['updated']}")
if list(child_after.data.materials) != [material]:
    raise AssertionError("Idempotent update lost external material slots")

print(
    "SPATIAL_UPDATE_REGRESSION="
    + json.dumps(
        {
            "initial": initial,
            "updated": updated,
            "repeated": repeated,
            "parentWorldX": parent_after.matrix_world.translation.x,
            "childWorldX": child_after.matrix_world.translation.x,
            "material": child_after.data.materials[0].name,
        },
        sort_keys=True,
    )
)
