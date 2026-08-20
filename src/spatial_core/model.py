"""Typed authored model and ordinary-Python authoring surface."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .errors import DuplicateIdError, InvalidParameterError, SchemaError


Number = int | float
Vec2 = tuple[float, float]
Vec3 = tuple[float, float, float]
OptionalVec3 = tuple[float | None, float | None, float | None]
ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
UNITS = {"mm": 0.001, "cm": 0.01, "m": 1.0, "in": 0.0254, "ft": 0.3048}
PRIMITIVES = {"box", "cylinder", "sphere", "cone", "plane", "torus", "mesh", "empty"}
RELATIONS = {"after", "before", "centered_on", "aligned_with"}
AXES = {"X", "Y", "Z"}
SHADING_MODES = {"flat", "smooth", "smooth_by_angle"}


def _number(value: Number, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise InvalidParameterError(f"Expected a finite number, got {value!r}", path=path)
    return float(value)


def _positive(value: Number, path: str, *, allow_zero: bool = False) -> float:
    result = _number(value, path)
    if result < 0 or (result == 0 and not allow_zero):
        operator = "non-negative" if allow_zero else "positive"
        raise InvalidParameterError(f"Expected a {operator} number, got {value!r}", path=path)
    return result


def _segments(value: Any, path: str, *, minimum: int = 3) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise InvalidParameterError(f"Expected an integer >= {minimum}, got {value!r}", path=path)
    return value


def _vec2(value: Sequence[Number], path: str) -> Vec2:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 2:
        raise InvalidParameterError("Expected a two-number vector", path=path)
    return (_number(value[0], f"{path}[0]"), _number(value[1], f"{path}[1]"))


def _vec3(value: Sequence[Number], path: str) -> Vec3:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 3:
        raise InvalidParameterError("Expected a three-number vector", path=path)
    return tuple(_number(value[index], f"{path}[{index}]") for index in range(3))  # type: ignore[return-value]


def _optional_vec3(value: Sequence[Number | None] | None, path: str) -> OptionalVec3:
    if value is None:
        return (None, None, None)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 3:
        raise InvalidParameterError("Expected a three-number vector", path=path)
    return tuple(None if item is None else _number(item, f"{path}[{index}]") for index, item in enumerate(value))  # type: ignore[return-value]


def _axis(value: str, path: str) -> str:
    normalized = str(value).strip().upper().removeprefix("+")
    if normalized not in AXES:
        raise InvalidParameterError(f"Expected X, Y or Z, got {value!r}", path=path)
    return normalized


def _signed_axis(value: str, path: str) -> str:
    candidate = str(value).strip().upper()
    sign = "-" if candidate.startswith("-") else ""
    principal = candidate.removeprefix("+").removeprefix("-")
    if principal not in AXES:
        raise InvalidParameterError(f"Expected a signed X, Y or Z axis, got {value!r}", path=path)
    return sign + principal


def _ref(value: str | Any) -> str:
    candidate = getattr(value, "id", value)
    if not isinstance(candidate, str) or not candidate.strip():
        raise InvalidParameterError(f"Expected an entity id, got {value!r}")
    return candidate.strip()


def _clean(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_clean(item) for item in value]
    if isinstance(value, list):
        return [_clean(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    return value


@dataclass(frozen=True)
class Bevel:
    width: float
    segments: int = 3

    def __post_init__(self) -> None:
        object.__setattr__(self, "width", _positive(self.width, "bevel.width"))
        if isinstance(self.segments, bool) or int(self.segments) < 1:
            raise InvalidParameterError("Bevel segments must be a positive integer", path="bevel.segments")
        object.__setattr__(self, "segments", int(self.segments))

    def to_dict(self) -> dict[str, Any]:
        return {"width": self.width, "segments": self.segments}


@dataclass(frozen=True)
class Shading:
    mode: str
    angle_degrees: float | None = None

    def __post_init__(self) -> None:
        normalized = str(self.mode).strip().lower()
        if normalized not in SHADING_MODES:
            raise InvalidParameterError(f"Shading mode must be one of {sorted(SHADING_MODES)}, got {self.mode!r}", path="shading.mode")
        object.__setattr__(self, "mode", normalized)
        if normalized == "smooth_by_angle":
            angle = _positive(30.0 if self.angle_degrees is None else self.angle_degrees, "shading.angle_degrees")
            if angle >= 180.0:
                raise InvalidParameterError("Smooth-by-angle must be less than 180 degrees", path="shading.angle_degrees")
            object.__setattr__(self, "angle_degrees", angle)
        elif self.angle_degrees is not None:
            raise InvalidParameterError(f"{normalized} shading does not accept an angle", path="shading.angle_degrees")

    @classmethod
    def flat(cls) -> "Shading":
        return cls("flat")

    @classmethod
    def smooth(cls) -> "Shading":
        return cls("smooth")

    @classmethod
    def smooth_by_angle(cls, angle_degrees: Number = 30) -> "Shading":
        return cls("smooth_by_angle", _number(angle_degrees, "shading.angle_degrees"))

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"mode": self.mode}
        if self.angle_degrees is not None:
            result["angleDegrees"] = self.angle_degrees
        return result


def _shading(value: Shading | Mapping[str, Any] | str, path: str) -> dict[str, Any]:
    if isinstance(value, Shading):
        return value.to_dict()
    if isinstance(value, str):
        return Shading(value).to_dict()
    if isinstance(value, Mapping):
        return Shading(str(value.get("mode", "")), value.get("angleDegrees", value.get("angle_degrees"))).to_dict()
    raise InvalidParameterError("shading must be a Shading, mode string, or mapping", path=path)


@dataclass(frozen=True)
class PrimitiveSpec:
    kind: str
    parameters: Mapping[str, Any]
    rotation: Vec3 = (0.0, 0.0, 0.0)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in PRIMITIVES - {"empty"}:
            raise InvalidParameterError(f"Unsupported primitive spec {self.kind!r}")
        object.__setattr__(self, "parameters", _validate_primitive(self.kind, dict(self.parameters), "element"))
        object.__setattr__(self, "rotation", _vec3(self.rotation, "element.rotation"))
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        result = {"type": self.kind, **_clean(dict(self.parameters))}
        if any(self.rotation):
            result["rotation"] = list(self.rotation)
        if self.metadata:
            result["metadata"] = _clean(dict(self.metadata))
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PrimitiveSpec":
        raw = dict(value)
        kind = str(raw.pop("type", ""))
        rotation = raw.pop("rotation", (0, 0, 0))
        metadata = raw.pop("metadata", {})
        return cls(kind, raw, _vec3(rotation, "element.rotation"), metadata)


def BoxSpec(*, size: Sequence[Number], bevel: Bevel | None = None, shading: Shading | Mapping[str, Any] | str | None = None, metadata: Mapping[str, Any] | None = None) -> PrimitiveSpec:
    parameters: dict[str, Any] = {"size": _vec3(size, "size")}
    if bevel:
        parameters["bevel"] = bevel.to_dict()
    if shading is not None:
        parameters["shading"] = shading
    return PrimitiveSpec("box", parameters, metadata=metadata or {})


def CylinderSpec(*, radius: Number, length: Number, axis: str = "Z", segments: int = 64, shading: Shading | Mapping[str, Any] | str | None = None, metadata: Mapping[str, Any] | None = None) -> PrimitiveSpec:
    return PrimitiveSpec("cylinder", {"radius": radius, "length": length, "axis": axis, "segments": segments, "shading": shading if shading is not None else Shading.smooth_by_angle()}, metadata=metadata or {})


def SphereSpec(*, radius: Number, segments: int = 48, rings: int = 24, shading: Shading | Mapping[str, Any] | str = "smooth", metadata: Mapping[str, Any] | None = None) -> PrimitiveSpec:
    return PrimitiveSpec("sphere", {"radius": radius, "segments": segments, "rings": rings, "shading": shading}, metadata=metadata or {})


def ConeSpec(*, radius1: Number, radius2: Number, length: Number, axis: str = "Z", segments: int = 64, shading: Shading | Mapping[str, Any] | str | None = None, metadata: Mapping[str, Any] | None = None) -> PrimitiveSpec:
    return PrimitiveSpec("cone", {"radius1": radius1, "radius2": radius2, "length": length, "axis": axis, "segments": segments, "shading": shading if shading is not None else Shading.smooth_by_angle()}, metadata=metadata or {})


def _validate_primitive(kind: str, parameters: dict[str, Any], path: str) -> dict[str, Any]:
    if kind not in PRIMITIVES:
        raise InvalidParameterError(f"Unsupported primitive type {kind!r}", path=f"{path}.type")
    result = dict(parameters)
    if kind == "box":
        result["size"] = tuple(_positive(item, f"{path}.size[{index}]") for index, item in enumerate(_vec3(result.get("size", ()), f"{path}.size")))
    elif kind in {"cylinder", "cone"}:
        if kind == "cylinder":
            result["radius"] = _positive(result.get("radius"), f"{path}.radius")
        else:
            result["radius1"] = _positive(result.get("radius1"), f"{path}.radius1", allow_zero=True)
            result["radius2"] = _positive(result.get("radius2"), f"{path}.radius2", allow_zero=True)
            if result["radius1"] == 0 and result["radius2"] == 0:
                raise InvalidParameterError("A cone needs at least one non-zero radius", path=path)
        result["length"] = _positive(result.get("length"), f"{path}.length")
        result["axis"] = _axis(result.get("axis", "Z"), f"{path}.axis")
        result["segments"] = _segments(result.get("segments", 64), f"{path}.segments")
    elif kind == "sphere":
        result["radius"] = _positive(result.get("radius"), f"{path}.radius")
        result["segments"] = _segments(result.get("segments", 48), f"{path}.segments")
        result["rings"] = _segments(result.get("rings", 24), f"{path}.rings")
    elif kind == "plane":
        result["size"] = tuple(_positive(item, f"{path}.size[{index}]") for index, item in enumerate(_vec2(result.get("size", ()), f"{path}.size")))
        result["normal"] = _axis(result.get("normal", "Z"), f"{path}.normal")
    elif kind == "torus":
        result["major_radius"] = _positive(result.get("major_radius"), f"{path}.major_radius")
        result["minor_radius"] = _positive(result.get("minor_radius"), f"{path}.minor_radius")
        result["axis"] = _axis(result.get("axis", "Z"), f"{path}.axis")
        result["major_segments"] = _segments(result.get("major_segments", 64), f"{path}.major_segments")
        result["minor_segments"] = _segments(result.get("minor_segments", 24), f"{path}.minor_segments")
    elif kind == "mesh":
        vertices = result.get("vertices")
        faces = result.get("faces")
        if not isinstance(vertices, list) or not isinstance(faces, list):
            raise InvalidParameterError("Mesh requires vertices and faces lists", path=path)
        result["vertices"] = [_vec3(vertex, f"{path}.vertices[{index}]") for index, vertex in enumerate(vertices)]
        clean_faces = []
        for index, face in enumerate(faces):
            if not isinstance(face, Sequence) or isinstance(face, (str, bytes)) or len(face) < 3:
                raise InvalidParameterError("Each mesh face needs at least three indices", path=f"{path}.faces[{index}]")
            clean_faces.append(tuple(int(item) for item in face))
        result["faces"] = clean_faces
    elif kind == "empty":
        result = {}
    bevel = result.get("bevel")
    if bevel is not None:
        if isinstance(bevel, Bevel):
            result["bevel"] = bevel.to_dict()
        elif isinstance(bevel, Mapping):
            result["bevel"] = Bevel(**bevel).to_dict()
        else:
            raise InvalidParameterError("bevel must be a Bevel or mapping", path=f"{path}.bevel")
    default_shading = {
        "box": Shading.flat(),
        "plane": Shading.flat(),
        "mesh": Shading.flat(),
        "cylinder": Shading.smooth_by_angle(),
        "cone": Shading.smooth_by_angle(),
        "sphere": Shading.smooth(),
        "torus": Shading.smooth(),
    }.get(kind)
    if default_shading is not None:
        result["shading"] = _shading(result.get("shading", default_shading), f"{path}.shading")
    return result


@dataclass(frozen=True)
class Anchor:
    name: str
    position: Vec3
    direction: Vec3 | None = None
    up: Vec3 | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"position": list(self.position)}
        if self.direction is not None:
            result["direction"] = list(self.direction)
        if self.up is not None:
            result["up"] = list(self.up)
        if self.metadata:
            result["metadata"] = _clean(dict(self.metadata))
        return result


@dataclass
class Entity:
    id: str
    kind: str
    parameters: dict[str, Any]
    frame: str = "world"
    center: OptionalVec3 = (None, None, None)
    rotation: Vec3 = (0.0, 0.0, 0.0)
    metadata: dict[str, Any] = field(default_factory=dict)
    anchors: dict[str, Anchor] = field(default_factory=dict)
    label: str | None = None
    _scene: "Scene | None" = field(default=None, repr=False, compare=False)

    def anchor(
        self,
        name: str,
        *,
        position: Sequence[Number],
        direction: Sequence[Number] | None = None,
        up: Sequence[Number] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Anchor:
        if name in self.anchors:
            raise DuplicateIdError(f"Duplicate anchor {self.id}.{name}")
        anchor = Anchor(
            name,
            _vec3(position, f"objects.{self.id}.anchors.{name}.position"),
            _vec3(direction, f"objects.{self.id}.anchors.{name}.direction") if direction is not None else None,
            _vec3(up, f"objects.{self.id}.anchors.{name}.up") if up is not None else None,
            dict(metadata or {}),
        )
        self.anchors[name] = anchor
        return anchor

    def _relation(self, relation: str, target: Any, **values: Any) -> "Relation":
        if self._scene is None:
            raise RuntimeError("Entity is not attached to a Scene")
        return self._scene.relate(self, relation, target, **values)

    def after(self, target: Any, *, gap: Number = 0, axis: str = "X", id: str | None = None) -> "Relation":
        return self._relation("after", target, gap=gap, axis=axis, id=id)

    def before(self, target: Any, *, gap: Number = 0, axis: str = "X", id: str | None = None) -> "Relation":
        return self._relation("before", target, gap=gap, axis=axis, id=id)

    def center_on(self, target: Any, *, axes: Iterable[str] | None = None, id: str | None = None) -> "Relation":
        return self._relation("centered_on", target, axes=tuple(axes) if axes is not None else None, id=id)

    def align_with(self, target: Any, *, axes: Iterable[str], id: str | None = None) -> "Relation":
        return self._relation("aligned_with", target, axes=tuple(axes), id=id)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"type": self.kind, **_clean(self.parameters)}
        if self.label:
            result["label"] = self.label
        if self.frame != "world":
            result["frame"] = self.frame
        if any(value is not None for value in self.center):
            result["center"] = list(self.center)
        if any(self.rotation):
            result["rotation"] = list(self.rotation)
        if self.anchors:
            result["anchors"] = {name: anchor.to_dict() for name, anchor in sorted(self.anchors.items())}
        if self.metadata:
            result["metadata"] = _clean(self.metadata)
        return result


@dataclass(frozen=True)
class Frame:
    id: str
    parent: str = "world"
    origin: Vec3 = (0.0, 0.0, 0.0)
    rotation: Vec3 = (0.0, 0.0, 0.0)

    def to_dict(self) -> dict[str, Any]:
        return {"parent": self.parent, "origin": list(self.origin), "rotation": list(self.rotation)}


@dataclass(frozen=True)
class Axis:
    id: str
    frame: str = "world"
    origin: Vec3 = (0.0, 0.0, 0.0)
    direction: Vec3 = (1.0, 0.0, 0.0)

    def to_dict(self) -> dict[str, Any]:
        return {"frame": self.frame, "origin": list(self.origin), "direction": list(self.direction)}


@dataclass
class Assembly:
    id: str
    frame: str = "world"
    children: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add(self, *children: Any) -> "Assembly":
        for child in children:
            child_id = _ref(child)
            if child_id not in self.children:
                self.children.append(child_id)
        return self

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"children": list(self.children)}
        if self.frame != "world":
            result["frame"] = self.frame
        if self.metadata:
            result["metadata"] = _clean(self.metadata)
        return result


@dataclass(frozen=True)
class AssetConstitution:
    root: str
    origin: str
    center_axes: tuple[str, ...]
    ground_axis: str | None
    up: str
    forward: str

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "root": self.root,
            "origin": self.origin,
            "centerAxes": list(self.center_axes),
            "up": self.up,
            "forward": self.forward,
        }
        if self.ground_axis is not None:
            result["groundAxis"] = self.ground_axis
        return result


@dataclass(frozen=True)
class Relation:
    id: str
    subject: str
    relation: str
    object_id: str
    axis: str | None = None
    axes: tuple[str, ...] | None = None
    gap: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "subject": self.subject,
            "relation": self.relation,
            "object": self.object_id,
        }
        if self.axis:
            result["axis"] = self.axis
        if self.axes:
            result["axes"] = list(self.axes)
        if self.gap:
            result["gap"] = self.gap
        return result


class Scene:
    """An authored semantic scene; no Blender objects exist at this layer."""

    version = "0.1"

    def __init__(self, id: str, *, units: str = "m", up: str = "Z", metadata: Mapping[str, Any] | None = None) -> None:
        self.id = self._validate_id(id, "scene.id")
        if units not in UNITS:
            raise InvalidParameterError(f"Unsupported scene units {units!r}", path="scene.units")
        self.units = units
        self.up = _axis(up, "scene.up")
        self.metadata = dict(metadata or {})
        self.frames: dict[str, Frame] = {}
        self.axes: dict[str, Axis] = {}
        self.objects: dict[str, Entity] = {}
        self.arrays: dict[str, Entity] = {}
        self.assemblies: dict[str, Assembly] = {}
        self.relations: list[Relation] = []
        self.asset_constitution: AssetConstitution | None = None
        self._ids: set[str] = set()

    @staticmethod
    def _validate_id(value: str, path: str) -> str:
        candidate = str(value or "").strip()
        if not ID_PATTERN.fullmatch(candidate):
            raise InvalidParameterError(
                f"ID {value!r} must match {ID_PATTERN.pattern}",
                path=path,
            )
        return candidate

    def _register(self, value: str, path: str) -> str:
        candidate = self._validate_id(value, path)
        if candidate in self._ids or candidate == "world":
            raise DuplicateIdError(f"Duplicate scene entity id {candidate!r}", path=path, entity_id=candidate)
        self._ids.add(candidate)
        return candidate

    def frame(self, id: str, *, parent: str | Frame = "world", origin: Sequence[Number] = (0, 0, 0), rotation: Sequence[Number] = (0, 0, 0)) -> Frame:
        entity_id = self._register(id, f"frames.{id}")
        value = Frame(entity_id, _ref(parent), _vec3(origin, f"frames.{id}.origin"), _vec3(rotation, f"frames.{id}.rotation"))
        self.frames[id] = value
        return value

    def axis(self, id: str, *, frame: str | Frame = "world", origin: Sequence[Number] = (0, 0, 0), direction: Sequence[Number] = (1, 0, 0)) -> Axis:
        entity_id = self._register(id, f"axes.{id}")
        vector = _vec3(direction, f"axes.{id}.direction")
        magnitude = math.sqrt(sum(item * item for item in vector))
        if magnitude <= 1e-12:
            raise InvalidParameterError("Axis direction cannot be zero", path=f"axes.{id}.direction")
        value = Axis(entity_id, _ref(frame), _vec3(origin, f"axes.{id}.origin"), tuple(item / magnitude for item in vector))  # type: ignore[arg-type]
        self.axes[id] = value
        return value

    def _primitive(
        self,
        id: str,
        kind: str,
        parameters: Mapping[str, Any],
        *,
        frame: str | Frame = "world",
        center: Sequence[Number | None] | None = None,
        rotation: Sequence[Number] = (0, 0, 0),
        metadata: Mapping[str, Any] | None = None,
        label: str | None = None,
    ) -> Entity:
        entity_id = self._register(id, f"objects.{id}")
        entity = Entity(
            entity_id,
            kind,
            _validate_primitive(kind, dict(parameters), f"objects.{id}"),
            _ref(frame),
            _optional_vec3(center, f"objects.{id}.center"),
            _vec3(rotation, f"objects.{id}.rotation"),
            dict(metadata or {}),
            label=label,
            _scene=self,
        )
        self.objects[id] = entity
        return entity

    def box(self, id: str, *, size: Sequence[Number], bevel: Bevel | Mapping[str, Any] | None = None, shading: Shading | Mapping[str, Any] | str | None = None, **common: Any) -> Entity:
        parameters: dict[str, Any] = {"size": size}
        if bevel is not None:
            parameters["bevel"] = bevel
        if shading is not None:
            parameters["shading"] = shading
        return self._primitive(id, "box", parameters, **common)

    def cylinder(self, id: str, *, radius: Number, length: Number, axis: str = "Z", segments: int = 64, shading: Shading | Mapping[str, Any] | str | None = None, **common: Any) -> Entity:
        return self._primitive(id, "cylinder", {"radius": radius, "length": length, "axis": axis, "segments": segments, "shading": shading if shading is not None else Shading.smooth_by_angle()}, **common)

    def sphere(self, id: str, *, radius: Number, segments: int = 48, rings: int = 24, shading: Shading | Mapping[str, Any] | str = "smooth", **common: Any) -> Entity:
        return self._primitive(id, "sphere", {"radius": radius, "segments": segments, "rings": rings, "shading": shading}, **common)

    def cone(self, id: str, *, radius1: Number, radius2: Number, length: Number, axis: str = "Z", segments: int = 64, shading: Shading | Mapping[str, Any] | str | None = None, **common: Any) -> Entity:
        return self._primitive(id, "cone", {"radius1": radius1, "radius2": radius2, "length": length, "axis": axis, "segments": segments, "shading": shading if shading is not None else Shading.smooth_by_angle()}, **common)

    def plane(self, id: str, *, size: Sequence[Number], normal: str = "Z", **common: Any) -> Entity:
        return self._primitive(id, "plane", {"size": size, "normal": normal}, **common)

    def torus(self, id: str, *, major_radius: Number, minor_radius: Number, axis: str = "Z", major_segments: int = 64, minor_segments: int = 24, shading: Shading | Mapping[str, Any] | str = "smooth", **common: Any) -> Entity:
        return self._primitive(id, "torus", {"major_radius": major_radius, "minor_radius": minor_radius, "axis": axis, "major_segments": major_segments, "minor_segments": minor_segments, "shading": shading}, **common)

    def mesh(self, id: str, *, vertices: list[Sequence[Number]], faces: list[Sequence[int]], shading: Shading | Mapping[str, Any] | str = "flat", **common: Any) -> Entity:
        return self._primitive(id, "mesh", {"vertices": vertices, "faces": faces, "shading": shading}, **common)

    def empty(self, id: str, **common: Any) -> Entity:
        return self._primitive(id, "empty", {}, **common)

    def _array(
        self,
        id: str,
        kind: str,
        parameters: Mapping[str, Any],
        *,
        element: PrimitiveSpec,
        frame: str | Frame = "world",
        center: Sequence[Number | None] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Entity:
        entity_id = self._register(id, f"arrays.{id}")
        entity = Entity(
            entity_id,
            kind,
            {**dict(parameters), "element": element},
            _ref(frame),
            _optional_vec3(center, f"arrays.{id}.center"),
            metadata=dict(metadata or {}),
            _scene=self,
        )
        self.arrays[id] = entity
        return entity

    def radial_array(
        self,
        id: str,
        *,
        count: int,
        axis: str | Axis,
        radius: Number,
        element: PrimitiveSpec,
        start_angle: Number = 0,
        frame: str | Frame = "world",
        center: Sequence[Number | None] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Entity:
        if isinstance(count, bool) or int(count) < 1:
            raise InvalidParameterError("Radial array count must be a positive integer", path=f"arrays.{id}.count")
        axis_ref = axis.id if isinstance(axis, Axis) else str(axis)
        if axis_ref.upper().removeprefix("+") in AXES:
            axis_ref = _axis(axis_ref, f"arrays.{id}.axis")
        return self._array(
            id,
            "radial_array",
            {"count": int(count), "axis": axis_ref, "radius": _positive(radius, f"arrays.{id}.radius", allow_zero=True), "start_angle": _number(start_angle, f"arrays.{id}.start_angle")},
            element=element,
            frame=frame,
            center=center,
            metadata=metadata,
        )

    def grid_array(
        self,
        id: str,
        *,
        counts: Sequence[int],
        pitch: Sequence[Number],
        plane: str,
        element: PrimitiveSpec,
        frame: str | Frame = "world",
        center: Sequence[Number | None] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Entity:
        if not isinstance(counts, Sequence) or len(counts) != 2 or any(isinstance(item, bool) or int(item) < 1 for item in counts):
            raise InvalidParameterError("Grid counts must contain two positive integers", path=f"arrays.{id}.counts")
        normalized_plane = str(plane).strip().upper()
        if normalized_plane not in {"XY", "XZ", "YZ"}:
            raise InvalidParameterError("Grid plane must be XY, XZ or YZ", path=f"arrays.{id}.plane")
        return self._array(
            id,
            "grid_array",
            {"counts": (int(counts[0]), int(counts[1])), "pitch": _vec2(pitch, f"arrays.{id}.pitch"), "plane": normalized_plane},
            element=element,
            frame=frame,
            center=center,
            metadata=metadata,
        )

    def linear_array(
        self,
        id: str,
        *,
        count: int,
        pitch: Number,
        axis: str,
        element: PrimitiveSpec,
        frame: str | Frame = "world",
        center: Sequence[Number | None] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Entity:
        if isinstance(count, bool) or int(count) < 1:
            raise InvalidParameterError("Linear array count must be a positive integer", path=f"arrays.{id}.count")
        return self._array(
            id,
            "linear_array",
            {"count": int(count), "pitch": _positive(pitch, f"arrays.{id}.pitch", allow_zero=True), "axis": _axis(axis, f"arrays.{id}.axis")},
            element=element,
            frame=frame,
            center=center,
            metadata=metadata,
        )

    def assembly(self, id: str, *, frame: str | Frame = "world", children: Iterable[Any] = (), metadata: Mapping[str, Any] | None = None) -> Assembly:
        entity_id = self._register(id, f"assemblies.{id}")
        value = Assembly(entity_id, _ref(frame), [_ref(child) for child in children], dict(metadata or {}))
        self.assemblies[id] = value
        return value

    def asset(
        self,
        *,
        root: str | Assembly,
        origin: str,
        center_axes: Iterable[str] | None = None,
        ground_axis: str | None = "Z",
        up: str = "Z",
        forward: str = "-Y",
    ) -> AssetConstitution:
        if self.asset_constitution is not None:
            raise DuplicateIdError("A Spatial scene can declare only one asset constitution", path="asset")
        normalized_ground = _axis(ground_axis, "asset.groundAxis") if ground_axis is not None else None
        normalized_center = tuple(
            _axis(item, f"asset.centerAxes[{index}]")
            for index, item in enumerate(center_axes if center_axes is not None else tuple(axis for axis in ("X", "Y", "Z") if axis != normalized_ground))
        )
        if len(set(normalized_center)) != len(normalized_center):
            raise InvalidParameterError("Asset center axes must be unique", path="asset.centerAxes")
        if normalized_ground in normalized_center:
            raise InvalidParameterError("The ground axis cannot also be a center axis", path="asset.centerAxes")
        normalized_up = _signed_axis(up, "asset.up")
        normalized_forward = _signed_axis(forward, "asset.forward")
        if normalized_ground is not None and normalized_up.removeprefix("-") != normalized_ground:
            raise InvalidParameterError("Asset up and ground axes must use the same principal axis", path="asset.up")
        if normalized_forward.removeprefix("-") == normalized_up.removeprefix("-"):
            raise InvalidParameterError("Asset forward cannot be parallel to up", path="asset.forward")
        selector = str(origin or "").strip()
        if selector.count(".") != 1 or not all(ID_PATTERN.fullmatch(part) for part in selector.split(".")):
            raise InvalidParameterError("Asset origin must be an entity.anchor selector", path="asset.origin")
        value = AssetConstitution(_ref(root), selector, normalized_center, normalized_ground, normalized_up, normalized_forward)
        self.asset_constitution = value
        return value

    def relate(
        self,
        subject: Any,
        relation: str,
        object: Any,
        *,
        axis: str | None = None,
        axes: Iterable[str] | None = None,
        gap: Number = 0,
        id: str | None = None,
    ) -> Relation:
        relation_name = str(relation).strip().lower()
        if relation_name not in RELATIONS:
            raise InvalidParameterError(f"Unsupported relation {relation!r}")
        relation_id = self._validate_id(id or f"rel_{len(self.relations) + 1:03d}", f"relations[{len(self.relations)}].id")
        if relation_id in {item.id for item in self.relations}:
            raise DuplicateIdError(f"Duplicate relation id {relation_id!r}")
        normalized_axis = _axis(axis or "X", f"relations.{relation_id}.axis") if relation_name in {"after", "before"} else None
        normalized_axes = tuple(_axis(item, f"relations.{relation_id}.axes") for item in axes) if axes is not None else None
        value = Relation(relation_id, _ref(subject), relation_name, _ref(object), normalized_axis, normalized_axes, _number(gap, f"relations.{relation_id}.gap"))
        self.relations.append(value)
        return value

    def validate(self) -> "Scene":
        from .resolve import validate_scene

        validate_scene(self)
        return self

    def resolve(self) -> Any:
        from .resolve import resolve_scene

        return resolve_scene(self)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "spatial": self.version,
            "scene": {"id": self.id, "units": self.units, "up": self.up},
        }
        if self.metadata:
            result["scene"]["metadata"] = _clean(self.metadata)
        if self.asset_constitution is not None:
            result["asset"] = self.asset_constitution.to_dict()
        if self.frames:
            result["frames"] = {key: value.to_dict() for key, value in sorted(self.frames.items())}
        if self.axes:
            result["axes"] = {key: value.to_dict() for key, value in sorted(self.axes.items())}
        if self.assemblies:
            result["assemblies"] = {key: value.to_dict() for key, value in sorted(self.assemblies.items())}
        if self.objects:
            result["objects"] = {key: value.to_dict() for key, value in sorted(self.objects.items())}
        if self.arrays:
            arrays: dict[str, Any] = {}
            for key, entity in sorted(self.arrays.items()):
                item = entity.to_dict()
                item["type"] = entity.kind.removesuffix("_array")
                element = entity.parameters["element"]
                item["element"] = element.to_dict()
                arrays[key] = item
            result["arrays"] = arrays
        if self.relations:
            result["relations"] = [value.to_dict() for value in self.relations]
        return result

    def canonical_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def source_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True) + "\n"

    def to_yaml(self) -> str:
        try:
            import yaml
        except ImportError as error:  # pragma: no cover - declared package dependency
            raise SchemaError("YAML support requires PyYAML") from error
        return yaml.safe_dump(self.to_dict(), sort_keys=False, allow_unicode=True)

    def write_yaml(self, path: str | Path) -> None:
        Path(path).write_text(self.to_yaml(), encoding="utf-8", newline="\n")

    def write_json(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8", newline="\n")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Scene":
        if not isinstance(value, Mapping):
            raise SchemaError("Spatial document root must be a mapping")
        if str(value.get("spatial")) != cls.version:
            raise SchemaError(f"Unsupported Spatial version {value.get('spatial')!r}", path="spatial")
        scene_data = value.get("scene")
        if not isinstance(scene_data, Mapping):
            raise SchemaError("scene must be a mapping", path="scene")
        scene = cls(str(scene_data.get("id", "")), units=str(scene_data.get("units", "m")), up=str(scene_data.get("up", "Z")), metadata=scene_data.get("metadata") or {})
        for entity_id, raw in (value.get("frames") or {}).items():
            scene.frame(entity_id, parent=raw.get("parent", "world"), origin=raw.get("origin", (0, 0, 0)), rotation=raw.get("rotation", (0, 0, 0)))
        for entity_id, raw in (value.get("axes") or {}).items():
            scene.axis(entity_id, frame=raw.get("frame", "world"), origin=raw.get("origin", (0, 0, 0)), direction=raw.get("direction", (1, 0, 0)))
        for entity_id, raw_value in (value.get("objects") or {}).items():
            raw = dict(raw_value)
            kind = str(raw.pop("type", ""))
            common = {
                "frame": raw.pop("frame", "world"),
                "center": raw.pop("center", None),
                "rotation": raw.pop("rotation", (0, 0, 0)),
                "metadata": raw.pop("metadata", {}),
                "label": raw.pop("label", None),
            }
            anchors = raw.pop("anchors", {})
            entity = scene._primitive(entity_id, kind, raw, **common)
            for name, anchor in anchors.items():
                entity.anchor(name, position=anchor["position"], direction=anchor.get("direction"), up=anchor.get("up"), metadata=anchor.get("metadata"))
        for entity_id, raw_value in (value.get("arrays") or {}).items():
            raw = dict(raw_value)
            kind = str(raw.pop("type", ""))
            element = PrimitiveSpec.from_dict(raw.pop("element"))
            frame = raw.pop("frame", "world")
            center = raw.pop("center", None)
            metadata = raw.pop("metadata", {})
            if kind == "radial":
                scene.radial_array(entity_id, element=element, frame=frame, center=center, metadata=metadata, **raw)
            elif kind == "grid":
                scene.grid_array(entity_id, element=element, frame=frame, center=center, metadata=metadata, **raw)
            elif kind == "linear":
                scene.linear_array(entity_id, element=element, frame=frame, center=center, metadata=metadata, **raw)
            else:
                raise InvalidParameterError(f"Unsupported array type {kind!r}", path=f"arrays.{entity_id}.type")
        for entity_id, raw in (value.get("assemblies") or {}).items():
            scene.assembly(entity_id, frame=raw.get("frame", "world"), children=raw.get("children", ()), metadata=raw.get("metadata"))
        asset = value.get("asset")
        if asset is not None:
            if not isinstance(asset, Mapping):
                raise SchemaError("asset must be a mapping", path="asset")
            scene.asset(
                root=asset.get("root"),
                origin=asset.get("origin"),
                center_axes=asset.get("centerAxes"),
                ground_axis=asset.get("groundAxis", "Z"),
                up=asset.get("up", "Z"),
                forward=asset.get("forward", "-Y"),
            )
        for raw in value.get("relations") or []:
            scene.relate(raw.get("subject"), raw.get("relation"), raw.get("object"), axis=raw.get("axis"), axes=raw.get("axes"), gap=raw.get("gap", 0), id=raw.get("id"))
        scene.validate()
        return scene

    @classmethod
    def load(cls, path: str | Path) -> "Scene":
        source = Path(path)
        text = source.read_text(encoding="utf-8-sig")
        if source.suffix.lower() == ".json":
            value = json.loads(text)
        elif source.suffix.lower() in {".yaml", ".yml"}:
            try:
                import yaml
            except ImportError as error:  # pragma: no cover - declared package dependency
                raise SchemaError("YAML support requires PyYAML") from error
            value = yaml.safe_load(text)
        else:
            raise SchemaError("Spatial source must use .json, .yaml or .yml", path=str(source))
        return cls.from_dict(value)


__all__ = [
    "Anchor",
    "Assembly",
    "AssetConstitution",
    "Axis",
    "Bevel",
    "BoxSpec",
    "ConeSpec",
    "CylinderSpec",
    "Entity",
    "Frame",
    "PrimitiveSpec",
    "Relation",
    "Scene",
    "Shading",
    "SphereSpec",
    "UNITS",
]
