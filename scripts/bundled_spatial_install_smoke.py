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
assert protocol.BRIDGE_VERSION == "0.3.2", protocol.BRIDGE_VERSION
bridge = service_module.get_service()
bridge.authority_initialized = True
bridge.session_trust_mode = "trusted_development"
root = bridge.root()
assert root is not None
target = {"instanceId": bridge.instance_id}
protocol.ensure_layout(root)
protocol.atomic_write_json(
    policy.policy_path(bridge.policy_root()),
    {"schemaVersion": 1, "mode": "opt_in", "fallbackAllowed": False},
)

bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)

source = """import spatial
scene = spatial.Scene('bundled_lamp', units='mm')
base = scene.box('base', size=(240, 240, 20), center=(0, 0, 10), bevel=spatial.Bevel(6, 3))
base.anchor('asset_origin', position=(0, 0, -10), direction=(1, 0, 0), up=(0, 0, 1))
arm = scene.cylinder('arm', radius=12, length=400, axis='Z', segments=72, shading=spatial.Shading.smooth_by_angle(30))
arm.after(base, gap=10, axis='Z', id='arm_after_base')
lamp = scene.assembly('lamp', children=[base, arm])
scene.asset(root=lamp, origin='base.asset_origin', center_axes=('X', 'Y'), ground_axis='Z', up='Z', forward='X')
"""

spatial_request = {
    "schemaVersion": 1,
    "commandId": "bundled-spatial-build",
    "target": target,
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
bridge._process_next(root, query=False, require_target=True)
spatial_result = protocol.read_json(root / "commands" / "applied" / "bundled-spatial-build.json")
assert spatial_result["status"] == "applied", spatial_result
for name in ("SP_base", "SP_arm", "SP_lamp"):
    assert bpy.data.objects.get(name) is not None, name
assert len(bpy.data.objects["SP_arm"].data.vertices) == 144
asset_state = json.loads(str(bpy.context.scene["spatial.asset"]))
assert asset_state["root"] == "lamp", asset_state
assert asset_state["originWorld"] == [0.0, 0.0, 0.0], asset_state
for name in (
    "action-00.spatial.py",
    "action-00.spatial-source.normalized.json",
    "action-00.spatial-resolved.json",
    "action-00.spatial-plan.json",
    "action-00.spatial.generated.py",
):
    assert (root / "runs" / "bundled-spatial-build" / name).is_file(), name

base_mesh_pointer = bpy.data.objects["SP_base"].data.as_pointer()
arm_mesh_pointer = bpy.data.objects["SP_arm"].data.as_pointer()
arm_z_before = float(bpy.data.objects["SP_arm"].matrix_world.translation.z)
external_finish = bpy.data.materials.new("BundledExternalFinish")
bpy.data.objects["SP_arm"].data.materials.append(external_finish)
updated_source = source.replace(
    "size=(240, 240, 20), center=(0, 0, 10)",
    "size=(240, 240, 30), center=(0, 0, 15)",
).replace("position=(0, 0, -10)", "position=(0, 0, -15)")
assert updated_source != source
update_request = {
    **spatial_request,
    "commandId": "bundled-spatial-stable-update",
    "actions": [
        {
            **spatial_request["actions"][0],
            "source": updated_source,
        }
    ],
}
protocol.atomic_write_json(root / "commands" / "pending" / "bundled-spatial-stable-update.json", update_request)
bridge._process_next(root, query=False, require_target=True)
update_result = protocol.read_json(root / "commands" / "applied" / "bundled-spatial-stable-update.json")
assert update_result["status"] == "applied", update_result
assert bpy.data.objects["SP_base"].data.as_pointer() != base_mesh_pointer
assert bpy.data.objects["SP_arm"].data.as_pointer() == arm_mesh_pointer
assert list(bpy.data.objects["SP_arm"].data.materials) == [external_finish]
arm_z_after = float(bpy.data.objects["SP_arm"].matrix_world.translation.z)
assert abs((arm_z_after - arm_z_before) - 0.01) < 1e-6, (arm_z_before, arm_z_after)
update_stdout = update_result["outputs"][0]["value"]["execution"]["stdout"]
result_line = next(line for line in update_stdout.splitlines() if line.startswith("SPATIAL_RESULT_JSON="))
stable_update = json.loads(result_line.removeprefix("SPATIAL_RESULT_JSON="))
assert stable_update["meshReplaced"] == ["base"], stable_update
assert stable_update["meshPreserved"] == ["arm"], stable_update

why_request = {
    "schemaVersion": 1,
    "commandId": "bundled-spatial-why",
    "target": target,
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
bridge._process_next(root, query=True, require_target=True)
why_result = protocol.read_json(root / "queries" / "results" / "bundled-spatial-why.json")
why = why_result["outputs"][0]["value"]["value"]
assert why["source"] == "relation:arm_after_base", why

raw_request = {
    "schemaVersion": 1,
    "commandId": "bundled-raw-compatible",
    "target": target,
    "representation": {"kind": "raw_bpy"},
    "actions": [
        {
            "action": "script.execute",
            "source": "import bpy\nbpy.ops.mesh.primitive_uv_sphere_add(radius=0.05)\nbpy.context.object.name='Raw_Detail'",
        }
    ],
}
protocol.atomic_write_json(root / "commands" / "pending" / "bundled-raw-compatible.json", raw_request)
bridge._process_next(root, query=False, require_target=True)
raw_result = protocol.read_json(root / "commands" / "applied" / "bundled-raw-compatible.json")
assert raw_result["status"] == "applied", raw_result

protocol.atomic_write_json(
    policy.policy_path(bridge.policy_root()),
    {"schemaVersion": 1, "mode": "required", "fallbackAllowed": False},
)
rejected_request = {**raw_request, "commandId": "bundled-required-reject"}
protocol.atomic_write_json(root / "commands" / "pending" / "bundled-required-reject.json", rejected_request)
bridge._process_next(root, query=False, require_target=True)
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
            "bridgeVersion": protocol.BRIDGE_VERSION,
            "spatialModule": importlib.import_module("spatial").__file__,
            "objects": sorted(obj.name for obj in bpy.data.objects),
            "why": why,
            "rawCompatible": True,
            "requiredRejectedRaw": True,
            "spatialAsset": asset_state,
            "stableUpdate": stable_update,
            "output": str(output),
        },
        sort_keys=True,
    )
)
