"""Real-Blender regression for canonical and legacy Spatial identity export."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import bpy


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "extension"))

from alepou_blender_bridge import state


bpy.ops.wm.read_factory_settings(use_empty=True)


def cube(name: str):
    bpy.ops.mesh.primitive_cube_add(size=1)
    obj = bpy.context.object
    obj.name = name
    return obj


canonical = cube("CanonicalSpatial")
canonical["spatial.entity_id"] = "canonical_entity"
canonical["spatial_id"] = "obsolete_alias"

legacy = cube("LegacySpatial")
legacy["spatial_id"] = "legacy_entity"

plain = cube("PlainObject")

depsgraph = bpy.context.evaluated_depsgraph_get()
canonical_summary = state.object_summary(canonical, depsgraph, include_bounds=False)
legacy_summary = state.object_summary(legacy, depsgraph, include_bounds=False)
plain_summary = state.object_summary(plain, depsgraph, include_bounds=False)
listed = {item["name"]: item["spatialId"] for item in state.objects_summary()["objects"]}
inspected = state.inspect_object("CanonicalSpatial")

if canonical_summary["spatialId"] != "canonical_entity":
    raise AssertionError(canonical_summary)
if legacy_summary["spatialId"] != "legacy_entity":
    raise AssertionError(legacy_summary)
if plain_summary["spatialId"] is not None:
    raise AssertionError(plain_summary)
if listed != {
    "CanonicalSpatial": "canonical_entity",
    "LegacySpatial": "legacy_entity",
    "PlainObject": None,
}:
    raise AssertionError(listed)
if inspected["spatialId"] != "canonical_entity":
    raise AssertionError(inspected)

print(
    "SPATIAL_STATE_IDENTITY_REGRESSION="
    + json.dumps(
        {
            "canonical": canonical_summary["spatialId"],
            "legacy": legacy_summary["spatialId"],
            "plain": plain_summary["spatialId"],
            "listed": listed,
            "inspected": inspected["spatialId"],
        },
        sort_keys=True,
    )
)
