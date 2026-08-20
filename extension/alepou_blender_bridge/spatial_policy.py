"""Project-level Spatial representation gate for Blender request enforcement."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MODES = {"off", "opt_in", "required"}
DEFAULT_MODE = "off"


class SpatialPolicyError(ValueError):
    pass


def policy_path(bridge_root: Path) -> Path:
    return bridge_root / "spatial-mode.json"


def read_mode(bridge_root: Path) -> str:
    path = policy_path(bridge_root)
    if not path.is_file():
        return DEFAULT_MODE
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise SpatialPolicyError(f"Invalid Spatial mode file: {path}: {error}") from error
    mode = str(value.get("mode") if isinstance(value, dict) else "").strip().lower().replace("-", "_")
    if mode not in MODES:
        raise SpatialPolicyError(f"Spatial mode must be one of {sorted(MODES)}, got {mode!r}")
    return mode


def representation_kind(request: dict[str, Any], action_names: list[str]) -> str:
    if any(name.startswith("spatial.") for name in action_names):
        return "spatial"
    if "script.execute" not in action_names:
        return "support"
    representation = request.get("representation")
    if not isinstance(representation, dict):
        return "raw_bpy"
    kind = str(representation.get("kind") or "raw_bpy").strip().lower()
    return kind


def enforce(bridge_root: Path, request: dict[str, Any], action_names: list[str]) -> str:
    mode = read_mode(bridge_root)
    if any(name.startswith("spatial.") for name in action_names) and "script.execute" in action_names:
        raise SpatialPolicyError("Spatial and raw bpy authoring must use separate recorded requests")
    kind = representation_kind(request, action_names)
    if kind == "spatial" and mode == "off":
        raise SpatialPolicyError("Spatial authoring is disabled for this project")
    if kind == "raw_bpy" and mode == "required":
        raise SpatialPolicyError("Raw bpy authoring is forbidden while Spatial mode is required")
    if kind not in {"support", "raw_bpy", "spatial"}:
        raise SpatialPolicyError(f"Unknown request representation kind {kind!r}")
    if kind == "spatial":
        representation = request.get("representation") or {}
        if representation.get("fallbackAllowed") is not False:
            raise SpatialPolicyError("Spatial requests must explicitly set fallbackAllowed=false")
    return kind


def describe(bridge_root: Path) -> dict[str, Any]:
    mode = read_mode(bridge_root)
    return {
        "mode": mode,
        "default": DEFAULT_MODE,
        "supportedModes": sorted(MODES),
        "spatialAllowed": mode != "off",
        "rawBpyAllowed": mode != "required",
        "fallbackAllowed": False,
    }
