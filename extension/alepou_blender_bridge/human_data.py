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

# hm08 stores real facial anatomy in the helper vertex range alongside clothing
# helpers. These parts are anatomy and must survive into a render; everything
# else helper-prefixed is fitting scaffolding.
ANATOMY_HELPER_PARTS = ("eye", "teeth", "tongue", "eyelash")


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


# --- Proxy meshes (.mhclo) ---------------------------------------------------
#
# A proxy is a separate mesh - eyes here - pinned to the base mesh so it follows
# every morph. Each proxy vertex is expressed as a barycentric blend of three
# base vertices plus an offset, and the offset is scaled by how far the body has
# stretched relative to a reference distance. Fitting therefore has to be
# computed, not merely loaded: a statically placed eyeball stops matching the
# socket the moment a morph is applied.


class ProxyDefinition:
    """Parsed .mhclo: how to place a proxy mesh on the base mesh."""

    __slots__ = ("name", "obj_file", "material", "scale_refs", "fits", "path")

    def __init__(self, path: Path) -> None:
        self.path = path
        self.name = path.stem
        self.obj_file = ""
        self.material = ""
        # axis index -> (vertexA, vertexB, referenceDistance)
        self.scale_refs: dict[int, tuple[int, int, float]] = {}
        # (v1, v2, v3, w1, w2, w3, ox, oy, oz)
        self.fits: list[tuple[int, int, int, float, float, float, float, float, float]] = []


AXIS_INDEX = {"x_scale": 0, "y_scale": 1, "z_scale": 2}


def load_proxy(path: str | Path) -> ProxyDefinition:
    """Parse a .mhclo proxy definition."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise HumanDataError("Cannot read proxy {}: {}".format(path, error)) from error

    proxy = ProxyDefinition(path)
    in_verts = False
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        key = parts[0]

        if key == "verts":
            in_verts = True
            continue
        if not in_verts:
            if key in AXIS_INDEX and len(parts) >= 4:
                proxy.scale_refs[AXIS_INDEX[key]] = (int(parts[1]), int(parts[2]), float(parts[3]))
            elif key == "obj_file" and len(parts) >= 2:
                proxy.obj_file = parts[1]
            elif key == "material" and len(parts) >= 2:
                proxy.material = parts[1]
            elif key == "name" and len(parts) >= 2:
                proxy.name = " ".join(parts[1:])
            continue

        # Inside the vertex block. Nine columns is the barycentric form; a
        # single column is the degenerate 1:1 form used by low-poly proxies.
        try:
            if len(parts) >= 9:
                v1, v2, v3 = int(parts[0]), int(parts[1]), int(parts[2])
                w1, w2, w3 = float(parts[3]), float(parts[4]), float(parts[5])
                ox, oy, oz = float(parts[6]), float(parts[7]), float(parts[8])
            elif len(parts) == 1:
                v1 = v2 = v3 = int(parts[0])
                w1, w2, w3 = 1.0, 0.0, 0.0
                ox = oy = oz = 0.0
            else:
                raise HumanDataError(
                    "{}:{}: expected 1 or 9 columns in the vertex block, got {}".format(
                        path, number, len(parts)
                    )
                )
        except ValueError as error:
            raise HumanDataError("{}:{}: {}".format(path, number, error)) from error

        for index in (v1, v2, v3):
            if not 0 <= index < EXPECTED_VERTEX_COUNT:
                raise HumanDataError(
                    "{}:{}: proxy references base vertex {} outside the canonical mesh".format(
                        path, number, index
                    )
                )
        proxy.fits.append((v1, v2, v3, w1, w2, w3, ox, oy, oz))

    if not proxy.fits:
        raise HumanDataError("{}: proxy has no vertex bindings".format(path))
    if not proxy.obj_file:
        raise HumanDataError("{}: proxy names no obj_file".format(path))
    return proxy


def proxy_scale(
    proxy: ProxyDefinition, makehuman_coords: list[tuple[float, float, float]]
) -> tuple[float, float, float]:
    """Per-axis offset scale, from how far the body has stretched.

    MakeHuman divides the current distance between two reference vertices by the
    distance recorded when the proxy was authored, so a proxy offset grows with
    the body rather than staying a fixed absolute distance.
    """
    scale = [1.0, 1.0, 1.0]
    for axis, (a, b, reference) in proxy.scale_refs.items():
        if reference == 0:
            continue
        scale[axis] = abs(makehuman_coords[a][axis] - makehuman_coords[b][axis]) / reference
    return (scale[0], scale[1], scale[2])


def fit_proxy(
    proxy: ProxyDefinition, makehuman_coords: list[tuple[float, float, float]]
) -> list[tuple[float, float, float]]:
    """Proxy vertex positions in MakeHuman space, fitted to the current body."""
    sx, sy, sz = proxy_scale(proxy, makehuman_coords)
    fitted: list[tuple[float, float, float]] = []
    for v1, v2, v3, w1, w2, w3, ox, oy, oz in proxy.fits:
        a, b, c = makehuman_coords[v1], makehuman_coords[v2], makehuman_coords[v3]
        fitted.append((
            a[0] * w1 + b[0] * w2 + c[0] * w3 + ox * sx,
            a[1] * w1 + b[1] * w2 + c[1] * w3 + oy * sy,
            a[2] * w1 + b[2] * w2 + c[2] * w3 + oz * sz,
        ))
    return fitted


def blender_to_makehuman(position: tuple[float, float, float]) -> tuple[float, float, float]:
    """Inverse of the load-time mapping, so fitting can run in MakeHuman space."""
    bx, by, bz = position
    return (bx / UNIT_SCALE, bz / UNIT_SCALE, -by / UNIT_SCALE)


def makehuman_to_blender(position: tuple[float, float, float]) -> tuple[float, float, float]:
    """MakeHuman space back to Blender space."""
    mx, my, mz = position
    return (mx * UNIT_SCALE, -mz * UNIT_SCALE, my * UNIT_SCALE)


# --- Named landmarks ----------------------------------------------------------
#
# Derived geometrically from the region vertex sets rather than hand-picked, so
# they can be regenerated when the pack changes and every definition is written
# down rather than asserted. Coordinates here are Blender space: +X to the
# subject's right in world terms, -Y forward (the face direction), +Z up.
#
# Silhouette matching was considered instead and rejected for frontal views: a
# real head's outline is mostly hair, which this substrate does not have, so the
# model outline and the photograph outline are not the same curve. Landmarks
# compare like with like.

MIDLINE_TOLERANCE = 0.006

# How far below the crown a facial landmark may sit, in metres.
HEAD_BAND_DEPTH = 0.30


def _pick(coords, indices, key, want_max):
    best = None
    best_value = None
    for index in indices:
        value = key(coords[index])
        if best_value is None or (value > best_value if want_max else value < best_value):
            best, best_value = index, value
    return best


def _midline(coords, indices, tolerance=MIDLINE_TOLERANCE):
    near = [i for i in indices if abs(coords[i][0]) <= tolerance]
    return near or list(indices)


def derive_landmarks(
    coords: list[tuple[float, float, float]], regions: dict[str, Iterable[int]]
) -> dict[str, Any]:
    """Locate named anthropometric points from region membership and geometry.

    Each landmark records the rule that found it, so a reader can judge whether
    the definition is the one they meant rather than trusting a bare index.
    """
    # Category membership alone is not enough. A morph category can touch a
    # stray vertex far from its own anatomy - the cheek set reaches one down the
    # body - and helper geometry sits inside several face regions, which put an
    # early glabella on the hair helper. Constrain candidates to real body
    # vertices inside the head band before choosing any extreme.
    body_top = max(coords[i][2] for i in range(0, min(BODY_VERTEX_RANGE[1] + 1, len(coords))))
    head_floor = body_top - HEAD_BAND_DEPTH

    def region(name):
        return [
            index
            for index in regions.get(name, [])
            if index <= BODY_VERTEX_RANGE[1] and coords[index][2] >= head_floor
        ]

    nose, chin, mouth = region("nose"), region("chin"), region("mouth")
    cheek, forehead, ears = region("cheek"), region("forehead"), region("ears")

    rules: list[tuple[str, list[int], Any, bool, str]] = [
        # -Y is forward, so the most forward point is the minimum y.
        ("pronasale", _midline(coords, nose), lambda c: c[1], False, "most forward nose vertex on the midline"),
        ("nasion", _midline(coords, nose), lambda c: c[2], True, "highest nose vertex on the midline"),
        ("subnasale", _midline(coords, nose), lambda c: c[2], False, "lowest nose vertex on the midline"),
        ("glabella", _midline(coords, forehead), lambda c: c[1], False, "most forward forehead vertex on the midline"),
        ("menton", _midline(coords, chin), lambda c: c[2], False, "lowest chin vertex on the midline"),
        ("pogonion", _midline(coords, chin), lambda c: c[1], False, "most forward chin vertex on the midline"),
        ("cheilion_left", mouth, lambda c: c[0], False, "leftmost mouth vertex, viewer left"),
        ("cheilion_right", mouth, lambda c: c[0], True, "rightmost mouth vertex, viewer right"),
        ("zygion_left", cheek, lambda c: c[0], False, "leftmost cheek vertex, viewer left"),
        ("zygion_right", cheek, lambda c: c[0], True, "rightmost cheek vertex, viewer right"),
        ("gonion_left", chin, lambda c: c[0], False, "leftmost jaw vertex, viewer left"),
        ("gonion_right", chin, lambda c: c[0], True, "rightmost jaw vertex, viewer right"),
        ("tragion_left", ears, lambda c: c[0], False, "leftmost ear vertex, viewer left"),
        ("tragion_right", ears, lambda c: c[0], True, "rightmost ear vertex, viewer right"),
    ]

    landmarks: dict[str, Any] = {}
    for name, indices, key, want_max, description in rules:
        if not indices:
            continue
        chosen = _pick(coords, indices, key, want_max)
        if chosen is None:
            continue
        landmarks[name] = {
            "vertex": int(chosen),
            "rule": description,
            "restPosition": [round(float(v), 5) for v in coords[chosen]],
        }

    # Stomion sits between the lip corners rather than at an extreme of anything.
    if "cheilion_left" in landmarks and "cheilion_right" in landmarks and mouth:
        height = (
            coords[landmarks["cheilion_left"]["vertex"]][2]
            + coords[landmarks["cheilion_right"]["vertex"]][2]
        ) / 2.0
        chosen = _pick(coords, _midline(coords, mouth), lambda c: abs(c[2] - height), False)
        if chosen is not None:
            landmarks["stomion"] = {
                "vertex": int(chosen),
                "rule": "midline mouth vertex nearest the height of the lip corners",
                "restPosition": [round(float(v), 5) for v in coords[chosen]],
            }
    return landmarks


LANDMARK_PAIRS = {
    "interpupillary": ("pupil_left", "pupil_right"),
    "bizygomatic": ("zygion_left", "zygion_right"),
    "bigonial": ("gonion_left", "gonion_right"),
    "mouthWidth": ("cheilion_left", "cheilion_right"),
    "noseHeight": ("nasion", "subnasale"),
    "lowerFaceHeight": ("subnasale", "menton"),
    "faceHeight": ("nasion", "menton"),
}


# --- Authoring a proxy --------------------------------------------------------
#
# load_proxy reads a .mhclo; this writes one. Without it we can consume
# MakeHuman's own assets but never make our own, which matters because the
# community hair library is per-asset licensed and cannot be vendored.
#
# A bound proxy follows the body. Sculpting hair onto a head and leaving it as
# a free mesh means every later morph breaks it; binding records where each
# hair vertex sits relative to the body surface, so the same blob still fits
# after the nose, jaw or skull changes.

# MakeHuman's standard body-scale reference pairs, taken from the shipped eye
# proxy. Offsets are divided by how far these have stretched, so a proxy grows
# with the body instead of holding a fixed absolute distance.
DEFAULT_SCALE_REFS = {0: (5399, 11998), 1: (791, 881), 2: (962, 5320)}


def measure_scale_refs(
    makehuman_coords: list[tuple[float, float, float]],
    pairs: dict[int, tuple[int, int]] | None = None,
) -> dict[int, tuple[int, int, float]]:
    """Reference distances for the body a proxy is being authored against."""
    pairs = pairs or DEFAULT_SCALE_REFS
    refs: dict[int, tuple[int, int, float]] = {}
    for axis, (a, b) in pairs.items():
        for index in (a, b):
            if not 0 <= index < EXPECTED_VERTEX_COUNT:
                raise HumanDataError("Scale reference vertex {} is off-topology".format(index))
        refs[axis] = (a, b, abs(makehuman_coords[a][axis] - makehuman_coords[b][axis]))
    return refs


AXIS_NAME = {0: "x_scale", 1: "y_scale", 2: "z_scale"}


def write_proxy(
    path: str | Path,
    *,
    obj_file: str,
    fits: list[tuple[int, int, int, float, float, float, float, float, float]],
    scale_refs: dict[int, tuple[int, int, float]],
    name: str,
    material: str = "",
    notes: Iterable[str] = (),
) -> Path:
    """Write a .mhclo in the same dialect load_proxy reads."""
    if not fits:
        raise HumanDataError("Refusing to write a proxy with no vertex bindings")

    lines = ["# Authored by the Alepou human substrate.", "#"]
    lines += ["# {}".format(note) for note in notes]
    lines += ["", "basemesh hm08", "name {}".format(name), "", "obj_file {}".format(obj_file)]
    for axis in sorted(scale_refs):
        a, b, distance = scale_refs[axis]
        lines.append("{} {} {} {:.4f}".format(AXIS_NAME[axis], a, b, distance))
    if material:
        lines.append("material {}".format(material))
    lines += ["", "verts 0"]
    for v1, v2, v3, w1, w2, w3, ox, oy, oz in fits:
        lines.append(
            "{} {} {} {:.5f} {:.5f} {:.5f} {:.5f} {:.5f} {:.5f}".format(
                v1, v2, v3, w1, w2, w3, ox, oy, oz
            )
        )

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def barycentric(
    point: tuple[float, float, float],
    a: tuple[float, float, float],
    b: tuple[float, float, float],
    c: tuple[float, float, float],
) -> tuple[float, float, float]:
    """Weights of ``point`` projected onto triangle ``abc``.

    Degenerate triangles fall back to the first corner rather than raising, so
    one bad face in a base mesh cannot fail a whole bind.
    """
    v0 = tuple(b[i] - a[i] for i in range(3))
    v1 = tuple(c[i] - a[i] for i in range(3))
    v2 = tuple(point[i] - a[i] for i in range(3))
    d00 = sum(v0[i] * v0[i] for i in range(3))
    d01 = sum(v0[i] * v1[i] for i in range(3))
    d11 = sum(v1[i] * v1[i] for i in range(3))
    d20 = sum(v2[i] * v0[i] for i in range(3))
    d21 = sum(v2[i] * v1[i] for i in range(3))
    denominator = d00 * d11 - d01 * d01
    if abs(denominator) < 1e-12:
        return (1.0, 0.0, 0.0)
    w2 = (d11 * d20 - d01 * d21) / denominator
    w3 = (d00 * d21 - d01 * d20) / denominator
    return (1.0 - w2 - w3, w2, w3)
