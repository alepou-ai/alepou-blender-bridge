"""Real-Blender regression for project-scoped multi-instance routing."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "extension"))


with tempfile.TemporaryDirectory(prefix="alepou-blender-instances-") as directory:
    project = Path(directory).resolve()
    os.environ["ALEPOU_BLENDER_PROJECT_ROOT"] = str(project)
    os.environ["ALEPOU_BLENDER_PROCESSOR_ENABLED"] = "true"
    os.environ["ALEPOU_BLENDER_TRUST_MODE"] = "observation"
    os.environ["ALEPOU_BLENDER_INSTANCE_ID"] = "instance-a"

    from alepou_blender_bridge import protocol, service

    first = service.BridgeService()
    os.environ["ALEPOU_BLENDER_INSTANCE_ID"] = "instance-b"
    second = service.BridgeService()

    first.start()
    second.start()
    base = protocol.bridge_root(project)
    first_root = first.root()
    second_root = second.root()
    assert first_root is not None and second_root is not None
    assert first_root != second_root
    assert first._legacy_owner is True
    assert second._legacy_owner is False
    assert protocol.read_json(first_root / "instance.json")["instanceId"] == "instance-a"
    assert protocol.read_json(second_root / "instance.json")["instanceId"] == "instance-b"
    assert protocol.read_json(base / "legacy-owner.json")["instanceId"] == "instance-a"

    def submit(instance: service.BridgeService, command_id: str, target: str) -> dict:
        root = instance.root()
        assert root is not None
        protocol.atomic_write_json(
            root / "queries" / "pending" / f"{command_id}.json",
            {
                "schemaVersion": 1,
                "commandId": command_id,
                "target": {"instanceId": target},
                "actions": [{"action": "bridge.ping"}],
            },
        )
        instance._process_next(root, query=True, require_target=True)
        return protocol.read_json(root / "queries" / "results" / f"{command_id}.json")

    first_result = submit(first, "to-first", "instance-a")
    second_result = submit(second, "to-second", "instance-b")
    mismatch = submit(second, "wrong-target", "instance-a")
    assert first_result["status"] == "applied" and first_result["instanceId"] == "instance-a"
    assert second_result["status"] == "applied" and second_result["instanceId"] == "instance-b"
    assert mismatch["status"] == "rejected" and mismatch["instanceId"] == "instance-b"

    protocol.atomic_write_json(
        base / "queries" / "pending" / "legacy-first.json",
        {"schemaVersion": 1, "commandId": "legacy-first", "actions": [{"action": "bridge.ping"}]},
    )
    first._process_next(base, query=True, require_target=False)
    legacy_first = protocol.read_json(base / "queries" / "results" / "legacy-first.json")
    assert legacy_first["instanceId"] == "instance-a"

    first.stop("handoff")
    second.tick()
    assert second._legacy_owner is True
    assert protocol.read_json(base / "legacy-owner.json")["instanceId"] == "instance-b"
    protocol.atomic_write_json(
        base / "queries" / "pending" / "legacy-second.json",
        {"schemaVersion": 1, "commandId": "legacy-second", "actions": [{"action": "bridge.ping"}]},
    )
    second._process_next(base, query=True, require_target=False)
    legacy_second = protocol.read_json(base / "queries" / "results" / "legacy-second.json")
    assert legacy_second["instanceId"] == "instance-b"
    second.stop("regression_complete")

    print(
        "BLENDER_INSTANCE_ROUTING_REGRESSION="
        + json.dumps(
            {
                "firstRoot": str(first_root),
                "secondRoot": str(second_root),
                "first": first_result,
                "second": second_result,
                "mismatchStatus": mismatch["status"],
                "legacyFirst": legacy_first["instanceId"],
                "legacySecond": legacy_second["instanceId"],
            },
            sort_keys=True,
        )
    )
