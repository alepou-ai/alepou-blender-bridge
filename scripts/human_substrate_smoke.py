"""Exercise the human substrate inside a real Blender.

Run headless:

    blender --background --factory-startup --python scripts/human_substrate_smoke.py

Checks the things unit tests cannot: that the mesh loads at the canonical
topology, that morphs actually move the vertices they claim to, that the rig
builds from joint centroids and deforms, and that the topology guard fires when
someone breaks vertex identity.
"""

import sys
from pathlib import Path

import bpy

REPO = Path(__file__).resolve().parents[1]
# The built extension bundles spatial as a wheel; from a source checkout it
# lives under src/, and the package __init__ imports it.
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "extension"))

PACK = Path("D:/alepou.ai/terminal-manager/agent/vendor/blender/resources/human_v0")

from alepou_blender_bridge import human, human_data  # noqa: E402
from alepou_blender_bridge.human_data import HumanDataError  # noqa: E402

failures = []
checks = 0


def check(label, condition, detail=""):
    global checks
    checks += 1
    if condition:
        print("  ok   {}".format(label))
    else:
        print("  FAIL {} {}".format(label, detail))
        failures.append(label)


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)

    print("load_human")
    obj = human.load_human(PACK)
    mesh = obj.data
    check("canonical vertex count", len(mesh.vertices) == 19158, len(mesh.vertices))
    check("faces built", len(mesh.polygons) == 18486, len(mesh.polygons))
    check("helper group tagged", "alepou_helpers" in obj.vertex_groups)
    for group in ("alepou_anatomy", "alepou_clothing_helpers", "alepou_joints", "alepou_hidden"):
        check("group {} exists".format(group), group in obj.vertex_groups)

    # hm08 keeps eyes, teeth, tongue and eyelashes in the helper range. Masking
    # the whole range leaves empty eye sockets, which is what the first pilot
    # rendered, so anatomy must be separated from clothing scaffolding.
    def group_size(name):
        index = obj.vertex_groups[name].index
        return sum(1 for v in mesh.vertices if any(g.group == index for g in v.groups))

    anatomy = group_size("alepou_anatomy")
    hidden = group_size("alepou_hidden")
    check("anatomy group holds eyes/teeth/tongue/lashes", 600 < anatomy < 900, anatomy)
    check("hidden group holds clothing and joints", hidden > 4000, hidden)
    def group_members(name):
        index = obj.vertex_groups[name].index
        return {v.index for v in mesh.vertices if any(g.group == index for g in v.groups)}

    overlap = group_members("alepou_anatomy") & group_members("alepou_hidden")
    check("anatomy and hidden sets are disjoint", not overlap, len(overlap))

    mask = human.hide_non_render_geometry(obj)
    check("hide modifier added", mask.type == "MASK")
    d = bpy.context.evaluated_depsgraph_get()
    visible = len(obj.evaluated_get(d).to_mesh().vertices)
    check("render keeps body plus anatomy", 13380 < visible < 14400, visible)
    obj.modifiers.remove(mask)

    # Height should be a believable human in metres, not decimetres.
    zs = [v.co.z for v in mesh.vertices]
    height = max(zs) - min(zs)
    check("height in metres", 1.5 < height < 2.0, "{:.3f} m".format(height))

    # Nose tip vertex 297 must sit on the midline, in front of the head.
    nose = mesh.vertices[297].co
    check("nose tip on midline", abs(nose.x) < 1e-4, nose.x)
    check("nose tip faces -Y", nose.y < 0, nose.y)

    print("morphs")
    key = human.add_morph(obj, PACK / "targets" / "nose" / "nose-scale-vert-incr.target")
    check("shape key created", key.name == "nose-scale-vert-incr")
    basis = mesh.shape_keys.key_blocks["Basis"]
    moved = sum(
        1 for i in range(len(mesh.vertices)) if (key.data[i].co - basis.data[i].co).length > 1e-9
    )
    check("morph moves only its own vertices", 0 < moved < 1000, moved)

    # A vertical morph must move mostly in Z, which is the axis mapping paying off.
    deltas = [key.data[i].co - basis.data[i].co for i in range(len(mesh.vertices))]
    vertical = sum(abs(d.z) for d in deltas)
    lateral = sum(abs(d.x) for d in deltas)
    depth = sum(abs(d.y) for d in deltas)
    check("vertical morph moves in Z", vertical > 10 * max(lateral, depth),
          "z={:.4f} x={:.4f} y={:.4f}".format(vertical, lateral, depth))

    depth_key = human.add_morph(obj, PACK / "targets" / "nose" / "nose-scale-depth-incr.target")
    d_deltas = [depth_key.data[i].co - basis.data[i].co for i in range(len(mesh.vertices))]
    d_depth = sum(abs(d.y) for d in d_deltas)
    d_vert = sum(abs(d.z) for d in d_deltas)
    check("depth morph moves in Y", d_depth > 10 * d_vert,
          "y={:.4f} z={:.4f}".format(d_depth, d_vert))

    print("rig")
    rig = human.build_rig(obj, PACK)
    check("armature built", rig.type == "ARMATURE")
    check("bone count", len(rig.data.bones) == 163, len(rig.data.bones))
    check("jaw present", "jaw" in rig.data.bones)
    check("tongue chain present", len([b for b in rig.data.bones if b.name.startswith("tongue")]) >= 7)
    check("mesh parented to rig", obj.parent == rig)
    check("armature modifier", any(m.type == "ARMATURE" for m in obj.modifiers))
    check("weight groups created", len(obj.vertex_groups) > 100, len(obj.vertex_groups))

    jaw = rig.data.bones.get("jaw")
    top = max(zs)
    if jaw:
        check("jaw bone has length", jaw.length > 1e-5, jaw.length)
        # The jaw should sit in the head, near the top of the body.
        check("jaw is up in the head", jaw.head_local.z > top * 0.7,
              "{:.3f} of top {:.3f}".format(jaw.head_local.z, top))

    print("rig follows a reshaped mesh")
    macro = human.add_morph(
        obj, PACK / "targets" / "macrodetails" / "caucasian-male-old.target"
    )
    macro.value = 1.0
    bpy.context.view_layer.update()
    rebuilt = human.build_rig(obj, PACK, name="AlepouHumanRig2")
    jaw2 = rebuilt.data.bones.get("jaw")
    if jaw and jaw2:
        shifted = (jaw2.head_local - jaw.head_local).length
        check("rebuilt rig tracks the morph", shifted > 1e-5, "{:.5f} m".format(shifted))

    print("eye proxy")
    eyes = human.add_eyes(obj, PACK)
    check("eye object created", eyes is not None and eyes.type == "MESH")
    check("eye mesh has geometry", len(eyes.data.vertices) > 100, len(eyes.data.vertices))
    check("eyes parented to the head", eyes.parent == obj)

    def bounds(mesh_object):
        cos = [v.co for v in mesh_object.data.vertices]
        return (min(c.x for c in cos), max(c.x for c in cos),
                min(c.z for c in cos), max(c.z for c in cos))

    ex0, ex1, ez0, ez1 = bounds(eyes)
    check("eyes sit up in the head", ez0 > top * 0.75, "{:.3f} vs top {:.3f}".format(ez0, top))
    check("eyes straddle the midline", ex0 < 0 < ex1, "{:.3f}..{:.3f}".format(ex0, ex1))
    check("eyes are a plausible width", 0.03 < (ex1 - ex0) < 0.12, "{:.3f} m".format(ex1 - ex0))

    envelope_hidden = False
    hidden_group = obj.vertex_groups["alepou_hidden"].index
    eye_group = obj.vertex_groups["alepou_eyes"].index
    eye_ids = {v.index for v in mesh.vertices if any(g.group == eye_group for g in v.groups)}
    hidden_ids = {v.index for v in mesh.vertices if any(g.group == hidden_group for g in v.groups)}
    envelope_hidden = eye_ids and eye_ids <= hidden_ids
    check("eye envelope folded into the hidden group", bool(envelope_hidden))

    # The whole point of fitting rather than loading: the eyes must follow.
    before = [v.co.copy() for v in eyes.data.vertices]
    widen = human.add_morph(obj, PACK / "targets" / "head" / "head-scale-horiz-incr.target")
    widen.slider_max = 1.0
    widen.value = 1.0
    bpy.context.view_layer.update()
    moved_count = human.refit_proxy(eyes, obj)
    shifted = max((v.co - before[i]).length for i, v in enumerate(eyes.data.vertices))
    check("refit returns every binding", moved_count == len(eyes.data.vertices), moved_count)
    check("eyes follow a head morph", shifted > 1e-4, "{:.5f} m".format(shifted))
    widen.value = 0.0
    bpy.context.view_layer.update()
    human.refit_proxy(eyes, obj)

    print("rig survives a modifier stack")
    # A Mask hiding helpers and a render-time Subdivision both change the
    # evaluated vertex count. Reading positions from the evaluated mesh raised
    # IndexError under Mask and silently produced a wrong rig under Subdivision,
    # so the rig must resolve positions from shape keys instead.
    mask = obj.modifiers.new(name="HideHelpers", type="MASK")
    mask.vertex_group = "alepou_helpers"
    mask.invert_vertex_group = True
    subsurf = obj.modifiers.new(name="RenderSubdiv", type="SUBSURF")
    subsurf.levels = 1
    bpy.context.view_layer.update()

    modified_rig = None
    try:
        modified_rig = human.build_rig(obj, PACK, name="AlepouHumanRigModifiers")
    except Exception as error:  # noqa: BLE001 - the smoke reports, it does not raise
        check("rig builds with Mask and Subdivision present", False, repr(error))
    if modified_rig is not None:
        check("rig builds with Mask and Subdivision present", True)
        jaw3 = modified_rig.data.bones.get("jaw")
        if jaw2 and jaw3:
            drift = (jaw3.head_local - jaw2.head_local).length
            check("modifiers do not move the rig", drift < 1e-6, "{:.6f} m".format(drift))

    obj.modifiers.remove(mask)
    obj.modifiers.remove(subsurf)

    print("landmarks")
    points = human.landmarks(obj, PACK)
    check("landmark set derived", len(points) >= 15, len(points))
    for name in ("pronasale", "nasion", "glabella", "menton", "stomion",
                 "zygion_left", "zygion_right", "pupil_left", "pupil_right"):
        check("landmark {} present".format(name), name in points)

    if {"glabella", "nasion", "pronasale", "menton", "subnasale"} <= set(points):
        check("glabella sits above nasion",
              points["glabella"].z > points["nasion"].z,
              "{:.3f} vs {:.3f}".format(points["glabella"].z, points["nasion"].z))
        check("nasion sits above the nose tip",
              points["nasion"].z > points["pronasale"].z)
        check("nose tip is the most forward face point",
              points["pronasale"].y < min(points[n].y for n in
                                          ("glabella", "subnasale", "menton", "stomion")))
        check("menton is the lowest landmark",
              points["menton"].z == min(p.z for p in points.values()))

    if {"zygion_left", "zygion_right"} <= set(points):
        check("zygion straddles the midline",
              points["zygion_left"].x < 0 < points["zygion_right"].x)

    proportions = human.measure(obj, PACK)
    check("measure returns ratios", bool(proportions["ratiosToInterpupillary"]),
          proportions["ratiosToInterpupillary"])
    check("bizygomatic is wider than interpupillary",
          proportions["ratiosToInterpupillary"].get("bizygomatic", 0) > 1.0,
          proportions["ratiosToInterpupillary"].get("bizygomatic"))

    # Landmarks must track morphs, or they measure the rest pose forever.
    widen = obj.data.shape_keys.key_blocks.get("head-scale-horiz-incr")
    if widen is None:
        widen = human.add_morph(obj, PACK / "targets" / "head" / "head-scale-horiz-incr.target")
    widen.slider_max = 1.0
    widen.value = 1.0
    bpy.context.view_layer.update()
    widened = human.measure(obj, PACK)
    check("widening the head changes the measured ratio",
          abs(widened["ratiosToInterpupillary"].get("bizygomatic", 0)
              - proportions["ratiosToInterpupillary"].get("bizygomatic", 0)) > 1e-3,
          "{} -> {}".format(proportions["ratiosToInterpupillary"].get("bizygomatic"),
                            widened["ratiosToInterpupillary"].get("bizygomatic")))
    widen.value = 0.0
    bpy.context.view_layer.update()

    print("topology guard")
    signature = human.verify_topology(obj)
    check("verify passes on canonical mesh", bool(signature))

    # Blender itself refuses to apply a destructive modifier to a mesh that has
    # shape keys, which blocks the most common way to lose vertex identity once
    # morphs are loaded. It does not block bmesh edits, so that is what the
    # guard has to catch.
    blocked_by_blender = False
    with_keys = obj.copy()
    with_keys.data = obj.data.copy()
    bpy.context.scene.collection.objects.link(with_keys)
    bpy.context.view_layer.objects.active = with_keys
    subsurf = with_keys.modifiers.new(name="Subdivision", type="SUBSURF")
    subsurf.levels = 1
    try:
        bpy.ops.object.modifier_apply(modifier=subsurf.name)
    except RuntimeError:
        blocked_by_blender = True
    check("Blender blocks applying a modifier over shape keys", blocked_by_blender)

    # Now break vertex identity the way an agent plausibly would: direct bmesh
    # work, which Blender permits.
    import bmesh

    broken = obj.copy()
    broken.data = obj.data.copy()
    broken.shape_key_clear()
    bpy.context.scene.collection.objects.link(broken)
    bm = bmesh.new()
    bm.from_mesh(broken.data)
    bmesh.ops.subdivide_edges(bm, edges=bm.edges[:32], cuts=1, use_grid_fill=False)
    bm.to_mesh(broken.data)
    bm.free()
    broken.data.update()
    check("bmesh edit changed the vertex count",
          len(broken.data.vertices) != 19158, len(broken.data.vertices))

    guarded = False
    try:
        human.verify_topology(broken, stage="rig construction")
    except HumanDataError as error:
        guarded = "Topology drift" in str(error)
    check("guard fires on a bmesh topology edit", guarded)

    rig_guarded = False
    try:
        human.build_rig(broken, PACK, name="ShouldNotExist")
    except HumanDataError:
        rig_guarded = True
    check("rig construction refuses a drifted mesh", rig_guarded)

    print("proxy authoring")
    # Bind a patch lifted off the scalp, then read it back from disk. The whole
    # value of binding is that the asset survives a change to the face, so the
    # test changes the face.
    import tempfile

    coords = human.morphed_coordinates(obj)
    crown = max(coords[v][2] for v in range(human_data.BODY_VERTEX_RANGE[1] + 1))
    patch = sorted(
        v for v in range(human_data.BODY_VERTEX_RANGE[1] + 1)
        if coords[v][2] > crown - 0.05
    )[:60]
    check("scalp patch found", len(patch) == 60, len(patch))

    lifted = bpy.data.meshes.new("SmokePatch")
    lifted.from_pydata(
        [(coords[v][0], coords[v][1], coords[v][2] + 0.01) for v in patch],
        [], [[0, 1, 2]],
    )
    lifted.update()
    blob = bpy.data.objects.new("SmokePatch", lifted)
    bpy.context.scene.collection.objects.link(blob)

    out_dir = Path(tempfile.mkdtemp())
    info = human.bind_proxy(obj, blob, out_dir, name="smoke-patch")
    check("bind wrote a proxy", Path(info["mhclo"]).is_file())
    check("bind wrote a mesh", Path(info["obj"]).is_file())
    check("bind recorded every vertex", info["vertices"] == 60, info["vertices"])
    check(
        "bind distance matches the lift",
        abs(info["maxBindDistance"] - 0.01) < 0.0015,
        info["maxBindDistance"],
    )
    check(
        "bind used body triangles only",
        info["hostTriangles"] < 27000,
        info["hostTriangles"],
    )

    definition = human_data.load_proxy(info["mhclo"])
    check("proxy reads back", len(definition.fits) == 60, len(definition.fits))
    check("proxy records all three scale axes", len(definition.scale_refs) == 3)
    check(
        "proxy binds inside the body range",
        all(v <= human_data.BODY_VERTEX_RANGE[1] for f in definition.fits for v in f[:3]),
    )

    bpy.data.objects.remove(blob, do_unlink=True)
    fitted = human.add_proxy(obj, info["mhclo"], name="SmokeFitted")
    check("fitted proxy has the authored vertex count", len(fitted.data.vertices) == 60)
    seated = [tuple(v.co) for v in fitted.data.vertices]
    check(
        "re-fitting reproduces the authored positions",
        max(
            abs(seated[i][2] - (coords[patch[i]][2] + 0.01)) for i in range(60)
        ) < 0.001,
    )

    before = [tuple(v.co) for v in fitted.data.vertices]
    tall = human.add_morph(obj, next(PACK.rglob("head-scale-vert-incr.target")))
    tall.slider_min, tall.slider_max = -1.5, 1.5
    tall.value = 1.0
    bpy.context.view_layer.update()
    human.refit_proxy(fitted, obj)
    after = [tuple(v.co) for v in fitted.data.vertices]
    check(
        "a bound proxy follows the face",
        max(abs(a[2] - b[2]) for a, b in zip(after, before)) > 0.005,
    )
    # Remove it again rather than just zeroing it: describe() below counts
    # shape keys, and leaving this one behind makes that check fail.
    obj.shape_key_remove(tall)
    bpy.context.view_layer.update()

    print("describe")
    report = human.describe(obj)
    check("describe reports canonical", report["canonicalTopology"] is True)
    check("describe counts shape keys", report["shapeKeyCount"] == 4, report["shapeKeyCount"])
    check("describe lists active morph", "caucasian-male-old" in report["activeShapeKeys"])

    print("")
    print("{} checks, {} failures".format(checks, len(failures)))
    if failures:
        for name in failures:
            print("  failed: {}".format(name))
        sys.exit(1)
    print("HUMAN SUBSTRATE SMOKE OK")


main()
