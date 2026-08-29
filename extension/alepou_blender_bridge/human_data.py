"""Pure-Python parsing for the Alepou human substrate.

No ``bpy`` import: everything here is testable outside Blender. The Blender
side lives in ``human.py``.

The substrate is fixed-topology. Every morph and every bone anchor is a list of
vertex indices into one canonical mesh, so vertex identity is the contract that
makes the whole thing work. See ``resources/human_v0/README.md``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Iterator

# hm08 canonical topology.
EXPECTED_VERTEX_COUNT = 19158
BODY_VERTEX_RANGE = (0, 13379)
HELPER_VERTEX_RANGE = (13380, 19157)

# MakeHuman authors in decimetres; Blender scenes are metres.
UNIT_SCALE = 0.1

ETHNICITY_DIRS = frozenset({"african", "asian", "caucasian"})


class HumanDataError(ValueError):
    """Raised when substrate data is missing, malformed, or off-topology."""


def target_offset_to_blender(lateral: float, up: float, depth: float) -> tuple[float, float, float]:
    """Map a MakeHuman displacement into Blender space.

    Target columns are ``index lateral up depth`` in MakeHuman's y-up space.
    Blender is z-up with characters facing -Y, so depth negates and the last two
    columns swap. Verified against the data rather than assumed:
    ``nose-scale-horiz-incr`` moves column 1, ``nose-scale-vert-incr`` moves
    column 2, ``nose-scale-depth-incr`` moves column 3, and nose tip vertex 297
    sits on the midline at positive depth.
    """
    return (lateral * UNIT_SCALE, -depth * UNIT_SCALE, up * UNIT_SCALE)


def parse_target(path: str | Path) -> list[tuple[int, tuple[float, float, float]]]:
    """Parse a .target sparse displacement file into Blender-space offsets."""
    path = Path(path)
    offsets: list[tuple[int, tuple[float, float, float]]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise HumanDataError("Cannot read target {}: {}".format(path, error)) from error

    for number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 4:
            raise HumanDataError(
                "{}:{}: expected 'index dx dy dz', got {!r}".format(path, number, line)
            )
        try:
            index = int(parts[0])
            lateral, up, depth = float(parts[1]), float(parts[2]), float(parts[3])
        except ValueError as error:
            raise HumanDataError("{}:{}: {}".format(path, number, error)) from error
        if not 0 <= index < EXPECTED_VERTEX_COUNT:
            raise HumanDataError(
                "{}:{}: vertex index {} is outside the canonical mesh".format(path, number, index)
            )
        offsets.append((index, target_offset_to_blender(lateral, up, depth)))
    if not offsets:
        raise HumanDataError("{}: target contains no displacements".format(path))
    return offsets


def morph_name(path: str | Path) -> str:
    """Shape-key name for a target file, unique across ethnicity subdirectories."""
    path = Path(path)
    parent = path.parent.name
    if parent in ETHNICITY_DIRS:
        return "{}_{}".format(parent, path.stem)
    return path.stem


def iter_targets(root: str | Path) -> Iterator[Path]:
    """Every .target under root, in a stable order."""
    return iter(sorted(Path(root).rglob("*.target")))


def load_skeleton(path: str | Path) -> dict[str, Any]:
    """Load a .mhskel skeleton definition.

    Bones name joints; joints are vertex index lists. A bone endpoint is the
    centroid of its joint's vertices, which is what lets the rig be rebuilt
    after the mesh is reshaped.
    """
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HumanDataError("Cannot read skeleton {}: {}".format(path, error)) from error

    for key in ("bones", "joints"):
        if not isinstance(data.get(key), dict) or not data[key]:
            raise HumanDataError("{}: skeleton has no {!r}".format(path, key))

    for name, verts in data["joints"].items():
        if not isinstance(verts, list) or not verts:
            raise HumanDataError("{}: joint {!r} has no vertices".format(path, name))
        for index in verts:
            if not isinstance(index, int) or not 0 <= index < EXPECTED_VERTEX_COUNT:
                raise HumanDataError(
                    "{}: joint {!r} references vertex {} outside the canonical mesh".format(
                        path, name, index
                    )
                )
    return data


def load_weights(path: str | Path) -> dict[str, list[tuple[int, float]]]:
    """Load .mhw skin weights as {bone: [(vertex, weight), ...]}."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HumanDataError("Cannot read weights {}: {}".format(path, error)) from error

    raw = data.get("weights")
    if not isinstance(raw, dict) or not raw:
        raise HumanDataError("{}: weights file has no 'weights'".format(path))

    weights: dict[str, list[tuple[int, float]]] = {}
    for bone, pairs in raw.items():
        entries: list[tuple[int, float]] = []
        for pair in pairs:
            if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                raise HumanDataError(
                    "{}: bone {!r} has a malformed weight entry".format(path, bone)
                )
            index, weight = int(pair[0]), float(pair[1])
            if not 0 <= index < EXPECTED_VERTEX_COUNT:
                raise HumanDataError(
                    "{}: bone {!r} weights vertex {} outside the canonical mesh".format(
                        path, bone, index
                    )
                )
            entries.append((index, weight))
        weights[bone] = entries
    return weights


def bone_order(bones: dict[str, Any]) -> list[str]:
    """Bone names ordered parents-before-children.

    Blender needs a parent to exist before a child is parented to it, and a
    cycle in the definition must be reported rather than silently dropped.
    """
    ordered: list[str] = []
    seen: set[str] = set()

    def visit(name: str, trail: tuple[str, ...]) -> None:
        if name in seen:
            return
        if name in trail:
            raise HumanDataError(
                "Skeleton has a cyclic parent chain: {}".format(" -> ".join(trail + (name,)))
            )
        parent = bones[name].get("parent")
        if parent:
            if parent not in bones:
                raise HumanDataError(
                    "Bone {!r} names missing parent {!r}".format(name, parent)
                )
            visit(parent, trail + (name,))
        seen.add(name)
        ordered.append(name)

    for name in bones:
        visit(name, ())
    return ordered


def centroid(
    coords: list[tuple[float, float, float]], indices: Iterable[int]
) -> tuple[float, float, float]:
    """Average position of the given vertices, used for bone endpoints."""
    total = [0.0, 0.0, 0.0]
    count = 0
    for index in indices:
        x, y, z = coords[index]
        total[0] += x
        total[1] += y
        total[2] += z
        count += 1
    if not count:
        raise HumanDataError("Cannot take a centroid of no vertices")
    return (total[0] / count, total[1] / count, total[2] / count)


def topology_signature(
    vertex_count: int, polygon_vertex_indices: Iterable[Iterable[int]]
) -> str:
    """Stable digest of mesh connectivity, independent of vertex positions.

    Positions change constantly - that is the entire point of morphs. What must
    never change is how many vertices there are and which ones form each face.
    Two meshes sharing this signature can exchange morphs and bone anchors
    safely; two that do not, cannot.
    """
    digest = hashlib.sha256()
    digest.update("v{}\n".format(vertex_count).encode("ascii"))
    for loop in polygon_vertex_indices:
        line = ",".join(str(int(index)) for index in loop) + "\n"
        digest.update(line.encode("ascii"))
    return digest.hexdigest()


TOPOLOGY_DRIFT_HELP = (
    "Every morph and every bone anchor is a vertex index, so they no longer refer "
    "to the anatomy they were authored for. Undo whatever changed the topology - "
    "typically an applied Mirror, Subdivision, Decimate or Triangulate modifier, a "
    "merge by distance, a dissolve, or a join."
)


def check_vertex_count(actual: int) -> None:
    """Fail loudly when the mesh is no longer the canonical topology."""
    if actual != EXPECTED_VERTEX_COUNT:
        raise HumanDataError(
            "Topology drift: mesh has {} vertices, canonical hm08 has {}. {}".format(
                actual, EXPECTED_VERTEX_COUNT, TOPOLOGY_DRIFT_HELP
            )
        )


def check_signature(actual: str, expected: str) -> None:
    """Fail loudly when face connectivity changed even though the count matched."""
    if actual != expected:
        raise HumanDataError(
            "Topology drift: face connectivity changed (signature {}, expected {}). {}".format(
                actual[:16], expected[:16], TOPOLOGY_DRIFT_HELP
            )
        )
