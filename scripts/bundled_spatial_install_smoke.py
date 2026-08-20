"""Exercise bundled Spatial through a clean installed Blender extension."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from pathlib import Path

import bpy


def arguments() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args(values)


ARGS = arguments()
project = Path(ARGS.project).resolve()
configured = Path(os.environ.get("ALEPOU_BLENDER_PROJECT_ROOT", "")).resolve()
assert configured == project, f"Expected ALEPOU_BLENDER_PROJECT_ROOT={project}, got {configured}"

service_module = importlib.import_module("bl_ext.user_default.alepou_blender_bridge.service")
protocol = importlib.import_module("bl_ext.user_default.alepou_blender_bridge.protocol")
policy = importlib.import_module("bl_ext.user_default.alepou_blender_bridge.spatial_policy")
runtime = importlib.import_module("bl_ext.user_default.alepou_blender_bridge.spatial_runtime")
bridge = service_module.get_service()
root = bridge.root()
assert root is not None
protocol.ensure_layout(root)
protocol.atomic_write_json(
    policy.policy_path(root),
    {"schemaVersion": 1, "mode": "opt_in", "fallbackAllowed": False},
)

bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)

source = """import spatial
scene = spatial.Scene('bundled_lamp', units='mm')
base = scene.box('base', size=(240, 240, 20), center=(0, 0, 10), bevel=spatial.Bevel(6, 3))
arm = scene.cylinder('arm', radius=12, length=400, axis='Z')
arm.after(base, gap=10, axis='Z', id='arm_after_base')
scene.assembly('lamp', children=[base, arm])
"""

spatial_request = {
    "schemaVersion": 1,
    "commandId": "bundled-spatial-build",
    "representation": {"kind": "spatial", "version": "0.1", "fallbackAllowed": False},
    "actions": [
        {
            "action": "spatial.execute",
            "sourceFormat": "python",
            "compileMode": "update",
            "source": source,
        },
        {"action": "state.refresh"},
    ],
}
protocol.atomic_write_json(root / "commands" / "pending" / "bundled-spatial-build.json", spatial_request)
bridge._process_next(root, query=False)
spatial_result = protocol.read_json(root / "commands" / "applied" / "bundled-spatial-build.json")
assert spatial_result["status"] == "applied", spatial_result
for name in ("SP_base", "SP_arm", "SP_lamp"):
    assert bpy.data.objects.get(name) is not None, name
for name in (
    "action-00.spatial.py",
    "action-00.spatial-source.normalized.json",
    "action-00.spatial-resolved.json",
    "action-00.spatial-plan.json",
    "action-00.spatial.generated.py",
):
    assert (root / "runs" / "bundled-spatial-build" / name).is_file(), name

why_request = {
    "schemaVersion": 1,
    "commandId": "bundled-spatial-why",
    "representation": {"kind": "spatial", "version": "0.1", "fallbackAllowed": False},
    "actions": [
        {
            "action": "spatial.why",
            "sourceFormat": "python",
            "source": source,
            "selector": "arm.center.z",
        }
    ],
}
protocol.atomic_write_json(root / "queries" / "pending" / "bundled-spatial-why.json", why_request)
bridge._process_next(root, query=True)
why_result = protocol.read_json(root / "queries" / "results" / "bundled-spatial-why.json")
why = why_result["outputs"][0]["value"]["value"]
assert why["source"] == "relation:arm_after_base", why

raw_request = {
    "schemaVersion": 1,
    "commandId": "bundled-raw-compatible",
    "representation": {"kind": "raw_bpy"},
    "actions": [
        {
            "action": "script.execute",
            "source": "import bpy\nbpy.ops.mesh.primitive_uv_sphere_add(radius=0.05)\nbpy.context.object.name='Raw_Detail'",
        }
    ],
}
protocol.atomic_write_json(root / "commands" / "pending" / "bundled-raw-compatible.json", raw_request)
bridge._process_next(root, query=False)
raw_result = protocol.read_json(root / "commands" / "applied" / "bundled-raw-compatible.json")
assert raw_result["status"] == "applied", raw_result

protocol.atomic_write_json(
    policy.policy_path(root),
    {"schemaVersion": 1, "mode": "required", "fallbackAllowed": False},
)
rejected_request = {**raw_request, "commandId": "bundled-required-reject"}
protocol.atomic_write_json(root / "commands" / "pending" / "bundled-required-reject.json", rejected_request)
bridge._process_next(root, query=False)
rejected = protocol.read_json(root / "commands" / "rejected" / "bundled-required-reject.json")
assert rejected["status"] == "rejected", rejected

output = Path(ARGS.output).resolve()
output.parent.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False)
print(
    "BUNDLED_SPATIAL_SMOKE="
    + json.dumps(
        {
            "status": "passed",
            "runtime": runtime.describe(),
            "spatialModule": importlib.import_module("spatial").__file__,
            "objects": sorted(obj.name for obj in bpy.data.objects),
            "why": why,
            "rawCompatible": True,
            "requiredRejectedRaw": True,
            "output": str(output),
        },
        sort_keys=True,
    )
)
