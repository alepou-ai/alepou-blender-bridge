"""Deterministic validation, relation solving, expansion and provenance."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable

from .errors import (
    ConstraintConflictError,
    InvalidFrameGraphError,
    UnknownReferenceError,
    UnsupportedConstraintCycleError,
    UnsupportedRelationError,
)
from .model import AXES, UNITS, Assembly, Axis, Entity, PrimitiveSpec, Relation, Scene, Vec3


AXIS_INDEX = {"X": 0, "Y": 1, "Z": 2}
INDEX_AXIS = {value: key for key, value in AXIS_INDEX.items()}
TOLERANCE = 1e-8
Matrix3 = tuple[Vec3, Vec3, Vec3]


def _identity() -> Matrix3:
    return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def _matmul(a: Matrix3, b: Matrix3) -> Matrix3:
    return tuple(
        tuple(sum(a[row][inner] * b[inner][column] for inner in range(3)) for column in range(3))
        for row in range(3)
    )  # type: ignore[return-value]


def _matvec(matrix: Matrix3, vector: Vec3) -> Vec3:
    return tuple(sum(matrix[row][column] * vector[column] for column in range(3)) for row in range(3))  # type: ignore[return-value]


def _add(a: Vec3, b: Vec3) -> Vec3:
    return tuple(a[index] + b[index] for index in range(3))  # type: ignore[return-value]


def _rotation(euler_degrees: Vec3) -> Matrix3:
    x, y, z = (math.radians(value) for value in euler_degrees)
    cx, sx = math.cos(x), math.sin(x)
    cy, sy = math.cos(y), math.sin(y)
    cz, sz = math.cos(z), math.sin(z)
    rx: Matrix3 = ((1, 0, 0), (0, cx, -sx), (0, sx, cx))
    ry: Matrix3 = ((cy, 0, sy), (0, 1, 0), (-sy, 0, cy))
    rz: Matrix3 = ((cz, -sz, 0), (sz, cz, 0), (0, 0, 1))
    return _matmul(rz, _matmul(ry, rx))


def _principal_axis(direction: Vec3, *, relation_id: str | None = None) -> str:
    absolute = [abs(value) for value in direction]
    index = absolute.index(max(absolute))
    if absolute[index] < 1 - TOLERANCE or any(value > TOLERANCE for position, value in enumerate(absolute) if position != index):
        raise UnsupportedRelationError(
            "The first Spatial solver supports centered_on only for principal axes",
            relation_id=relation_id,
            repair="Use a world/frame X, Y or Z axis for this vertical slice",
        )
    return INDEX_AXIS[index]


@dataclass(frozen=True)
class Bounds:
    minimum: Vec3
    maximum: Vec3

    @classmethod
    def from_center_half(cls, center: Vec3, half: Vec3) -> "Bounds":
        return cls(
            tuple(center[index] - half[index] for index in range(3)),  # type: ignore[arg-type]
            tuple(center[index] + half[index] for index in range(3)),  # type: ignore[arg-type]
        )

    @classmethod
    def combine(cls, values: Iterable["Bounds"]) -> "Bounds":
        items = list(values)
        if not items:
            return cls((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        return cls(
            tuple(min(item.minimum[index] for item in items) for index in range(3)),  # type: ignore[arg-type]
            tuple(max(item.maximum[index] for item in items) for index in range(3)),  # type: ignore[arg-type]
        )

    def to_dict(self) -> dict[str, list[float]]:
        return {"min": list(self.minimum), "max": list(self.maximum)}


@dataclass(frozen=True)
class Derivation:
    entity_id: str
    property: str
    value: float
    source: str
    expression: str
    dependencies: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity_id,
            "property": self.property,
            "value": self.value,
            "source": self.source,
            "expression": self.expression,
            "dependencies": list(self.dependencies),
        }


@dataclass
class ResolvedEntity:
    id: str
    kind: str
    frame: str
    local_center: Vec3
    world_center: Vec3
    world_rotation: Matrix3
    parameters: dict[str, Any]
    bounds: Bounds
    metadata: dict[str, Any] = field(default_factory=dict)
    parent: str | None = None
    children: list[str] = field(default_factory=list)
    anchors: dict[str, Any] = field(default_factory=dict)
    source_id: str | None = None

    @property
    def is_geometry(self) -> bool:
        return self.kind in {"box", "cylinder", "sphere", "cone", "plane", "torus", "mesh"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "frame": self.frame,
            "localCenter": list(self.local_center),
            "worldCenter": list(self.world_center),
            "worldRotation": [list(row) for row in self.world_rotation],
            "parameters": _jsonable(self.parameters),
            "bounds": self.bounds.to_dict(),
            "metadata": _jsonable(self.metadata),
            "parent": self.parent,
            "children": list(self.children),
            "anchors": _jsonable(self.anchors),
            "sourceId": self.source_id,
        }


@dataclass
class ResolvedScene:
    id: str
    units: str
    source_hash: str
    entities: dict[str, ResolvedEntity]
    relations: list[Relation]
    derivations: dict[str, Derivation]

    @property
    def unit_scale_meters(self) -> float:
        return UNITS[self.units]

    def inspect(self, entity_id: str) -> dict[str, Any]:
        try:
            entity = self.entities[entity_id]
        except KeyError as error:
            raise UnknownReferenceError(f"Unknown resolved entity {entity_id!r}", entity_id=entity_id) from error
        relation_ids = [item.id for item in self.relations if item.subject == entity_id or item.object_id == entity_id]
        result = entity.to_dict()
        result["relations"] = relation_ids
        result["units"] = self.units
        return result

    def why(self, selector: str) -> dict[str, Any]:
        derivation = self.derivations.get(selector)
        if derivation is None:
            entity_id, _, property_name = selector.partition(".")
            if entity_id not in self.entities:
                raise UnknownReferenceError(f"Unknown resolved entity {entity_id!r}", entity_id=entity_id)
            raise UnknownReferenceError(
                f"No derivation recorded for {selector!r}",
                entity_id=entity_id,
                repair=f"Try one of: {', '.join(sorted(key for key in self.derivations if key.startswith(entity_id + '.')))}",
            )
        return derivation.to_dict()

    def to_dict(self) -> dict[str, Any]:
        return {
            "spatial": "0.1",
            "scene": {"id": self.id, "units": self.units, "sourceHash": self.source_hash},
            "entities": {key: value.to_dict() for key, value in sorted(self.entities.items())},
            "derivations": {key: value.to_dict() for key, value in sorted(self.derivations.items())},
        }


def _jsonable(value: Any) -> Any:
    if isinstance(value, PrimitiveSpec):
        return value.to_dict()
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


@dataclass(frozen=True)
class _FrameTransform:
    origin: Vec3
    rotation: Matrix3


def _all_nodes(scene: Scene) -> dict[str, Entity]:
    return {**scene.objects, **scene.arrays}


def validate_scene(scene: Scene) -> None:
    nodes = _all_nodes(scene)
    addressable = set(nodes) | set(scene.axes) | set(scene.assemblies)
    frames = {"world"} | set(scene.frames)
    for frame in scene.frames.values():
        if frame.parent not in frames:
            raise UnknownReferenceError(f"Frame {frame.id!r} has unknown parent {frame.parent!r}", path=f"frames.{frame.id}.parent")
    _validate_frame_cycles(scene)
    for axis in scene.axes.values():
        if axis.frame not in frames:
            raise UnknownReferenceError(f"Axis {axis.id!r} uses unknown frame {axis.frame!r}", path=f"axes.{axis.id}.frame")
    for node in nodes.values():
        if node.frame not in frames:
            raise UnknownReferenceError(f"Entity {node.id!r} uses unknown frame {node.frame!r}", path=f"objects.{node.id}.frame")
        if node.kind == "radial_array":
            axis_ref = str(node.parameters["axis"])
            if axis_ref not in AXES and axis_ref not in scene.axes:
                raise UnknownReferenceError(f"Array {node.id!r} uses unknown axis {axis_ref!r}", path=f"arrays.{node.id}.axis")
    for assembly in scene.assemblies.values():
        if assembly.frame not in frames:
            raise UnknownReferenceError(f"Assembly {assembly.id!r} uses unknown frame {assembly.frame!r}", path=f"assemblies.{assembly.id}.frame")
        for child in assembly.children:
            if child not in addressable - set(scene.axes):
                raise UnknownReferenceError(f"Assembly {assembly.id!r} has unknown child {child!r}", path=f"assemblies.{assembly.id}.children")
    _validate_assembly_cycles(scene)
    relation_ids: set[str] = set()
    for relation in scene.relations:
        if relation.id in relation_ids:
            raise ConstraintConflictError(f"Duplicate relation id {relation.id!r}", relation_id=relation.id)
        relation_ids.add(relation.id)
        if relation.subject not in nodes:
            raise UnknownReferenceError(f"Relation subject {relation.subject!r} is not an object or array", relation_id=relation.id)
        if relation.object_id not in nodes and relation.object_id not in scene.axes:
            raise UnknownReferenceError(f"Relation target {relation.object_id!r} is unknown", relation_id=relation.id)
        subject = nodes[relation.subject]
        target_frame = nodes[relation.object_id].frame if relation.object_id in nodes else scene.axes[relation.object_id].frame
        if subject.frame != target_frame:
            raise UnsupportedRelationError(
                f"Relation operands must currently share a frame ({subject.frame!r} != {target_frame!r})",
                relation_id=relation.id,
                repair="Place both operands in one frame or author the transform explicitly",
            )
        if relation.relation in {"after", "before"}:
            target = nodes[relation.object_id]
            if any(abs(value) > TOLERANCE for value in subject.rotation + target.rotation):
                raise UnsupportedRelationError(
                    "after/before currently require axis-aligned entities in their shared frame",
                    relation_id=relation.id,
                )


def _validate_frame_cycles(scene: Scene) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(frame_id: str) -> None:
        if frame_id == "world" or frame_id in visited:
            return
        if frame_id in visiting:
            raise InvalidFrameGraphError(f"Frame cycle includes {frame_id!r}", entity_id=frame_id)
        visiting.add(frame_id)
        visit(scene.frames[frame_id].parent)
        visiting.remove(frame_id)
        visited.add(frame_id)

    for frame_id in scene.frames:
        visit(frame_id)


def _validate_assembly_cycles(scene: Scene) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(assembly_id: str) -> None:
        if assembly_id in visited:
            return
        if assembly_id in visiting:
            raise InvalidFrameGraphError(f"Assembly cycle includes {assembly_id!r}", entity_id=assembly_id)
        visiting.add(assembly_id)
        for child in scene.assemblies[assembly_id].children:
            if child in scene.assemblies:
                visit(child)
        visiting.remove(assembly_id)
        visited.add(assembly_id)

    for assembly_id in scene.assemblies:
        visit(assembly_id)


def _resolve_frames(scene: Scene) -> dict[str, _FrameTransform]:
    resolved = {"world": _FrameTransform((0.0, 0.0, 0.0), _identity())}

    def resolve(frame_id: str) -> _FrameTransform:
        if frame_id in resolved:
            return resolved[frame_id]
        frame = scene.frames[frame_id]
        parent = resolve(frame.parent)
        local_rotation = _rotation(frame.rotation)
        result = _FrameTransform(
            _add(parent.origin, _matvec(parent.rotation, frame.origin)),
            _matmul(parent.rotation, local_rotation),
        )
        resolved[frame_id] = result
        return result

    for frame_id in scene.frames:
        resolve(frame_id)
    return resolved


def _primitive_half(kind: str, parameters: dict[str, Any]) -> Vec3:
    if kind == "box":
        return tuple(value / 2 for value in parameters["size"])  # type: ignore[return-value]
    if kind == "sphere":
        radius = parameters["radius"]
        return (radius, radius, radius)
    if kind in {"cylinder", "cone"}:
        radius = parameters["radius"] if kind == "cylinder" else max(parameters["radius1"], parameters["radius2"])
        values = [radius, radius, radius]
        values[AXIS_INDEX[parameters["axis"]]] = parameters["length"] / 2
        return tuple(values)  # type: ignore[return-value]
    if kind == "plane":
        first, second = parameters["size"]
        normal = parameters["normal"]
        if normal == "X":
            return (0.0, first / 2, second / 2)
        if normal == "Y":
            return (first / 2, 0.0, second / 2)
        return (first / 2, second / 2, 0.0)
    if kind == "torus":
        outer = parameters["major_radius"] + parameters["minor_radius"]
        minor = parameters["minor_radius"]
        axis = parameters["axis"]
        values = [outer, outer, outer]
        values[AXIS_INDEX[axis]] = minor
        return tuple(values)  # type: ignore[return-value]
    if kind == "mesh":
        vertices = parameters["vertices"]
        minimum = [min(vertex[index] for vertex in vertices) for index in range(3)]
        maximum = [max(vertex[index] for vertex in vertices) for index in range(3)]
        return tuple((maximum[index] - minimum[index]) / 2 for index in range(3))  # type: ignore[return-value]
    return (0.0, 0.0, 0.0)


def _array_half(node: Entity) -> Vec3:
    element: PrimitiveSpec = node.parameters["element"]
    half = list(_primitive_half(element.kind, dict(element.parameters)))
    if node.kind == "radial_array":
        radius = node.parameters["radius"]
        axis_ref = str(node.parameters["axis"])
        if axis_ref in AXES:
            principal = axis_ref
        else:
            principal = "X"  # replaced during relation resolution when semantic axis is available
        for index in range(3):
            if index != AXIS_INDEX[principal]:
                half[index] += radius
    elif node.kind == "grid_array":
        first_axis, second_axis = [AXIS_INDEX[item] for item in node.parameters["plane"]]
        counts = node.parameters["counts"]
        pitch = node.parameters["pitch"]
        half[first_axis] += (counts[0] - 1) * pitch[0] / 2
        half[second_axis] += (counts[1] - 1) * pitch[1] / 2
    elif node.kind == "linear_array":
        index = AXIS_INDEX[node.parameters["axis"]]
        half[index] += (node.parameters["count"] - 1) * node.parameters["pitch"] / 2
    return tuple(half)  # type: ignore[return-value]


def _half(node: Entity, scene: Scene) -> Vec3:
    if node.kind.endswith("_array"):
        if node.kind == "radial_array" and str(node.parameters["axis"]) in scene.axes:
            element: PrimitiveSpec = node.parameters["element"]
            half = list(_primitive_half(element.kind, dict(element.parameters)))
            principal = _principal_axis(scene.axes[str(node.parameters["axis"])].direction)
            for index in range(3):
                if index != AXIS_INDEX[principal]:
                    half[index] += node.parameters["radius"]
            return tuple(half)  # type: ignore[return-value]
        return _array_half(node)
    return _primitive_half(node.kind, node.parameters)


def _relation_written_axes(relation: Relation, scene: Scene) -> tuple[str, ...]:
    if relation.relation in {"after", "before"}:
        return (relation.axis or "X",)
    if relation.axes:
        return relation.axes
    if relation.object_id in scene.axes:
        principal = _principal_axis(scene.axes[relation.object_id].direction, relation_id=relation.id)
        return tuple(axis for axis in ("X", "Y", "Z") if axis != principal)
    return ("X", "Y", "Z")


def _node_default_center(node: Entity, scene: Scene) -> Vec3:
    if node.kind == "radial_array" and str(node.parameters["axis"]) in scene.axes:
        return scene.axes[str(node.parameters["axis"])].origin
    return (0.0, 0.0, 0.0)


def _solve_centers(scene: Scene) -> tuple[dict[str, Vec3], dict[str, Derivation]]:
    nodes = _all_nodes(scene)
    values: dict[str, list[float | None]] = {key: list(node.center) for key, node in nodes.items()}
    authored = {(key, index) for key, node in nodes.items() for index, value in enumerate(node.center) if value is not None}
    written = {(relation.subject, AXIS_INDEX[axis]) for relation in scene.relations for axis in _relation_written_axes(relation, scene)}
    derivations: dict[str, Derivation] = {}
    for node_id, node in nodes.items():
        defaults = _node_default_center(node, scene)
        for index in range(3):
            selector = f"{node_id}.center.{INDEX_AXIS[index].lower()}"
            if values[node_id][index] is not None:
                derivations[selector] = Derivation(node_id, f"center.{INDEX_AXIS[index].lower()}", float(values[node_id][index]), "authored", "Authored center coordinate")
            elif (node_id, index) not in written:
                values[node_id][index] = defaults[index]
                derivations[selector] = Derivation(node_id, f"center.{INDEX_AXIS[index].lower()}", defaults[index], "default", "Unconstrained coordinate defaults to the frame origin")

    pending = list(scene.relations)

    def assign(subject: str, axis: str, value: float, relation: Relation, expression: str, dependencies: tuple[str, ...]) -> None:
        index = AXIS_INDEX[axis]
        current = values[subject][index]
        if current is not None and abs(float(current) - value) > TOLERANCE:
            origin = "authored" if (subject, index) in authored else "another relation"
            raise ConstraintConflictError(
                f"{subject}.center.{axis.lower()} is {current:g} from {origin}, but relation requires {value:g}",
                entity_id=subject,
                relation_id=relation.id,
            )
        values[subject][index] = value
        selector = f"{subject}.center.{axis.lower()}"
        derivations[selector] = Derivation(subject, f"center.{axis.lower()}", value, f"relation:{relation.id}", expression, dependencies)

    while pending:
        progressed = False
        remaining: list[Relation] = []
        for relation in pending:
            subject = nodes[relation.subject]
            if relation.relation in {"after", "before"}:
                axis = relation.axis or "X"
                index = AXIS_INDEX[axis]
                target_center = values[relation.object_id][index]
                if target_center is None:
                    remaining.append(relation)
                    continue
                target = nodes[relation.object_id]
                subject_half = _half(subject, scene)[index]
                target_half = _half(target, scene)[index]
                sign = 1.0 if relation.relation == "after" else -1.0
                desired = float(target_center) + sign * (target_half + relation.gap + subject_half)
                expression = f"{relation.object_id}.center.{axis.lower()} {'+' if sign > 0 else '-'} ({target_half:g} + {relation.gap:g} + {subject_half:g})"
                assign(relation.subject, axis, desired, relation, expression, (f"{relation.object_id}.center.{axis.lower()}",))
            elif relation.relation in {"centered_on", "aligned_with"}:
                axes = _relation_written_axes(relation, scene)
                if relation.object_id in scene.axes:
                    target_values = scene.axes[relation.object_id].origin
                    target_prefix = f"axis:{relation.object_id}.origin"
                else:
                    target_values = values[relation.object_id]
                    target_prefix = f"{relation.object_id}.center"
                if any(target_values[AXIS_INDEX[axis]] is None for axis in axes):
                    remaining.append(relation)
                    continue
                for axis in axes:
                    index = AXIS_INDEX[axis]
                    desired = float(target_values[index])
                    assign(
                        relation.subject,
                        axis,
                        desired,
                        relation,
                        f"align {relation.subject}.center.{axis.lower()} with {target_prefix}.{axis.lower()}",
                        (f"{target_prefix}.{axis.lower()}",),
                    )
            else:  # validated before solve
                raise UnsupportedRelationError(f"Unsupported relation {relation.relation!r}", relation_id=relation.id)
            progressed = True
        if remaining and not progressed:
            raise UnsupportedConstraintCycleError(
                f"Could not resolve relations: {', '.join(item.id for item in remaining)}",
                repair="Break the cycle with one authored coordinate",
            )
        pending = remaining

    result: dict[str, Vec3] = {}
    for node_id, coordinates in values.items():
        if any(value is None for value in coordinates):
            unresolved = [INDEX_AXIS[index] for index, value in enumerate(coordinates) if value is None]
            raise UnsupportedConstraintCycleError(f"Unresolved coordinates for {node_id}: {', '.join(unresolved)}", entity_id=node_id)
        result[node_id] = tuple(float(value) for value in coordinates)  # type: ignore[arg-type]
    return result, derivations


def _world_bounds(center: Vec3, half: Vec3, rotation: Matrix3) -> Bounds:
    world_half = tuple(sum(abs(rotation[row][column]) * half[column] for column in range(3)) for row in range(3))
    return Bounds.from_center_half(center, world_half)  # type: ignore[arg-type]


def _resolved_primitive(entity: Entity, local_center: Vec3, frame: _FrameTransform) -> ResolvedEntity:
    world_center = _add(frame.origin, _matvec(frame.rotation, local_center))
    world_rotation = _matmul(frame.rotation, _rotation(entity.rotation))
    half = _primitive_half(entity.kind, entity.parameters)
    return ResolvedEntity(
        entity.id,
        entity.kind,
        entity.frame,
        local_center,
        world_center,
        world_rotation,
        dict(entity.parameters),
        _world_bounds(world_center, half, world_rotation),
        dict(entity.metadata),
        anchors={name: anchor.to_dict() for name, anchor in entity.anchors.items()},
        source_id=entity.id,
    )


def _expand_array(entity: Entity, center: Vec3, frame: _FrameTransform, scene: Scene, derivations: dict[str, Derivation]) -> tuple[ResolvedEntity, list[ResolvedEntity]]:
    element: PrimitiveSpec = entity.parameters["element"]
    children: list[ResolvedEntity] = []
    positions: list[Vec3] = []
    if entity.kind == "radial_array":
        axis_ref = str(entity.parameters["axis"])
        if axis_ref in scene.axes:
            principal = _principal_axis(scene.axes[axis_ref].direction)
        else:
            principal = axis_ref
        angle_step = 360.0 / entity.parameters["count"]
        for index in range(entity.parameters["count"]):
            angle = math.radians(entity.parameters["start_angle"] + index * angle_step)
            offset = [0.0, 0.0, 0.0]
            perpendicular = [item for item in range(3) if item != AXIS_INDEX[principal]]
            offset[perpendicular[0]] = entity.parameters["radius"] * math.cos(angle)
            offset[perpendicular[1]] = entity.parameters["radius"] * math.sin(angle)
            positions.append(tuple(center[position] + offset[position] for position in range(3)))  # type: ignore[arg-type]
    elif entity.kind == "grid_array":
        axes = [AXIS_INDEX[item] for item in entity.parameters["plane"]]
        counts = entity.parameters["counts"]
        pitch = entity.parameters["pitch"]
        for first in range(counts[0]):
            for second in range(counts[1]):
                value = list(center)
                value[axes[0]] += (first - (counts[0] - 1) / 2) * pitch[0]
                value[axes[1]] += (second - (counts[1] - 1) / 2) * pitch[1]
                positions.append(tuple(value))  # type: ignore[arg-type]
    elif entity.kind == "linear_array":
        axis_index = AXIS_INDEX[entity.parameters["axis"]]
        for index in range(entity.parameters["count"]):
            value = list(center)
            value[axis_index] += (index - (entity.parameters["count"] - 1) / 2) * entity.parameters["pitch"]
            positions.append(tuple(value))  # type: ignore[arg-type]

    for index, position in enumerate(positions):
        child_id = f"{entity.id}[{index}]"
        parameters = dict(element.parameters)
        kind = element.kind
        world_center = _add(frame.origin, _matvec(frame.rotation, position))
        world_rotation = _matmul(frame.rotation, _rotation(element.rotation))
        bounds = _world_bounds(world_center, _primitive_half(kind, parameters), world_rotation)
        child = ResolvedEntity(
            child_id,
            kind,
            entity.frame,
            position,
            world_center,
            world_rotation,
            parameters,
            bounds,
            {**dict(entity.metadata), **dict(element.metadata), "spatial.array": entity.id, "spatial.index": index},
            entity.id,
            source_id=entity.id,
        )
        children.append(child)
        for coordinate_index, axis in enumerate(("x", "y", "z")):
            derivations[f"{child_id}.center.{axis}"] = Derivation(
                child_id,
                f"center.{axis}",
                position[coordinate_index],
                f"array:{entity.id}",
                f"Stable {entity.kind} instance {index} generated from {entity.id}",
                (f"{entity.id}.center.{axis}",),
            )
    group_bounds = Bounds.combine(child.bounds for child in children)
    group = ResolvedEntity(
        entity.id,
        entity.kind,
        entity.frame,
        center,
        _add(frame.origin, _matvec(frame.rotation, center)),
        frame.rotation,
        {key: value for key, value in entity.parameters.items() if key != "element"},
        group_bounds,
        dict(entity.metadata),
        children=[child.id for child in children],
        source_id=entity.id,
    )
    return group, children


def resolve_scene(scene: Scene) -> ResolvedScene:
    validate_scene(scene)
    frames = _resolve_frames(scene)
    centers, derivations = _solve_centers(scene)
    entities: dict[str, ResolvedEntity] = {}
    for entity in scene.objects.values():
        entities[entity.id] = _resolved_primitive(entity, centers[entity.id], frames[entity.frame])
    for array in scene.arrays.values():
        group, children = _expand_array(array, centers[array.id], frames[array.frame], scene, derivations)
        entities[group.id] = group
        entities.update({child.id: child for child in children})

    parent_for: dict[str, str] = {}
    for assembly in scene.assemblies.values():
        for child in assembly.children:
            if child in parent_for:
                raise ConstraintConflictError(f"{child!r} belongs to multiple assemblies", entity_id=child)
            parent_for[child] = assembly.id
    for child_id, parent_id in parent_for.items():
        if child_id in entities:
            entities[child_id].parent = parent_id

    def ensure_assembly(assembly: Assembly) -> ResolvedEntity:
        existing = entities.get(assembly.id)
        if existing:
            return existing
        frame = frames[assembly.frame]
        child_entities = [ensure_assembly(scene.assemblies[child]) if child in scene.assemblies else entities[child] for child in assembly.children]
        bounds = Bounds.combine(child.bounds for child in child_entities)
        value = ResolvedEntity(
            assembly.id,
            "assembly",
            assembly.frame,
            (0.0, 0.0, 0.0),
            frame.origin,
            frame.rotation,
            {},
            bounds,
            dict(assembly.metadata),
            parent_for.get(assembly.id),
            list(assembly.children),
            source_id=assembly.id,
        )
        entities[assembly.id] = value
        return value

    for assembly in scene.assemblies.values():
        ensure_assembly(assembly)
    for array in scene.arrays.values():
        if array.id in parent_for:
            entities[array.id].parent = parent_for[array.id]

    return ResolvedScene(scene.id, scene.units, scene.source_hash(), entities, list(scene.relations), derivations)


__all__ = ["Bounds", "Derivation", "ResolvedEntity", "ResolvedScene", "resolve_scene", "validate_scene"]
