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


def morphed_coordinates(obj: Any) -> list[tuple[float, float, float]]:
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

    if keys is None or not keys.use_relative:
        return [tuple(matrix @ v.co) for v in mesh.vertices]

    blocks = keys.key_blocks
    reference = blocks[0]
    positions = [reference.data[i].co.copy() for i in range(len(mesh.vertices))]

    for block in blocks[1:]:
        weight = block.value
        if abs(weight) < 1e-9:
            continue
        relative = block.relative_key or reference
        for index in range(len(positions)):
            delta = block.data[index].co - relative.data[index].co
            if delta.length_squared:
                positions[index] += delta * weight

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
