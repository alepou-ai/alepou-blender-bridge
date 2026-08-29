"""Regenerate the landmark block in the pack's anatomy.json.

    blender --background --factory-startup --python scripts/build_landmarks.py -- <pack>

Landmarks are derived from the rest mesh, so this only needs re-running when the
pack's geometry or region layout changes.
"""

import json
import sys
from pathlib import Path

import bpy

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "extension"))

from alepou_blender_bridge import human, human_data  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
PACK = Path(argv[0]) if argv else human.default_resource_dir()

bpy.ops.wm.read_factory_settings(use_empty=True)
obj = human.load_human(PACK)
coords = [tuple(v.co) for v in obj.data.vertices]

anatomy_path = PACK / "anatomy.json"
anatomy = json.loads(anatomy_path.read_text(encoding="utf-8"))
regions = {name: [i for lo, hi in entry["vertexRanges"] for i in range(lo, hi + 1)]
           for name, entry in anatomy["regions"].items()}

landmarks = human_data.derive_landmarks(coords, regions)
anatomy["landmarks"] = landmarks
anatomy["landmarkPairs"] = {k: list(v) for k, v in human_data.LANDMARK_PAIRS.items()}
anatomy_path.write_text(json.dumps(anatomy, indent=2) + "\n", encoding="utf-8")

print("derived {} landmarks".format(len(landmarks)))
for name in sorted(landmarks):
    entry = landmarks[name]
    print("  {:<16} vertex {:<6} at {}".format(name, entry["vertex"], entry["restPosition"]))
