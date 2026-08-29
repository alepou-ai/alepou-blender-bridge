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

    print("describe")
    report = human.describe(obj)
    check("describe reports canonical", report["canonicalTopology"] is True)
    check("describe counts shape keys", report["shapeKeyCount"] == 3, report["shapeKeyCount"])
    check("describe lists active morph", "caucasian-male-old" in report["activeShapeKeys"])

    print("")
    print("{} checks, {} failures".format(checks, len(failures)))
    if failures:
        for name in failures:
            print("  failed: {}".format(name))
        sys.exit(1)
    print("HUMAN SUBSTRATE SMOKE OK")


main()
