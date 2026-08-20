"""Familiar Python authoring surface for the backend-neutral Spatial IR."""

from spatial_core import (
    Anchor,
    Assembly,
    Axis,
    AuthoringPolicy,
    Bevel,
    Bounds,
    BoxSpec,
    CompileOperation,
    CompilePlan,
    ConeSpec,
    ConstraintConflictError,
    CylinderSpec,
    Derivation,
    DuplicateIdError,
    Entity,
    ExistingEntity,
    ExternalModificationConflictError,
    Frame,
    InvalidFrameGraphError,
    InvalidParameterError,
    PrimitiveSpec,
    Relation,
    ResolvedEntity,
    ResolvedScene,
    Scene,
    SchemaError,
    Shading,
    SpatialError,
    SpatialMode,
    SpatialModeError,
    SphereSpec,
    UnknownReferenceError,
    UnresolvedPropertyError,
    UnsupportedConstraintCycleError,
    UnsupportedRelationError,
    build_compile_plan,
)

__version__ = "0.1.0"

__all__ = [name for name in globals() if not name.startswith("_")]
