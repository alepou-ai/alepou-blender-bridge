"""Real-Blender regression for transform-only Spatial mesh identity stability."""

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


def make_scene(stand_height: float) -> spatial.Scene:
    scene = spatial.Scene("stable_mesh_update_regression", units="m")
    base = scene.cylinder("base", radius=1.2, length=0.2, center=(0, 0, 0.1), segments=64)
    stand = scene.cylinder(
        "stand",
        radius=0.12,
        length=stand_height,
        center=(0, 0, 0.2 + stand_height / 2.0),
        segments=48,
    )
    head_frame = scene.frame("head_frame", origin=(0, 0, 0.2 + stand_height))
    motor = scene.cylinder(
        "motor",
        radius=0.55,
        length=0.7,
        axis="Y",
        center=(0, 0, 0.45),
        frame=head_frame,
        segments=72,
    )
    cage = scene.torus(
        "cage",
        major_radius=1.0,
        minor_radius=0.04,
        axis="Y",
        center=(0, -0.4, 0.45),
        frame=head_frame,
        major_segments=96,
        minor_segments=12,
    )
    head = scene.assembly("head", frame=head_frame, children=(motor, cage))
    scene.assembly("asset", children=(base, stand, head))
    return scene


def apply(scene: spatial.Scene) -> dict[str, object]:
    compiled = compile_script(build_compile_plan(scene.resolve(), mode="update"))
    namespace: dict[str, object] = {"__name__": "__spatial_stable_mesh_update_regression__"}
    exec(compile(compiled.source, "<spatial-stable-mesh-update-regression>", "exec"), namespace, namespace)
    return namespace["RESULT"]  # type: ignore[return-value]


def close(actual: float, expected: float, label: str) -> None:
    if abs(actual - expected) > 1e-6:
        raise AssertionError(f"{label}: expected {expected}, got {actual}")


bpy.ops.wm.read_factory_settings(use_empty=True)

initial = apply(make_scene(2.0))
base = bpy.data.objects["SP_base"]
stand = bpy.data.objects["SP_stand"]
motor = bpy.data.objects["SP_motor"]
cage = bpy.data.objects["SP_cage"]
initial_data = {
    "base": base.data.as_pointer(),
    "stand": stand.data.as_pointer(),
    "motor": motor.data.as_pointer(),
    "cage": cage.data.as_pointer(),
}
initial_motor_z = float(motor.matrix_world.translation.z)
initial_cage_z = float(cage.matrix_world.translation.z)
finish = bpy.data.materials.new("ExternalMotorFinish")
motor.data.materials.append(finish)
# Simulate a Bridge 0.3.0-authored scene. The upgraded compiler must derive
# legacy geometry identity from stored authored parameters and avoid a
# one-time migration rebuild.
for obj in (base, stand, motor, cage):
    obj.pop("spatial.geometry_fingerprint", None)

updated = apply(make_scene(2.3))
base_after = bpy.data.objects["SP_base"]
stand_after = bpy.data.objects["SP_stand"]
motor_after = bpy.data.objects["SP_motor"]
cage_after = bpy.data.objects["SP_cage"]

if base_after.data.as_pointer() != initial_data["base"]:
    raise AssertionError("Unchanged base mesh datablock was replaced")
if stand_after.data.as_pointer() == initial_data["stand"]:
    raise AssertionError("Changed stand geometry did not replace its mesh datablock")
if motor_after.data.as_pointer() != initial_data["motor"]:
    raise AssertionError("Transform-only motor update replaced its mesh datablock")
if cage_after.data.as_pointer() != initial_data["cage"]:
    raise AssertionError("Transform-only cage update replaced its mesh datablock")
if list(motor_after.data.materials) != [finish]:
    raise AssertionError("Transform-only update lost external material finishing")

close(float(motor_after.matrix_world.translation.z) - initial_motor_z, 0.3, "motor Z delta")
close(float(cage_after.matrix_world.translation.z) - initial_cage_z, 0.3, "cage Z delta")
if updated["meshReplaced"] != ["stand"]:
    raise AssertionError(f"Unexpected mesh replacements: {updated['meshReplaced']}")
if set(updated["meshPreserved"]) != {"motor", "cage"}:
    raise AssertionError(f"Unexpected transform-only mesh preservation: {updated['meshPreserved']}")

repeated = apply(make_scene(2.3))
if repeated["updated"] or repeated["meshReplaced"] or repeated["meshPreserved"]:
    raise AssertionError(f"Idempotent update unexpectedly changed entities: {repeated}")

print(
    "SPATIAL_STABLE_MESH_UPDATE_REGRESSION="
    + json.dumps(
        {
            "initial": initial,
            "updated": updated,
            "repeated": repeated,
            "standHeight": stand_after.dimensions.z,
            "motorMaterial": motor_after.data.materials[0].name,
        },
        sort_keys=True,
    )
)
