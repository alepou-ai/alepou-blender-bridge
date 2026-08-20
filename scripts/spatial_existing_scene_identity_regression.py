"""Verify that reapplying Spatial source preserves mesh identity in an existing blend."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
for module_name in list(sys.modules):
    if module_name == "spatial" or module_name.startswith(("spatial_core", "spatial_blender")):
        del sys.modules[module_name]

from spatial_blender import compile_script
from spatial_core import build_compile_plan


def arguments_after_separator() -> list[str]:
    return sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []


parser = argparse.ArgumentParser()
parser.add_argument("--source", required=True, type=Path)
args = parser.parse_args(arguments_after_separator())

namespace: dict[str, object] = {"__name__": "__spatial_existing_scene_source__"}
source = args.source.read_text(encoding="utf-8")
exec(compile(source, str(args.source), "exec"), namespace, namespace)
scene = namespace.get("scene")
if scene is None:
    raise AssertionError(f"Spatial source did not define `scene`: {args.source}")

managed_meshes = {
    str(obj.get("spatial.entity_id")): obj.data.as_pointer()
    for obj in bpy.data.objects
    if obj.get("spatial.managed") and obj.type == "MESH" and obj.data is not None
}
if not managed_meshes:
    raise AssertionError("Loaded blend contains no Spatial-managed meshes")

compiled = compile_script(build_compile_plan(scene.resolve(), mode="update"))
runtime: dict[str, object] = {"__name__": "__spatial_existing_scene_regression__"}
exec(compile(compiled.source, "<spatial-existing-scene-regression>", "exec"), runtime, runtime)
result = runtime["RESULT"]

managed_after = {
    str(obj.get("spatial.entity_id")): obj.data.as_pointer()
    for obj in bpy.data.objects
    if obj.get("spatial.managed") and obj.type == "MESH" and obj.data is not None
}
if managed_after.keys() != managed_meshes.keys():
    raise AssertionError("Spatial-managed mesh membership changed during source reapplication")
replaced = [
    entity_id
    for entity_id, pointer in managed_meshes.items()
    if managed_after[entity_id] != pointer
]
if replaced:
    raise AssertionError(f"Idempotent source reapplication replaced mesh datablocks: {replaced}")
if result["meshReplaced"]:
    raise AssertionError(f"Runtime reported unexpected mesh replacements: {result['meshReplaced']}")

second_runtime: dict[str, object] = {"__name__": "__spatial_existing_scene_repeat__"}
exec(compile(compiled.source, "<spatial-existing-scene-repeat>", "exec"), second_runtime, second_runtime)
repeated = second_runtime["RESULT"]
if repeated["updated"] or repeated["meshReplaced"] or repeated["meshPreserved"]:
    raise AssertionError(f"Second source reapplication was not idempotent: {repeated}")

print(
    "SPATIAL_EXISTING_SCENE_IDENTITY_REGRESSION="
    + json.dumps(
        {
            "managedMeshes": len(managed_meshes),
            "first": result,
            "second": repeated,
        },
        sort_keys=True,
    )
)
