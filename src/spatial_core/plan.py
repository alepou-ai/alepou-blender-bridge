"""Backend-neutral managed-object compile and update plans."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .errors import ExternalModificationConflictError, InvalidParameterError
from .resolve import ResolvedEntity, ResolvedScene


PLAN_MODES = {"build", "update", "dry_run"}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ExistingEntity:
    entity_id: str
    fingerprint: str
    managed: bool = True
    scene_id: str | None = None
    externally_modified: bool = False


@dataclass(frozen=True)
class CompileOperation:
    action: str
    entity_id: str
    kind: str
    desired: Mapping[str, Any] | None = None
    previous_fingerprint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"action": self.action, "entityId": self.entity_id, "kind": self.kind}
        if self.desired is not None:
            result["desired"] = dict(self.desired)
        if self.previous_fingerprint is not None:
            result["previousFingerprint"] = self.previous_fingerprint
        return result


@dataclass(frozen=True)
class CompilePlan:
    scene_id: str
    source_hash: str
    units: str
    unit_scale_meters: float
    mode: str
    force: bool
    operations: tuple[CompileOperation, ...]
    asset: Mapping[str, Any] | None = None
    runtime_version: str = "0.1.0"
    backend: str = "blender"
    backend_version: str = "0.1.1"
    fallback_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        result = {
            "schemaVersion": 1,
            "sceneId": self.scene_id,
            "sourceHash": self.source_hash,
            "units": self.units,
            "unitScaleMeters": self.unit_scale_meters,
            "mode": self.mode,
            "force": self.force,
            "runtimeVersion": self.runtime_version,
            "backend": self.backend,
            "backendVersion": self.backend_version,
            "fallbackAllowed": self.fallback_allowed,
            "operations": [operation.to_dict() for operation in self.operations],
        }
        if self.asset is not None:
            result["asset"] = dict(self.asset)
        return result

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for operation in self.operations:
            counts[operation.action] = counts.get(operation.action, 0) + 1
        return {
            "sceneId": self.scene_id,
            "sourceHash": self.source_hash,
            "mode": self.mode,
            "operationCounts": counts,
            "fallbackAllowed": self.fallback_allowed,
        }


def _scale_parameters(entity: ResolvedEntity, scale: float) -> dict[str, Any]:
    values = dict(entity.parameters)
    kind = entity.kind
    if kind == "box":
        values["size"] = [item * scale for item in values["size"]]
        if values.get("bevel"):
            values["bevel"] = {**values["bevel"], "width": values["bevel"]["width"] * scale}
    elif kind == "sphere":
        values["radius"] *= scale
    elif kind == "cylinder":
        values["radius"] *= scale
        values["length"] *= scale
    elif kind == "cone":
        values["radius1"] *= scale
        values["radius2"] *= scale
        values["length"] *= scale
    elif kind == "plane":
        values["size"] = [item * scale for item in values["size"]]
    elif kind == "torus":
        values["major_radius"] *= scale
        values["minor_radius"] *= scale
    elif kind == "mesh":
        values["vertices"] = [[coordinate * scale for coordinate in vertex] for vertex in values["vertices"]]
        values["faces"] = [list(face) for face in values["faces"]]
    return values


def _desired(entity: ResolvedEntity, resolved: ResolvedScene) -> dict[str, Any]:
    scale = resolved.unit_scale_meters
    parameters = _scale_parameters(entity, scale) if entity.is_geometry else {}
    desired = {
        "entityId": entity.id,
        "kind": entity.kind,
        "sceneId": resolved.id,
        "sourceId": entity.source_id,
        "parent": entity.parent,
        "children": list(entity.children),
        "locationMeters": [coordinate * scale for coordinate in entity.world_center],
        "rotationMatrix": [list(row) for row in entity.world_rotation],
        "parametersMeters": parameters,
        "metadata": dict(entity.metadata),
        "sourceHash": resolved.source_hash,
    }
    if entity.is_geometry:
        # Mesh identity is governed only by authored geometry and mesh policy.
        # World transforms, hierarchy, metadata and whole-scene source changes
        # must not force Blender to replace an otherwise identical datablock.
        desired["geometryFingerprint"] = _fingerprint(
            {"kind": entity.kind, "parametersMeters": parameters}
        )
    desired["fingerprint"] = _fingerprint({key: value for key, value in desired.items() if key != "sourceHash"})
    return desired


def _scaled_asset(resolved: ResolvedScene) -> dict[str, Any] | None:
    if resolved.asset is None:
        return None
    scale = resolved.unit_scale_meters
    value = dict(resolved.asset)
    for key in ("translation", "originWorldBefore", "originWorld"):
        value[key] = [coordinate * scale for coordinate in value[key]]
    bounds = value["rootBounds"]
    value["rootBounds"] = {
        "min": [coordinate * scale for coordinate in bounds["min"]],
        "max": [coordinate * scale for coordinate in bounds["max"]],
    }
    if value.get("groundedCoordinate") is not None:
        value["groundedCoordinate"] = value["groundedCoordinate"] * scale
    value["sourceUnits"] = resolved.units
    value["units"] = "m"
    return value


def build_compile_plan(
    resolved: ResolvedScene,
    *,
    mode: str = "update",
    existing: Iterable[ExistingEntity] | None = None,
    force: bool = False,
) -> CompilePlan:
    normalized_mode = str(mode).strip().lower()
    if normalized_mode not in PLAN_MODES:
        raise InvalidParameterError(f"Compile mode must be one of {sorted(PLAN_MODES)}, got {mode!r}")
    current = {item.entity_id: item for item in (existing or ())}
    operations: list[CompileOperation] = []
    for entity_id, entity in sorted(resolved.entities.items()):
        desired = _desired(entity, resolved)
        prior = current.get(entity_id)
        if prior is None or not prior.managed or prior.scene_id not in {None, resolved.id}:
            action = "ensure" if existing is None else "create"
        elif normalized_mode == "build":
            raise ExternalModificationConflictError(
                f"Build mode found existing managed entity {entity_id!r}",
                entity_id=entity_id,
                repair="Use update mode or start from a clean Spatial scene",
            )
        elif prior.fingerprint == desired["fingerprint"]:
            action = "preserve_external" if prior.externally_modified else "unchanged"
        elif prior.externally_modified and not force:
            raise ExternalModificationConflictError(
                f"Spatial and an external Blender edit both changed {entity_id!r}",
                entity_id=entity_id,
                repair="Inspect the external change, then re-run with force only if overwriting is intentional",
            )
        else:
            action = "update"
        operations.append(CompileOperation(action, entity_id, entity.kind, desired, prior.fingerprint if prior else None))

    if existing is not None and normalized_mode in {"update", "dry_run"}:
        desired_ids = set(resolved.entities)
        for entity_id, prior in sorted(current.items()):
            if prior.managed and prior.scene_id == resolved.id and entity_id not in desired_ids:
                if prior.externally_modified and not force:
                    raise ExternalModificationConflictError(
                        f"Stale managed entity {entity_id!r} was externally modified",
                        entity_id=entity_id,
                        repair="Preserve it outside Spatial ownership or use force after inspection",
                    )
                operations.append(CompileOperation("delete", entity_id, "managed", previous_fingerprint=prior.fingerprint))

    return CompilePlan(
        scene_id=resolved.id,
        source_hash=resolved.source_hash,
        units=resolved.units,
        unit_scale_meters=resolved.unit_scale_meters,
        mode=normalized_mode,
        force=bool(force),
        operations=tuple(operations),
        asset=_scaled_asset(resolved),
    )


__all__ = ["CompileOperation", "CompilePlan", "ExistingEntity", "build_compile_plan"]
