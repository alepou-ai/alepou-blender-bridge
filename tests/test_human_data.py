"""Tests for the pure-Python half of the human substrate.

These run without Blender. The bpy half is exercised by
scripts/human_substrate_smoke.py inside a real Blender.
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO / "extension" / "alepou_blender_bridge" / "human_data.py"
SPEC = importlib.util.spec_from_file_location("alepou_blender_bridge_human_data", MODULE_PATH)
human_data = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = human_data
SPEC.loader.exec_module(human_data)

HumanDataError = human_data.HumanDataError

PACK = Path("D:/alepou.ai/terminal-manager/agent/vendor/blender/resources/human_v0")


class TargetParsing(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_axis_mapping_swaps_depth_and_up_and_scales_to_metres(self):
        # MakeHuman is y-up in decimetres; Blender is z-up facing -Y, in metres.
        got = human_data.target_offset_to_blender(1.0, 2.0, 3.0)
        for value, want in zip(got, (0.1, -0.3, 0.2)):
            self.assertAlmostEqual(value, want)

    def test_reads_sparse_displacements(self):
        path = self.write("nose-test.target", "# comment\n\n161 0 -.011 0\n297 1 2 3\n")
        offsets = human_data.parse_target(path)
        self.assertEqual(offsets[0][0], 161)
        self.assertAlmostEqual(offsets[0][1][2], -0.0011)
        for got, want in zip(offsets[1][1], (0.1, -0.3, 0.2)):
            self.assertAlmostEqual(got, want)

    def test_rejects_vertex_outside_canonical_mesh(self):
        path = self.write("bad.target", "999999 1 1 1\n")
        with self.assertRaisesRegex(HumanDataError, "outside the canonical mesh"):
            human_data.parse_target(path)

    def test_rejects_short_rows(self):
        path = self.write("short.target", "161 0 1\n")
        with self.assertRaisesRegex(HumanDataError, "expected"):
            human_data.parse_target(path)

    def test_rejects_an_empty_target(self):
        path = self.write("empty.target", "# only a comment\n")
        with self.assertRaisesRegex(HumanDataError, "no displacements"):
            human_data.parse_target(path)

    def test_morph_name_disambiguates_ethnicity_variants(self):
        self.assertEqual(
            human_data.morph_name(Path("targets/nose/nose-hump-incr.target")), "nose-hump-incr"
        )
        self.assertEqual(
            human_data.morph_name(Path("units/asian/mouth-open.target")), "asian_mouth-open"
        )


class SkeletonData(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)

    def write_json(self, name, payload):
        path = self.root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_bone_order_places_parents_before_children(self):
        bones = {
            "hand": {"parent": "forearm"},
            "forearm": {"parent": "upperarm"},
            "upperarm": {"parent": None},
        }
        order = human_data.bone_order(bones)
        self.assertLess(order.index("upperarm"), order.index("forearm"))
        self.assertLess(order.index("forearm"), order.index("hand"))

    def test_bone_order_reports_a_cycle_rather_than_dropping_bones(self):
        with self.assertRaisesRegex(HumanDataError, "cyclic"):
            human_data.bone_order({"a": {"parent": "b"}, "b": {"parent": "a"}})

    def test_bone_order_reports_a_missing_parent(self):
        with self.assertRaisesRegex(HumanDataError, "missing parent"):
            human_data.bone_order({"hand": {"parent": "ghost"}})

    def test_centroid_averages_joint_cube_vertices(self):
        coords = [(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 2.0, 0.0), (2.0, 2.0, 0.0)]
        for got, want in zip(human_data.centroid(coords, [0, 1, 2, 3]), (1.0, 1.0, 0.0)):
            self.assertAlmostEqual(got, want)

    def test_load_skeleton_rejects_joints_outside_the_canonical_mesh(self):
        path = self.write_json(
            "bad.mhskel",
            {"bones": {"a": {"head": "h", "tail": "t"}}, "joints": {"h": [999999]}},
        )
        with self.assertRaisesRegex(HumanDataError, "outside the canonical mesh"):
            human_data.load_skeleton(path)

    def test_load_weights_rejects_malformed_entries(self):
        path = self.write_json("bad.mhw", {"weights": {"bone": [[1]]}})
        with self.assertRaisesRegex(HumanDataError, "malformed weight entry"):
            human_data.load_weights(path)


class TopologyContract(unittest.TestCase):
    def test_signature_ignores_positions_but_not_connectivity(self):
        base = human_data.topology_signature(4, [[0, 1, 2], [1, 2, 3]])
        self.assertEqual(base, human_data.topology_signature(4, [[0, 1, 2], [1, 2, 3]]))
        self.assertNotEqual(base, human_data.topology_signature(5, [[0, 1, 2], [1, 2, 3]]))
        self.assertNotEqual(base, human_data.topology_signature(4, [[0, 1, 3], [1, 2, 3]]))

    def test_check_vertex_count_explains_what_broke(self):
        human_data.check_vertex_count(human_data.EXPECTED_VERTEX_COUNT)
        with self.assertRaisesRegex(HumanDataError, "Topology drift"):
            human_data.check_vertex_count(human_data.EXPECTED_VERTEX_COUNT * 4)

    def test_check_signature_rejects_rewired_faces(self):
        human_data.check_signature("abc", "abc")
        with self.assertRaisesRegex(HumanDataError, "face connectivity changed"):
            human_data.check_signature("abc", "def")


@unittest.skipUnless(PACK.is_dir(), "vendored pack not present")
class VendoredPack(unittest.TestCase):
    """Verify against the real vendored data, not fixtures."""

    def test_every_vendored_target_parses(self):
        failures = []
        count = 0
        for path in human_data.iter_targets(PACK / "targets"):
            count += 1
            try:
                human_data.parse_target(path)
            except HumanDataError as error:
                failures.append(str(error))
        self.assertEqual(count, 384)
        self.assertEqual(failures, [])

    def test_vendored_morph_names_are_unique(self):
        names = [human_data.morph_name(p) for p in human_data.iter_targets(PACK / "targets")]
        self.assertEqual(len(names), len(set(names)))

    def test_vendored_skeleton_and_weights_load_and_order(self):
        skeleton = human_data.load_skeleton(PACK / "rigs" / "default.mhskel")
        weights = human_data.load_weights(PACK / "rigs" / "default_weights.mhw")
        self.assertEqual(len(skeleton["bones"]), 163)
        self.assertEqual(len(skeleton["joints"]), 326)

        order = human_data.bone_order(skeleton["bones"])
        self.assertEqual(len(order), 163)
        seen = set()
        for name in order:
            parent = skeleton["bones"][name].get("parent")
            if parent:
                self.assertIn(parent, seen, "{} came before its parent {}".format(name, parent))
            seen.add(name)
        self.assertTrue(set(weights) <= set(skeleton["bones"]))

    def test_vendored_face_rig_has_jaw_and_tongue(self):
        bones = set(human_data.load_skeleton(PACK / "rigs" / "default.mhskel")["bones"])
        self.assertIn("jaw", bones)
        self.assertTrue({"eye.L", "eye.R"} <= bones)
        self.assertGreaterEqual(len([b for b in bones if b.startswith("tongue")]), 7)

    def test_every_joint_resolves_within_the_canonical_mesh(self):
        skeleton = human_data.load_skeleton(PACK / "rigs" / "default.mhskel")
        joints = skeleton["joints"]
        missing = []
        for name, bone in skeleton["bones"].items():
            for end in ("head", "tail"):
                if bone.get(end) not in joints:
                    missing.append("{}.{}".format(name, end))
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
