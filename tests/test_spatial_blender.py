import ast
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import spatial
from spatial_blender import compile_script
from spatial_core import build_compile_plan

POLICY_PATH = REPO / "extension" / "alepou_blender_bridge" / "spatial_policy.py"
SPEC = importlib.util.spec_from_file_location("alepou_blender_spatial_policy", POLICY_PATH)
policy = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(policy)


class SpatialBlenderTests(unittest.TestCase):
    def test_compiler_is_deterministic_and_request_is_truthful(self):
        scene = spatial.Scene("demo", units="mm")
        scene.box("housing", size=(400, 300, 200), center=(0, 0, 100), bevel=spatial.Bevel(10, 3))
        compiled = compile_script(build_compile_plan(scene.resolve(), mode="update"))
        repeated = compile_script(build_compile_plan(scene.resolve(), mode="update"))
        self.assertEqual(compiled.source, repeated.source)
        self.assertEqual(compiled.source_hash, repeated.source_hash)
        ast.parse(compiled.source)
        request = compiled.request("spatial-demo", session_id="session-1")
        self.assertEqual(request["representation"]["kind"], "spatial")
        self.assertFalse(request["representation"]["fallbackAllowed"])
        self.assertEqual(request["actions"][0]["action"], "script.execute")
        self.assertIn("spatial.managed", request["actions"][0]["source"])
        self.assertIn("EXTERNAL_MODIFICATION_CONFLICT", request["actions"][0]["source"])

    def test_project_mode_enforces_off_opt_in_and_required(self):
        raw = {"actions": [{"action": "script.execute", "source": "import bpy"}]}
        spatial_request = {
            "representation": {"kind": "spatial", "fallbackAllowed": False},
            "actions": [{"action": "script.execute", "source": "import bpy"}],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(policy.SpatialPolicyError):
                policy.enforce(root, spatial_request, ["script.execute"])
            self.assertEqual(policy.enforce(root, raw, ["script.execute"]), "raw_bpy")
            (root / "spatial-mode.json").write_text('{"mode":"opt_in"}', encoding="utf-8")
            self.assertEqual(policy.enforce(root, spatial_request, ["script.execute"]), "spatial")
            self.assertEqual(policy.enforce(root, raw, ["script.execute"]), "raw_bpy")
            (root / "spatial-mode.json").write_text('{"mode":"required"}', encoding="utf-8")
            self.assertEqual(policy.enforce(root, spatial_request, ["script.execute"]), "spatial")
            with self.assertRaises(policy.SpatialPolicyError):
                policy.enforce(root, raw, ["script.execute"])
            self.assertEqual(policy.enforce(root, {"actions": [{"action": "scene.summary"}]}, ["scene.summary"]), "support")

    def test_spatial_request_requires_explicit_no_fallback(self):
        request = {
            "representation": {"kind": "spatial"},
            "actions": [{"action": "script.execute", "source": "import bpy"}],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "spatial-mode.json").write_text(json.dumps({"mode": "opt_in"}), encoding="utf-8")
            with self.assertRaises(policy.SpatialPolicyError):
                policy.enforce(root, request, ["script.execute"])


if __name__ == "__main__":
    unittest.main()
