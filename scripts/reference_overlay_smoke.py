"""Exercise the reference-overlay module inside a real Blender.

Run headless:

    blender --background --factory-startup --python scripts/reference_overlay_smoke.py -- <photo> <outdir>
"""

import sys
from pathlib import Path

import bpy
from mathutils import Vector

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "extension"))

PACK = Path("D:/alepou.ai/terminal-manager/agent/vendor/blender/resources/human_v0")

from alepou_blender_bridge import human, reference  # noqa: E402
from alepou_blender_bridge.reference import ReferenceError  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
PHOTO = Path(argv[0])
OUT = Path(argv[1] if len(argv) > 1 else ".")
OUT.mkdir(parents=True, exist_ok=True)

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
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT"

    print("reference image")
    image = reference.load_image(PHOTO)
    check("photo loads", image.size[0] > 0 and image.size[1] > 0, image.size[:])
    reused = reference.load_image(PHOTO)
    check("loading twice reuses the datablock", reused is image)

    pixels = reference.image_pixels(image)
    check("pixels shaped height,width,4",
          pixels.shape == (image.size[1], image.size[0], 4), pixels.shape)

    print("resolution matching")
    width, height = reference.match_resolution_to_reference(scene, PHOTO)
    check("render resolution follows the photo",
          (scene.render.resolution_x, scene.render.resolution_y) == (width, height),
          (scene.render.resolution_x, scene.render.resolution_y))
    check("resolution percentage is full", scene.render.resolution_percentage == 100)

    print("subject and camera")
    obj = human.load_human(PACK)
    human.hide_non_render_geometry(obj)
    obj.data.polygons.foreach_set("use_smooth", [True] * len(obj.data.polygons))
    obj.data.update()

    zs = [v.co.z for v in obj.data.vertices]
    top = max(zs)
    head = [v.co for v in obj.data.vertices if v.co.z > top - 0.26]
    centre = sum(head, Vector((0, 0, 0))) / len(head)

    camera_data = bpy.data.cameras.new("cam")
    camera_data.lens = 85.0
    camera = bpy.data.objects.new("cam", camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    camera.location = centre + Vector((0.0, -0.62, 0.03))
    camera.rotation_euler = (centre - camera.location).to_track_quat("-Z", "Y").to_euler()

    lamp_data = bpy.data.lights.new("key", type="AREA")
    lamp_data.energy = 18
    lamp_data.size = 0.5
    lamp = bpy.data.objects.new("key", lamp_data)
    lamp.location = centre + Vector((-0.5, -0.62, 0.32))
    lamp.rotation_euler = (centre - lamp.location).to_track_quat("-Z", "Y").to_euler()
    scene.collection.objects.link(lamp)

    print("camera background")
    background = reference.set_camera_background(camera, PHOTO, alpha=0.4)
    check("background attached", background.image is image)
    check("background alpha applied", abs(background.alpha - 0.4) < 1e-6, background.alpha)
    check("camera shows backgrounds", camera.data.show_background_images is True)
    again = reference.set_camera_background(camera, PHOTO, alpha=0.6)
    check("re-attaching does not duplicate", len(camera.data.background_images) == 1,
          len(camera.data.background_images))
    check("alpha updated on re-attach", abs(again.alpha - 0.6) < 1e-6, again.alpha)

    print("overlays")
    written = []
    for mode in reference.OVERLAY_MODES:
        path = OUT / "overlay-{}.png".format(mode)
        result = reference.render_against_reference(PHOTO, path, mode=mode)
        ok = result.is_file() and result.stat().st_size > 1000
        check("mode {} writes an image".format(mode), ok,
              result.stat().st_size if result.is_file() else "missing")
        written.append(result)

    check("overlay matches the photo resolution",
          reference.image_pixels(reference.load_image(written[0])).shape[:2] == (height, width),
          reference.image_pixels(reference.load_image(written[0])).shape[:2])

    print("guards")
    rejected = False
    try:
        reference.composite(written[0], PHOTO, OUT / "bad.png", mode="nonsense")
    except ReferenceError:
        rejected = True
    check("unknown mode rejected", rejected)

    missing = False
    try:
        reference.load_image(OUT / "does-not-exist.png")
    except ReferenceError:
        missing = True
    check("missing reference rejected", missing)

    mismatched = False
    scene.render.resolution_x = max(16, width // 2)
    scene.render.resolution_y = max(16, height // 2)
    small = OUT / "small.png"
    scene.render.filepath = str(small)
    scene.render.film_transparent = True
    bpy.ops.render.render(write_still=True)
    try:
        reference.composite(small, PHOTO, OUT / "bad2.png", mode="over")
    except ReferenceError as error:
        mismatched = "match_resolution_to_reference" in str(error)
    check("size mismatch is refused with a fix in the message", mismatched)

    print("alignment report")
    reference.match_resolution_to_reference(scene, PHOTO)
    keep = OUT / "aligned.render.png"
    reference.render_against_reference(PHOTO, OUT / "aligned.png", mode="over", keep_render=keep)
    report = reference.alignment_report(keep, PHOTO)
    check("report counts subject pixels", report["subjectPixels"] > 1000, report["subjectPixels"])
    check("report gives a centroid",
          0.0 < report["centroid"]["x"] < 1.0 and 0.0 < report["centroid"]["y"] < 1.0,
          report["centroid"])
    check("report warns pose is unsolved", "pose is not solved" in report["note"])

    print("")
    print("{} checks, {} failures".format(checks, len(failures)))
    if failures:
        for name in failures:
            print("  failed: {}".format(name))
        sys.exit(1)
    print("REFERENCE OVERLAY SMOKE OK")


main()
