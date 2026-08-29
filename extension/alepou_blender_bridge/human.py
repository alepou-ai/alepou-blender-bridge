"""Blender-side loading of the Alepou human substrate.

Three pieces of support and one hard contract, as planned in
``blender-human-substrate-plan.md``:

* load the canonical human mesh;
* convert sparse morph targets into shape keys;
* rebuild the rig from vertex-anchored joint definitions;
* refuse to build a rig or export when the topology has drifted.

Everything else stays arbitrary ``bpy``. The agent may add its own corrective
shape keys freely - that freedom is the point of the substrate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import bpy
from mathutils import Vector

from . import human_data
from .human_data import HumanDataError

MESH_NAME = "AlepouHuman"
ARMATURE_NAME = "AlepouHumanRig"
BASIS_KEY = "Basis"
SIGNATURE_PROPERTY = "alepou_topology_signature"
RESOURCE_PROPERTY = "alepou_human_resource"

RESOURCES_ENV = "ALEPOU_BLENDER_RESOURCES"
PACK_NAME = "human_v0"


def default_resource_dir() -> Path:
    """Locate the human pack without the caller hardcoding an install path.

    Alepou sets ALEPOU_BLENDER_RESOURCES when it launches Blender, so a session
    bound to any project can find the pack that ships with the running Alepou.
    """
    import os

    root = os.environ.get(RESOURCES_ENV, "").strip()
    if root:
        candidate = Path(root) / PACK_NAME
        if candidate.is_dir():
            return candidate
        raise HumanDataError(
            "{} points at {} but {} is not there".format(RESOURCES_ENV, root, PACK_NAME)
        )

    bundled = Path(__file__).resolve().parent / "resources" / PACK_NAME
    if bundled.is_dir():
        return bundled

    raise HumanDataError(
        "Cannot locate the human substrate. Alepou normally sets {} when it launches "
        "Blender. Pass the resource directory explicitly, or check that this Blender "
        "was started by Alepou.".format(RESOURCES_ENV)
    )


def _mesh_object(target: Any) -> bpy.types.Object:
    if target is None:
        raise HumanDataError("No human object was given")
    if getattr(target, "type", None) != "MESH":
        raise HumanDataError("Object {!r} is not a mesh".format(getattr(target, "name", target)))
    return target


def polygon_loops(mesh: bpy.types.Mesh) -> list[list[int]]:
    return [list(polygon.vertices) for polygon in mesh.polygons]


def signature_of(obj: Any) -> str:
    """Connectivity digest of the object's mesh, ignoring vertex positions."""
    mesh = _mesh_object(obj).data
    return human_data.topology_signature(len(mesh.vertices), polygon_loops(mesh))


def verify_topology(obj: Any, *, stage: str = "operation") -> str:
    """Raise unless the mesh is still the canonical topology.

    Called before rig construction and before export. Positions are free to
    move; vertex count and face connectivity are not.
    """
    mesh = _mesh_object(obj).data
    human_data.check_vertex_count(len(mesh.vertices))

    actual = human_data.topology_signature(len(mesh.vertices), polygon_loops(mesh))
    expected = obj.get(SIGNATURE_PROPERTY)
    if expected:
        try:
            human_data.check_signature(actual, expected)
        except HumanDataError as error:
            raise HumanDataError("{} blocked. {}".format(stage.capitalize(), error)) from error
    return actual


def load_human(
    resource_dir: str | Path | None = None,
    *,
    name: str = MESH_NAME,
    collection: Any = None,
) -> bpy.types.Object:
    """Load the canonical base mesh, helpers included, as a new object.

    Helper geometry (vertices 13380-19157) is kept. The rig anchors bone
    endpoints to joint-cube vertices inside that range, so deleting helpers
    destroys the ability to rebuild the rig after reshaping. They are hidden
    instead, and stripped only at final export.
    """
    resource_dir = Path(resource_dir) if resource_dir else default_resource_dir()
    obj_path = resource_dir / "3dobjs" / "base.obj"
    if not obj_path.is_file():
        raise HumanDataError("No base mesh at {}".format(obj_path))

    coords: list[tuple[float, float, float]] = []
    faces: list[list[int]] = []
    group_vertices: dict[str, set[int]] = {}
    scale = human_data.UNIT_SCALE
    current_group = ""
    for line in obj_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("v "):
            parts = line.split()
            lateral, up, depth = float(parts[1]), float(parts[2]), float(parts[3])
            coords.append((lateral * scale, -depth * scale, up * scale))
        elif line.startswith("g "):
            current_group = line[2:].strip()
            group_vertices.setdefault(current_group, set())
        elif line.startswith("f "):
            loop = [int(chunk.split("/")[0]) - 1 for chunk in line.split()[1:]]
            if len(loop) >= 3:
                faces.append(loop)
                if current_group:
                    group_vertices[current_group].update(loop)

    human_data.check_vertex_count(len(coords))

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(coords, [], faces)
    mesh.validate(verbose=False)
    mesh.update()

    obj = bpy.data.objects.new(name, mesh)
    obj[RESOURCE_PROPERTY] = str(resource_dir)
    obj[SIGNATURE_PROPERTY] = human_data.topology_signature(len(mesh.vertices), polygon_loops(mesh))

    target_collection = collection or bpy.context.scene.collection
    target_collection.objects.link(obj)

    _ensure_basis(obj)
    _tag_vertex_groups(obj, group_vertices)
    return obj


def _ensure_basis(obj: bpy.types.Object) -> bpy.types.ShapeKey:
    mesh = obj.data
    if mesh.shape_keys is None:
        return obj.shape_key_add(name=BASIS_KEY, from_mix=False)
    return mesh.shape_keys.key_blocks[0]


def _tag_vertex_groups(obj: bpy.types.Object, group_vertices: dict[str, set[int]]) -> None:
    """Sort the mesh into semantic groups so renders hide only what should hide.

    hm08 keeps eyes, eyelashes, teeth and the tongue inside the helper vertex
    range alongside clothing helpers and joint cubes. Masking the whole helper
    range therefore removes them and leaves empty sockets, which is what the
    first pilot rendered. They live in the same mesh, so they already follow
    every morph and are already rig-anchored.

    Note that the eye and eyelash groups are fitting *envelopes*, deliberately
    slightly larger than the anatomy they guide. Rendered directly they poke
    through the eyelids. They are correct for placement, driving and export
    masking; a render-quality eyeball still needs the separate eye proxy.
    """
    low, high = human_data.HELPER_VERTEX_RANGE

    def add(name: str, indices: Iterable[int]) -> int:
        indices = sorted(set(indices))
        group = obj.vertex_groups.get(name) or obj.vertex_groups.new(name=name)
        if indices:
            group.add(list(indices), 1.0, "REPLACE")
        return len(indices)

    helper_range = set(range(low, high + 1))
    anatomy: set[int] = set()
    clothing: set[int] = set()
    joints: set[int] = set()
    for name, vertices in group_vertices.items():
        if name.startswith("joint-"):
            joints |= vertices
        elif any(part in name for part in human_data.ANATOMY_HELPER_PARTS):
            anatomy |= vertices
        elif name.startswith("helper-"):
            clothing |= vertices

    # A group name may only claim helper-range vertices; the body is never hidden.
    anatomy &= helper_range
    clothing &= helper_range
    joints &= helper_range

    # Per-part groups so a project can give the eyes their own material, drive
    # them, or export them separately without rediscovering index ranges.
    parts: dict[str, set[int]] = {}
    for name, vertices in group_vertices.items():
        for part in human_data.ANATOMY_HELPER_PARTS:
            if part in name and not name.startswith("joint-"):
                key = "alepou_{}s".format(part) if not part.endswith("s") else "alepou_" + part
                parts.setdefault(key, set()).update(vertices & helper_range)
    for key, vertices in parts.items():
        add(key, vertices)

    add("alepou_helpers", helper_range)
    add("alepou_anatomy", anatomy)
    add("alepou_clothing_helpers", clothing)
    add("alepou_joints", joints)
    add("alepou_hidden", (clothing | joints) - anatomy)


def hide_non_render_geometry(obj: Any, *, name: str = "AlepouHideHelpers") -> Any:
    """Mask clothing helpers and joint cubes, keeping body and facial anatomy.

    A Mask modifier, never applied, so vertex identity survives untouched.
    """
    obj = _mesh_object(obj)
    if "alepou_hidden" not in obj.vertex_groups:
        raise HumanDataError("Object was not loaded by load_human; alepou_hidden is missing")
    for modifier in obj.modifiers:
        if modifier.name == name:
            return modifier
    modifier = obj.modifiers.new(name=name, type="MASK")
    modifier.vertex_group = "alepou_hidden"
    modifier.invert_vertex_group = True
    return modifier


def add_morph(obj: Any, target_path: str | Path, *, name: str | None = None) -> bpy.types.ShapeKey:
    """Add one .target file as a shape key.

    A morph is a sparse list of displacements applied to the basis, so the shape
    key starts as a copy of the basis and only the touched vertices move.
    """
    obj = _mesh_object(obj)
    human_data.check_vertex_count(len(obj.data.vertices))

    target_path = Path(target_path)
    offsets = human_data.parse_target(target_path)
    basis = _ensure_basis(obj)
    key_name = name or human_data.morph_name(target_path)

    existing = obj.data.shape_keys.key_blocks.get(key_name)
    if existing is not None:
        obj.shape_key_remove(existing)

    key = obj.shape_key_add(name=key_name, from_mix=False)
    key.slider_min = 0.0
    key.slider_max = 1.0
    key.value = 0.0
    for index, (dx, dy, dz) in offsets:
        key.data[index].co = basis.data[index].co + Vector((dx, dy, dz))
    return key


def add_morphs(obj: Any, root: str | Path) -> list[str]:
    """Add every .target under root as a shape key. Returns the names added."""
    added: list[str] = []
    for path in human_data.iter_targets(root):
        added.append(add_morph(obj, path).name)
    return added


def morphed_coordinates(obj: Any, indices: Iterable[int] | None = None) -> list[tuple[float, float, float]]:
    """World-space vertex positions with morphs applied and modifiers ignored.

    Deliberately not the depsgraph-evaluated mesh. Modifiers may add or remove
    vertices - a Mask hiding helpers, a render-time Subdivision - and then index
    N no longer means vertex N. That failure is silent with Subdivision, which
    yields a plausible-looking but wrong rig, so positions are resolved the same
    way the substrate defines a morph: V = V0 + sum(wi * Di).
    """
    obj = _mesh_object(obj)
    mesh = obj.data
    matrix = obj.matrix_world
    keys = mesh.shape_keys

    # Resolving only the vertices asked for matters enormously during fitting,
    # where this is called once per morph per landmark set. Computing all 19158
    # positions to read 17 of them made a Jacobian build take minutes.
    wanted = list(range(len(mesh.vertices))) if indices is None else list(indices)

    if keys is None or not keys.use_relative:
        return [tuple(matrix @ mesh.vertices[i].co) for i in wanted]

    blocks = keys.key_blocks
    basis = blocks[0]
    positions = [basis.data[i].co.copy() for i in wanted]

    for block in blocks[1:]:
        weight = block.value
        if abs(weight) < 1e-9:
            continue
        relative = block.relative_key or basis
        for slot, index in enumerate(wanted):
            delta = block.data[index].co - relative.data[index].co
            if delta.length_squared:
                positions[slot] += delta * weight

    return [tuple(matrix @ position) for position in positions]


def build_rig(
    obj: Any,
    resource_dir: str | Path | None = None,
    *,
    name: str = ARMATURE_NAME,
    collection: Any = None,
) -> bpy.types.Object:
    """Construct the armature from vertex-anchored joint definitions.

    Bone endpoints are the centroids of joint-cube vertices in the *current*
    mesh, so calling this again after reshaping produces a rig that fits the
    reshaped body. That is the whole reason the skeleton is data rather than a
    baked armature.
    """
    obj = _mesh_object(obj)
    verify_topology(obj, stage="rig construction")

    resource_dir = Path(resource_dir or obj.get(RESOURCE_PROPERTY, "") or default_resource_dir())
    skeleton = human_data.load_skeleton(resource_dir / "rigs" / "default.mhskel")
    weights = human_data.load_weights(resource_dir / "rigs" / "default_weights.mhw")

    bones = skeleton["bones"]
    joints = skeleton["joints"]

    coords = morphed_coordinates(obj)

    def endpoint(joint_name: str) -> Vector:
        if joint_name not in joints:
            raise HumanDataError("Skeleton names unknown joint {!r}".format(joint_name))
        return Vector(human_data.centroid(coords, joints[joint_name]))

    armature = bpy.data.armatures.new(name)
    rig = bpy.data.objects.new(name, armature)
    (collection or bpy.context.scene.collection).objects.link(rig)

    previous_active = bpy.context.view_layer.objects.active
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        created: dict[str, Any] = {}
        for bone_name in human_data.bone_order(bones):
            definition = bones[bone_name]
            edit_bone = armature.edit_bones.new(bone_name)
            edit_bone.head = endpoint(definition["head"])
            edit_bone.tail = endpoint(definition["tail"])
            if (edit_bone.tail - edit_bone.head).length < 1e-6:
                # A zero-length bone is silently discarded by Blender on mode
                # exit, which would leave children orphaned. Give it a minimum.
                edit_bone.tail = edit_bone.head + Vector((0.0, 0.0, 1e-4))
            created[bone_name] = edit_bone

        for bone_name, definition in bones.items():
            parent = definition.get("parent")
            if parent:
                created[bone_name].parent = created[parent]
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.context.view_layer.objects.active = previous_active

    _apply_weights(obj, weights, set(bones))

    if not any(m.type == "ARMATURE" and m.object == rig for m in obj.modifiers):
        modifier = obj.modifiers.new(name="AlepouHumanRig", type="ARMATURE")
        modifier.object = rig
    obj.parent = rig
    return rig


def _apply_weights(
    obj: bpy.types.Object, weights: dict[str, list[tuple[int, float]]], known_bones: set[str]
) -> None:
    for bone_name, entries in weights.items():
        if bone_name not in known_bones:
            continue
        group = obj.vertex_groups.get(bone_name) or obj.vertex_groups.new(name=bone_name)
        for index, weight in entries:
            group.add([index], weight, "REPLACE")


def strip_helpers(obj: Any) -> int:
    """Remove helper geometry. Export only - this is what changes vertex order.

    After this the mesh is no longer the canonical topology and no further morph
    or rig operation is valid on it, which is why it clears the signature.
    """
    import bmesh

    obj = _mesh_object(obj)
    verify_topology(obj, stage="export")

    low, high = human_data.HELPER_VERTEX_RANGE
    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()
    doomed = [v for v in bm.verts if low <= v.index <= high]
    removed = len(doomed)
    bmesh.ops.delete(bm, geom=doomed, context="VERTS")
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()

    obj[SIGNATURE_PROPERTY] = ""
    return removed


def describe(obj: Any) -> dict[str, Any]:
    """Report substrate state for the bridge's structured results."""
    obj = _mesh_object(obj)
    mesh = obj.data
    keys = mesh.shape_keys.key_blocks if mesh.shape_keys else []
    active = [k.name for k in keys if k.name != BASIS_KEY and abs(k.value) > 1e-6]
    return {
        "object": obj.name,
        "vertexCount": len(mesh.vertices),
        "canonicalTopology": len(mesh.vertices) == human_data.EXPECTED_VERTEX_COUNT,
        "topologySignature": signature_of(obj),
        "shapeKeyCount": max(0, len(keys) - 1),
        "activeShapeKeys": sorted(active),
        "resourceDir": obj.get(RESOURCE_PROPERTY, ""),
        "rig": obj.parent.name if obj.parent and obj.parent.type == "ARMATURE" else None,
    }


EYE_OBJECT_NAME = "AlepouHumanEyes"
PROXY_SOURCE_PROPERTY = "alepou_proxy_source"
PROXY_HOST_PROPERTY = "alepou_proxy_host"


def _read_obj(path: Path) -> tuple[list[list[int]], list[tuple[float, float]], list[list[int]]]:
    """Faces, UVs and per-loop UV indices from a proxy obj."""
    faces: list[list[int]] = []
    uvs: list[tuple[float, float]] = []
    uv_loops: list[list[int]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("vt "):
            parts = line.split()
            uvs.append((float(parts[1]), float(parts[2])))
        elif line.startswith("f "):
            chunks = line.split()[1:]
            loop, loop_uv = [], []
            for chunk in chunks:
                bits = chunk.split("/")
                loop.append(int(bits[0]) - 1)
                loop_uv.append(int(bits[1]) - 1 if len(bits) > 1 and bits[1] else -1)
            if len(loop) >= 3:
                faces.append(loop)
                uv_loops.append(loop_uv)
    return faces, uvs, uv_loops


def _makehuman_coordinates(obj: Any) -> list[tuple[float, float, float]]:
    return [human_data.blender_to_makehuman(p) for p in morphed_coordinates(obj)]


def add_eyes(
    obj: Any,
    resource_dir: str | Path | None = None,
    *,
    variant: str = "high-poly",
    name: str = EYE_OBJECT_NAME,
    collection: Any = None,
) -> bpy.types.Object:
    """Fit the eye proxy to the current head and link it as its own object.

    The eye envelope inside hm08 is a fitting guide, deliberately larger than
    the eyeball it positions, so it protrudes if rendered. This builds the real
    proxy instead and hides the envelope. Call refit_proxy() after changing
    morphs so the eyes keep following the head.
    """
    obj = _mesh_object(obj)
    verify_topology(obj, stage="eye fitting")

    resource_dir = Path(resource_dir or obj.get(RESOURCE_PROPERTY, "") or default_resource_dir())
    mhclo = resource_dir / "eyes" / variant / "{}.mhclo".format(variant)
    if not mhclo.is_file():
        raise HumanDataError("No eye proxy at {}".format(mhclo))

    proxy = human_data.load_proxy(mhclo)
    obj_path = (mhclo.parent / proxy.obj_file).resolve()
    if not obj_path.is_file():
        raise HumanDataError("Proxy {} names a missing mesh {}".format(mhclo, obj_path))

    faces, uvs, uv_loops = _read_obj(obj_path)
    fitted = human_data.fit_proxy(proxy, _makehuman_coordinates(obj))
    coords = [human_data.makehuman_to_blender(p) for p in fitted]

    if faces and max(max(f) for f in faces) >= len(coords):
        raise HumanDataError(
            "Proxy mesh has {} vertices but {} defines {} bindings".format(
                max(max(f) for f in faces) + 1, mhclo.name, len(coords)
            )
        )

    existing = bpy.data.objects.get(name)
    if existing is not None:
        bpy.data.objects.remove(existing, do_unlink=True)

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(coords, [], faces)
    mesh.validate(verbose=False)

    # from_pydata trusts the obj winding, which is not guaranteed consistent.
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(mesh)
    bm.free()

    if uvs and any(i >= 0 for loop in uv_loops for i in loop):
        layer = mesh.uv_layers.new(name="UVMap")
        for polygon, loop_uv in zip(mesh.polygons, uv_loops):
            for slot, uv_index in zip(polygon.loop_indices, loop_uv):
                if 0 <= uv_index < len(uvs):
                    layer.data[slot].uv = uvs[uv_index]
    mesh.update()

    eyes = bpy.data.objects.new(name, mesh)
    eyes[PROXY_SOURCE_PROPERTY] = str(mhclo)
    eyes[PROXY_HOST_PROPERTY] = obj.name
    (collection or bpy.context.scene.collection).objects.link(eyes)
    eyes.parent = obj

    # The envelope has done its job; showing it too would double the eyeball.
    # Folding it into alepou_hidden keeps a single mask rather than stacking one
    # modifier per hidden part.
    _hide_envelope(obj)
    return eyes


def _hide_envelope(obj: bpy.types.Object) -> None:
    eye_group = obj.vertex_groups.get("alepou_eyes")
    hidden = obj.vertex_groups.get("alepou_hidden")
    if eye_group is None or hidden is None:
        return
    indices = [
        v.index for v in obj.data.vertices if any(g.group == eye_group.index for g in v.groups)
    ]
    if indices:
        hidden.add(indices, 1.0, "REPLACE")


def refit_proxy(eyes: Any, host: Any = None) -> int:
    """Re-fit an existing proxy object after the host mesh changed shape."""
    eyes = _mesh_object(eyes)
    source = eyes.get(PROXY_SOURCE_PROPERTY, "")
    if not source:
        raise HumanDataError("{} was not created by add_eyes".format(eyes.name))

    host = host or eyes.parent or bpy.data.objects.get(eyes.get(PROXY_HOST_PROPERTY, ""))
    if host is None:
        raise HumanDataError("Cannot find the host mesh for {}".format(eyes.name))

    proxy = human_data.load_proxy(Path(source))
    fitted = human_data.fit_proxy(proxy, _makehuman_coordinates(host))
    if len(fitted) != len(eyes.data.vertices):
        raise HumanDataError(
            "Proxy binding count {} no longer matches the {} vertices in {}".format(
                len(fitted), len(eyes.data.vertices), eyes.name
            )
        )
    for vertex, position in zip(eyes.data.vertices, fitted):
        vertex.co = Vector(human_data.makehuman_to_blender(position))
    eyes.data.update()
    return len(fitted)


def hide_group(obj: Any, group_name: str, *, name: str | None = None) -> Any:
    """Mask one vertex group away without changing topology."""
    obj = _mesh_object(obj)
    if group_name not in obj.vertex_groups:
        raise HumanDataError("Object has no vertex group {!r}".format(group_name))
    modifier_name = name or "AlepouHide_{}".format(group_name)
    for modifier in obj.modifiers:
        if modifier.name == modifier_name:
            return modifier
    modifier = obj.modifiers.new(name=modifier_name, type="MASK")
    modifier.vertex_group = group_name
    modifier.invert_vertex_group = True
    return modifier


def eye_centres(obj: Any) -> tuple[Any, Any]:
    """World-space centres of the left and right eyes, as (viewer_left, viewer_right).

    Split by side rather than by vertex index range, so it keeps working if the
    pack's group layout ever changes.
    """
    obj = _mesh_object(obj)
    group = obj.vertex_groups.get("alepou_eyes")
    if group is None:
        raise HumanDataError("Object has no alepou_eyes group; load it with load_human")

    members = [
        v.index for v in obj.data.vertices
        if any(g.group == group.index for g in v.groups)
    ]
    left: list[Any] = []
    right: list[Any] = []
    for position in morphed_coordinates(obj, members):
        vector = Vector(position)
        (left if vector.x < 0 else right).append(vector)

    if not left or not right:
        raise HumanDataError("Could not separate the eyes by side")

    def centre(points):
        total = Vector((0.0, 0.0, 0.0))
        for point in points:
            total += point
        return total / len(points)

    # The camera looks along +Y with +Z up, so world +X falls on the right of
    # the image. Returned in viewer order - image-left first - to match the
    # normalised image coordinates the reference module works in.
    return (centre(left), centre(right))


def landmarks(obj: Any, resource_dir: str | Path | None = None) -> dict[str, Any]:
    """Named landmark positions on the current, morphed mesh.

    The same names can be marked on a photograph, so model and reference are
    measured with one vocabulary instead of two sets of ad-hoc extents. The
    ad-hoc version is what made the t-939 comparison wrong: the model's face
    width included its ears and the photograph's did not.
    """
    import json

    obj = _mesh_object(obj)
    resource_dir = Path(resource_dir or obj.get(RESOURCE_PROPERTY, "") or default_resource_dir())
    anatomy_path = resource_dir / "anatomy.json"
    if not anatomy_path.is_file():
        raise HumanDataError("No anatomy map at {}".format(anatomy_path))

    anatomy = json.loads(anatomy_path.read_text(encoding="utf-8"))
    defined = anatomy.get("landmarks")
    if not defined:
        raise HumanDataError(
            "{} has no landmarks; regenerate it with scripts/build_landmarks.py".format(anatomy_path)
        )

    wanted: list[int] = []
    names: list[str] = []
    vertex_count = len(obj.data.vertices)
    for name, entry in defined.items():
        index = entry.get("vertex")
        if isinstance(index, int) and 0 <= index < vertex_count:
            wanted.append(index)
            names.append(name)

    coords = morphed_coordinates(obj, wanted)
    found: dict[str, Any] = {
        name: Vector(position) for name, position in zip(names, coords)
    }

    # The pupils are the eye proxy centres, not a base-mesh vertex.
    try:
        left, right = eye_centres(obj)
        found["pupil_left"], found["pupil_right"] = left, right
    except HumanDataError:
        pass
    return found


def measure(obj: Any, resource_dir: str | Path | None = None) -> dict[str, Any]:
    """Dimensionless proportions from the named landmarks.

    Ratios only. A photograph carries no scale, so absolute millimetres cannot be
    compared against one; ratios and angles can.
    """
    points = landmarks(obj, resource_dir)
    distances: dict[str, float] = {}
    for name, (a, b) in human_data.LANDMARK_PAIRS.items():
        if a in points and b in points:
            distances[name] = float((points[a] - points[b]).length)

    baseline = distances.get("interpupillary")
    ratios = {}
    if baseline and baseline > 1e-9:
        ratios = {
            name: round(value / baseline, 4)
            for name, value in distances.items()
            if name != "interpupillary"
        }
    return {
        "distances_m": {k: round(v, 5) for k, v in distances.items()},
        "ratiosToInterpupillary": ratios,
        "landmarksFound": sorted(points),
    }


def compare_to_reference(
    obj: Any,
    marks: dict[str, Any],
    resource_dir: str | Path | None = None,
    *,
    baseline: str = "interpupillary",
) -> dict[str, Any]:
    """Report the same named proportions on the mesh and on a marked photograph.

    A tool for judgement, not a solver. It says the model is 14 percent wider
    across the cheekbones than the reference and leaves which morph to reach for,
    and by how much, to whoever is doing the work. Six landmarks cannot describe
    a face, so a number here is a hint to investigate, never an instruction.

    Photograph marks are normalised image coordinates, so both columns are
    dimensionless ratios against the baseline pair. Photograph values are
    projected 2D and mesh values are true 3D, so a pair that runs mostly in depth
    - anything involving the nose tip or chin - will disagree for reasons that
    are honest projection rather than shape. Those are flagged rather than
    silently compared.
    """
    points = landmarks(obj, resource_dir)

    def photo_distance(a: str, b: str) -> float | None:
        if a not in marks or b not in marks:
            return None
        ax, ay = float(marks[a][0]), float(marks[a][1])
        bx, by = float(marks[b][0]), float(marks[b][1])
        return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5

    def mesh_distance(a: str, b: str) -> float | None:
        if a not in points or b not in points:
            return None
        return float((points[a] - points[b]).length)

    pairs = human_data.LANDMARK_PAIRS
    if baseline not in pairs:
        raise HumanDataError("Unknown baseline pair {!r}".format(baseline))

    photo_base = photo_distance(*pairs[baseline])
    mesh_base = mesh_distance(*pairs[baseline])
    if not photo_base or not mesh_base:
        raise HumanDataError(
            "The baseline pair {} is not marked on both sides; nothing can be "
            "compared without it".format(baseline)
        )

    rows: list[dict[str, Any]] = []
    for name, (a, b) in pairs.items():
        if name == baseline:
            continue
        photo = photo_distance(a, b)
        mesh = mesh_distance(a, b)
        if photo is None or mesh is None:
            continue
        photo_ratio = photo / photo_base
        mesh_ratio = mesh / mesh_base
        depth = max(
            abs(points[a].y - points[b].y) / max(1e-9, (points[a] - points[b]).length),
            0.0,
        )
        rows.append({
            "measure": name,
            "reference": round(photo_ratio, 4),
            "model": round(mesh_ratio, 4),
            "modelOverReference": round(mesh_ratio / photo_ratio, 4) if photo_ratio else None,
            "percentDifference": round(100.0 * (mesh_ratio / photo_ratio - 1.0), 1)
            if photo_ratio else None,
            "depthFraction": round(depth, 2),
            "caution": "runs mostly in depth; a frontal photograph foreshortens it"
            if depth > 0.5 else None,
        })

    rows.sort(key=lambda row: -abs(row.get("percentDifference") or 0.0))
    return {
        "baseline": baseline,
        "baselineMarked": sorted(pairs[baseline]),
        "rows": rows,
        "note": (
            "Ratios against the baseline pair, so both columns are scale free. A "
            "positive percentDifference means the model is larger than the "
            "reference in that proportion. These are hints for judgement: they do "
            "not say which morph to use, and they cannot see anything the marked "
            "landmarks do not touch."
        ),
    }
