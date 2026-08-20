"""Run the minimal Spatial build/update/coexistence loop in real Blender."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "examples" / "spatial"))

import bpy

from spatial_blender import compile_script
from spatial_core import build_compile_plan
from triple_quad import make_scene


def arguments() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    return parser.parse_args(values)


def apply_model(scene, *, force: bool = False) -> tuple[object, object]:
    resolved = scene.resolve()
    plan = build_compile_plan(resolved, mode="update", force=force)
    compiled = compile_script(plan)
    namespace = {"__name__": "__spatial_blender_smoke__", "__file__": "<generated-spatial>"}
    exec(compile(compiled.source, "<generated-spatial>", "exec"), namespace, namespace)
    return resolved, compiled


def apply_scene(chamber_length: float, *, force: bool = False) -> tuple[object, object]:
    return apply_model(make_scene(chamber_length), force=force)


ARGS = arguments()
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)

bpy.ops.mesh.primitive_cube_add(size=0.1, location=(2, 2, 0.05))
raw = bpy.context.object
raw.name = "Raw_User_Object"
raw_pointer = raw.as_pointer()

first, first_compiled = apply_scene(220)
assert bpy.data.objects.get("Raw_User_Object") is raw
assert raw.as_pointer() == raw_pointer
assert not bool(raw.get("spatial.managed"))
assert len([obj for obj in bpy.data.objects if obj.get("spatial.scene_id") == "triple_quad_demo"]) == 12
assert len([obj for obj in bpy.data.objects if str(obj.get("spatial.entity_id", "")).startswith("Q1_rods[")]) == 4

chamber = next(obj for obj in bpy.data.objects if obj.get("spatial.entity_id") == "collision_cell")
q3 = next(obj for obj in bpy.data.objects if obj.get("spatial.entity_id") == "Q3_rods")
chamber_pointer = chamber.as_pointer()
q3_pointer = q3.as_pointer()
q3_before = q3.matrix_world.translation.x

second, second_compiled = apply_scene(450)
chamber_after = next(obj for obj in bpy.data.objects if obj.get("spatial.entity_id") == "collision_cell")
q3_after = next(obj for obj in bpy.data.objects if obj.get("spatial.entity_id") == "Q3_rods")
assert chamber_after.as_pointer() == chamber_pointer
assert q3_after.as_pointer() == q3_pointer
assert q3_after.matrix_world.translation.x > q3_before
assert bpy.data.objects.get("Raw_User_Object") is raw
assert raw.as_pointer() == raw_pointer
assert second.why("Q3_rods.center.x")["source"] == "relation:q3_after_cell"

q1_child = next(obj for obj in bpy.data.objects if obj.get("spatial.entity_id") == "Q1_rods[0]")
q1_child_pointer = q1_child.as_pointer()
q1_child.location.x += 0.01
bpy.context.view_layer.update()
external_world_x = q1_child.matrix_world.translation.x
apply_scene(450)
assert q1_child.as_pointer() == q1_child_pointer
assert abs(q1_child.matrix_world.translation.x - external_world_x) < 1e-6
assert bool(q1_child.get("spatial.external_dirty"))

conflicting = make_scene(450)
conflicting.arrays["Q1_rods"].parameters["radius"] = 65.0
conflict_detected = False
try:
    apply_model(conflicting)
except RuntimeError as error:
    conflict_detected = "EXTERNAL_MODIFICATION_CONFLICT" in str(error)
assert conflict_detected
forced, forced_compiled = apply_model(conflicting, force=True)
assert q1_child.as_pointer() == q1_child_pointer
assert not bool(q1_child.get("spatial.external_dirty", False))
assert bpy.data.objects.get("Raw_User_Object") is raw

output = Path(ARGS.output).resolve()
output.parent.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False)

print(
    json.dumps(
        {
            "status": "passed",
            "output": str(output),
            "managedEntities": len([obj for obj in bpy.data.objects if obj.get("spatial.scene_id") == "triple_quad_demo"]),
            "rawObjectPreserved": True,
            "stableChamberObject": True,
            "stableQ3Object": True,
            "externalChangePreservedWhenUnrelated": True,
            "conflictingExternalChangeRejected": conflict_detected,
            "forcedConflictResolutionRecorded": True,
            "q3BeforeMeters": q3_before,
            "q3AfterMeters": q3_after.matrix_world.translation.x,
            "firstGeneratedScriptSha256": first_compiled.source_hash,
            "secondGeneratedScriptSha256": second_compiled.source_hash,
            "forcedGeneratedScriptSha256": forced_compiled.source_hash,
            "why": second.why("Q3_rods.center.x"),
        },
        indent=2,
        sort_keys=True,
    )
)
