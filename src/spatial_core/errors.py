"""Typed, repairable errors raised by Spatial Core."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    path: str | None = None
    entity_id: str | None = None
    relation_id: str | None = None
    repair: str | None = None

    def to_dict(self) -> dict[str, str]:
        return {key: value for key, value in vars(self).items() if value is not None}


class SpatialError(ValueError):
    code = "SPATIAL_ERROR"

    def __init__(
        self,
        message: str,
        *,
        path: str | None = None,
        entity_id: str | None = None,
        relation_id: str | None = None,
        repair: str | None = None,
    ) -> None:
        self.issue = Issue(self.code, message, path, entity_id, relation_id, repair)
        super().__init__(self._format())

    def _format(self) -> str:
        location = f" at {self.issue.path}" if self.issue.path else ""
        context_values = []
        if self.issue.entity_id:
            context_values.append(f"entity={self.issue.entity_id}")
        if self.issue.relation_id:
            context_values.append(f"relation={self.issue.relation_id}")
        context = f" ({', '.join(context_values)})" if context_values else ""
        repair = f" Repair: {self.issue.repair}" if self.issue.repair else ""
        return f"{self.code}{location}{context}: {self.issue.message}.{repair}".rstrip(".")

    def to_dict(self) -> dict[str, str]:
        return self.issue.to_dict()


class SchemaError(SpatialError):
    code = "SCHEMA_ERROR"


class DuplicateIdError(SpatialError):
    code = "DUPLICATE_ID"


class UnknownReferenceError(SpatialError):
    code = "UNKNOWN_REFERENCE"


class InvalidFrameGraphError(SpatialError):
    code = "INVALID_FRAME_GRAPH"


class InvalidParameterError(SpatialError):
    code = "INVALID_PARAMETER"


class ConstraintConflictError(SpatialError):
    code = "CONSTRAINT_CONFLICT"


class UnresolvedPropertyError(SpatialError):
    code = "UNRESOLVED_PROPERTY"


class UnsupportedConstraintCycleError(SpatialError):
    code = "UNSUPPORTED_CONSTRAINT_CYCLE"


class UnsupportedRelationError(SpatialError):
    code = "UNSUPPORTED_RELATION"


class ExternalModificationConflictError(SpatialError):
    code = "EXTERNAL_MODIFICATION_CONFLICT"


class SpatialModeError(SpatialError):
    code = "SPATIAL_MODE_ERROR"
