"""Ground-truth gate for the weight solve.

Run before trusting any photograph fit.

Set known morph values, project the landmarks, reset to base, then ask the
fitter to recover those landmark positions. If it cannot recover its own
projection it will certainly not fit a photograph.

    blender --background --factory-startup --python recover.py
"""

import json
import sys
from pathlib import Path

import bpy
from mathutils import Vector

BRIDGE = Path("D:/alepou.ai/alepou-blender-bridge")
sys.path.insert(0, str(BRIDGE / "src"))
sys.path.insert(0, str(BRIDGE / "extension"))

PACK = Path("D:/alepou.ai/terminal-manager/agent/vendor/blender/resources/human_v0")

from alepou_blender_bridge import fit, human, reference  # noqa: E402

TRUTH = {
    "nose-hump-incr": 0.60,
    "nose-scale-vert-incr": 0.45,
    "chin-prominent-decr": 0.40,
    "head-scale-horiz-decr": 0.35,
    "mouth-scale-horiz-incr": 0.30,
    "l-cheek-bones-incr": 0.40,
    "r-cheek-bones-incr": 0.40,
}

MARKED = ["pupil_left", "pupil_right", "pronasale", "menton",
          "cheilion_left", "cheilion_right", "zygion_left", "zygion_right"]


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.resolution_x = scene.render.resolution_y = 1000

    obj = human.load_human(PACK)
    human.add_eyes(obj, PACK)

    points = human.landmarks(obj, PACK)
    centre = (points["pupil_left"] + points["pupil_right"]) / 2.0
    camera_data = bpy.data.cameras.new("cam")
    camera_data.lens = 85.0
    camera = bpy.data.objects.new("cam", camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    camera.location = centre + Vector((0.0, -0.75, 0.0))
    camera.rotation_euler = (centre - camera.location).to_track_quat("-Z", "Y").to_euler()
    bpy.context.view_layer.update()

    # Ground truth: apply known morphs and record where the landmarks land.
    names = fit.ensure_morphs(obj, fit.candidate_morphs(PACK))
    print("morph pool: {}".format(len(names)))
    keys = obj.data.shape_keys.key_blocks
    missing = [n for n in TRUTH if n not in keys]
    if missing:
        print("MISSING FROM POOL: {}".format(missing))
    for name, value in TRUTH.items():
        if name in keys:
            keys[name].value = value
    bpy.context.view_layer.update()

    truth_points = human.landmarks(obj, PACK)
    marks = {n: reference.project(scene, camera, truth_points[n]) for n in MARKED}
    face_ids = [v.index for v in obj.data.vertices if v.co.z > 0.55 and v.index < 13380]
    truth_face = human.morphed_coordinates(obj, face_ids)

    # Reset and try to get back.
    for name in TRUTH:
        if name in keys:
            keys[name].value = 0.0
    bpy.context.view_layer.update()

    result = fit.fit_landmarks(scene, camera, obj, PACK, marks,
                               rounds=3, resolve_camera=False)

    print("\n--- convergence ---")
    for row in result["history"]:
        print("  round {round}: kept {morphsKept}/{morphsConsidered}  rms {rmsBefore} -> {rmsAfter}".format(**row))
    print("final rms {} worst {}".format(result["rmsError"], result["worstPointError"]))

    print("\n--- recovered vs truth ---")
    recovered = result["weights"]
    for name, value in sorted(TRUTH.items()):
        print("  {:<26} truth {:.2f}   got {:+.2f}".format(name, value, recovered.get(name, 0.0)))
    spurious = {k: v for k, v in recovered.items() if k not in TRUTH and abs(v) > 0.15}
    print("  large weights not in truth: {}".format(len(spurious)))
    for name, value in sorted(spurious.items(), key=lambda kv: -abs(kv[1]))[:6]:
        print("    {:<26} {:+.2f}".format(name, value))

    # Landmarks agreeing says nothing about the face between them.
    got_face = human.morphed_coordinates(obj, face_ids)
    deltas = [
        ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5
        for a, b in zip(truth_face, got_face)
    ]
    mean_mm = 1000.0 * sum(deltas) / len(deltas)
    max_mm = 1000.0 * max(deltas)
    print("")
    print("--- face geometry vs truth ({} verts above z=0.55) ---".format(len(face_ids)))
    print("  mean vertex error {:.2f} mm   worst {:.2f} mm".format(mean_mm, max_mm))

    ok = result["rmsError"] < 0.004
    print("\nRECOVERY {}".format("OK" if ok else "WEAK"))


main()
