"""Real-Blender vertical-slice smoke test.

Run with Blender and pass script arguments after `--`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "extension"))


def arguments() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    return parser.parse_args(values)


ARGS = arguments()
PROJECT = Path(ARGS.project).resolve()
PROJECT.mkdir(parents=True, exist_ok=True)
os.environ["ALEPOU_BLENDER_PROJECT_ROOT"] = str(PROJECT)
os.environ["ALEPOU_BLENDER_TRUST_MODE"] = "trusted_development"
os.environ["ALEPOU_BLENDER_SESSION_ID"] = "blender-smoke"
os.environ["ALEPOU_BLENDER_PROCESSOR_ENABLED"] = "true"

import bpy
from alepou_blender_bridge import protocol, register, service, unregister


def write_request(relative: str, payload: dict) -> None:
    protocol.atomic_write_json(ROOT / relative, payload)
    BRIDGE.tick()


ROOT = protocol.bridge_root(PROJECT)
protocol.ensure_layout(ROOT)
protocol.atomic_write_json(
    ROOT / "commands/processing/smoke-orphan.json",
    {"schemaVersion": 1, "commandId": "smoke-orphan", "actions": [{"action": "state.refresh"}]},
)
register()
BRIDGE = service.get_service()
BRIDGE.tick()

assert (ROOT / "bridge-health.json").is_file()
assert (ROOT / "commands/interrupted/smoke-orphan.json").is_file()
health = protocol.read_json(ROOT / "bridge-health.json")
assert health["processorActive"] is True
assert health["background"] is True

write_request(
    "queries/pending/smoke-summary.json",
    {"schemaVersion": 1, "commandId": "smoke-summary", "actions": [{"action": "scene.summary"}]},
)
summary_result = protocol.read_json(ROOT / "queries/results/smoke-summary.json")
assert summary_result["status"] == "applied", summary_result
write_request(
    "queries/pending/smoke-summary.json",
    {"schemaVersion": 1, "commandId": "smoke-summary", "actions": [{"action": "scene.summary"}]},
)
assert protocol.read_json(ROOT / "queries/results/smoke-summary.json")["status"] == "applied"
assert len(list((ROOT / "runs/smoke-summary/duplicates").glob("*.result.json"))) == 1

script_source = """import bpy
bpy.ops.mesh.primitive_cube_add(location=(1.0, 2.0, 0.5))
cube = bpy.context.object
cube.name = 'AlepouSmokeCube'
cube.scale = (1.0, 2.0, 0.5)
print('created', cube.name)
"""
write_request(
    "commands/pending/smoke-script.json",
    {
        "schemaVersion": 1,
        "commandId": "smoke-script",
        "sessionId": "blender-smoke",
        "actions": [{"action": "script.execute", "source": script_source}],
    },
)
script_result = protocol.read_json(ROOT / "commands/applied/smoke-script.json")
assert script_result["status"] == "applied", script_result
assert bpy.data.objects.get("AlepouSmokeCube") is not None
assert (ROOT / "recovery/smoke-script-before.blend").is_file()

write_request(
    "queries/pending/smoke-inspect.json",
    {"schemaVersion": 1, "commandId": "smoke-inspect", "actions": [{"action": "object.inspect", "object": "AlepouSmokeCube"}]},
)
inspect_result = protocol.read_json(ROOT / "queries/results/smoke-inspect.json")
assert inspect_result["status"] == "applied", inspect_result
bounds = inspect_result["outputs"][0]["value"]["bounds"]
assert bounds["authored"]["world"] and bounds["evaluated"]["world"]

write_request(
    "queries/pending/smoke-capture.json",
    {
        "schemaVersion": 1,
        "commandId": "smoke-capture",
        "actions": [{"action": "capture.diagnostic", "target": {"object": "AlepouSmokeCube"}, "view": "north-east", "mode": "clay", "resolution": 128}],
    },
)
capture_result = protocol.read_json(ROOT / "queries/results/smoke-capture.json")
assert capture_result["status"] == "applied", capture_result
capture_path = Path(capture_result["outputs"][0]["value"]["path"])
assert capture_path.is_file() and capture_path.stat().st_size > 0

save_destination = PROJECT / "artifacts" / "smoke-copy.blend"
write_request(
    "commands/pending/smoke-save.json",
    {
        "schemaVersion": 1,
        "commandId": "smoke-save",
        "sessionId": "blender-smoke",
        "actions": [{"action": "scene.save_copy", "destination": str(save_destination)}],
    },
)
save_result = protocol.read_json(ROOT / "commands/applied/smoke-save.json")
assert save_result["status"] == "applied", save_result
assert save_destination.is_file()

recovery_source = ROOT / "recovery" / "smoke-script-before.blend"
write_request(
    "commands/pending/smoke-restore.json",
    {
        "schemaVersion": 1,
        "commandId": "smoke-restore",
        "sessionId": "blender-smoke",
        "actions": [{"action": "scene.restore_snapshot", "source": str(recovery_source)}],
    },
)
restore_result = protocol.read_json(ROOT / "commands/applied/smoke-restore.json")
assert restore_result["status"] == "applied", restore_result
assert bpy.data.objects.get("AlepouSmokeCube") is None

write_request(
    "commands/pending/smoke-failure.json",
    {
        "schemaVersion": 1,
        "commandId": "smoke-failure",
        "sessionId": "blender-smoke",
        "actions": [{"action": "script.execute", "source": "import bpy\nbpy.data.objects.new('PartialMutation', None)\nraise RuntimeError('intentional smoke failure')"}],
    },
)
failure_result = protocol.read_json(ROOT / "commands/failed/smoke-failure.json")
assert failure_result["status"] == "failed", failure_result
failure_evidence = failure_result["error"]["details"]
assert failure_evidence["rollbackClaimed"] is False
assert failure_evidence["failure"]["type"] == "RuntimeError"
assert bpy.data.objects.get("PartialMutation") is not None

os.environ["ALEPOU_BLENDER_TRUST_MODE"] = "observation"
write_request(
    "commands/pending/smoke-policy.json",
    {
        "schemaVersion": 1,
        "commandId": "smoke-policy",
        "sessionId": "blender-smoke",
        "actions": [{"action": "object.select", "object": "PartialMutation"}],
    },
)
policy_result = protocol.read_json(ROOT / "commands/rejected/smoke-policy.json")
assert policy_result["status"] == "rejected", policy_result
os.environ["ALEPOU_BLENDER_TRUST_MODE"] = "trusted_development"

protocol.atomic_write_json(
    ROOT / "queries/pending/smoke-stop.json",
    {"schemaVersion": 1, "commandId": "smoke-stop", "actions": [{"action": "scene.summary"}]},
)
BRIDGE.stop("smoke_stop")
stop_result = protocol.read_json(ROOT / "queries/results/smoke-stop.json")
assert stop_result["status"] == "rejected", stop_result
assert protocol.read_json(ROOT / "bridge-health.json")["processorActive"] is False
BRIDGE.start()

print(json.dumps({
    "status": "passed",
    "blenderVersion": bpy.app.version_string,
    "bridgeRoot": str(ROOT),
    "stateRevision": BRIDGE.state_revision,
    "capture": str(capture_path),
    "saveCopy": str(save_destination),
    "restoreSnapshot": str(recovery_source),
    "duplicateReplay": "rejected-with-original-preserved",
    "failedScriptEvidence": str(ROOT / "commands/failed/smoke-failure.json"),
    "stopControl": "passed",
}, indent=2))

unregister()
