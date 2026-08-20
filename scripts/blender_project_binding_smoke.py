"""Verify the installed extension's N-panel project binding in real Blender."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

import bpy


def arguments() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args(values)


args = arguments()
project = Path(args.project).resolve()
project.mkdir(parents=True, exist_ok=True)
output = Path(args.output).resolve()

package = importlib.import_module("bl_ext.user_default.alepou_blender_bridge")
discovery = importlib.import_module("bl_ext.user_default.alepou_blender_bridge.alepou_discovery")
protocol = importlib.import_module("bl_ext.user_default.alepou_blender_bridge.protocol")
service = importlib.import_module("bl_ext.user_default.alepou_blender_bridge.service")

assert hasattr(bpy.types, "ALEPOU_PT_blender_bridge")
preferences = bpy.context.preferences.addons[package.__package__].preferences
candidate = {
    "projectId": "binding-smoke",
    "name": "Binding Smoke",
    "path": str(project),
    "projectKind": "auto",
    "bridgePath": "plan/blender",
    "bindable": True,
    "runningSessionCount": 1,
    "connectedInstanceCount": 0,
    "instances": [],
}
discovery._projects = [candidate]
discovery._enum_items = [("binding-smoke", "Binding Smoke [1 Alepou session(s)]", str(project))]
preferences.discovered_project = "binding-smoke"
preferences.trust_mode = "trusted_development"
preferences.session_id = "must-be-revoked"

result = bpy.ops.alepou.bridge_bind_project()
assert result == {"FINISHED"}, result
bridge = service.get_service()
root = bridge.root()
assert root is not None
health = protocol.read_json(root / "instance.json")
assert health["projectId"] == "binding-smoke", health
assert Path(health["projectRoot"]).resolve() == project
assert health["trustMode"] == "observation"
assert health["processorActive"] is True
assert preferences.project_id == "binding-smoke"
assert preferences.trust_mode == "observation"
assert preferences.session_id == ""

second_project = project.parent / "second-project"
second_project.mkdir(parents=True, exist_ok=True)
second_candidate = {**candidate, "projectId": "binding-smoke-second", "name": "Binding Smoke Second", "path": str(second_project)}
discovery._projects = [candidate, second_candidate]
discovery._enum_items = [
    ("binding-smoke", "Binding Smoke", str(project)),
    ("binding-smoke-second", "Binding Smoke Second", str(second_project)),
]
preferences.discovered_project = "binding-smoke-second"
result = bpy.ops.alepou.bridge_bind_project()
assert result == {"FINISHED"}, result
root = bridge.root()
assert root is not None
health = protocol.read_json(root / "instance.json")
assert health["projectId"] == "binding-smoke-second", health
assert Path(health["projectRoot"]).resolve() == second_project

output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(
    json.dumps(
        {
            "ok": True,
            "projectId": health["projectId"],
            "instanceId": health["instanceId"],
            "projectRoot": health["projectRoot"],
            "trustMode": health["trustMode"],
            "processorActive": health["processorActive"],
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
