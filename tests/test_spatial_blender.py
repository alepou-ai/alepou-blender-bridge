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
RUNTIME_PATH = REPO / "extension" / "alepou_blender_bridge" / "spatial_runtime.py"
RUNTIME_SPEC = importlib.util.spec_from_file_location("alepou_blender_spatial_runtime", RUNTIME_PATH)
runtime = importlib.util.module_from_spec(RUNTIME_SPEC)
assert RUNTIME_SPEC and RUNTIME_SPEC.loader
sys.modules[RUNTIME_SPEC.name] = runtime
RUNTIME_SPEC.loader.exec_module(runtime)
STATE_PATH = REPO / "extension" / "alepou_blender_bridge" / "state.py"


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

    def test_generated_update_orders_hierarchy_and_preserves_material_slots(self):
        scene = spatial.Scene("hierarchy_regression")
        frame = scene.frame("moving_frame", origin=(2, 0, 0))
        child = scene.box("a_child", size=(3, 1, 1), frame=frame, center=(1.5, 0, 0))
        scene.assembly("z_parent", frame=frame, children=(child,))
        source = compile_script(build_compile_plan(scene.resolve(), mode="update")).source
        ast.parse(source)
        self.assertIn("ORDERED_ENTITY_IDS = _hierarchy_order()", source)
        self.assertLess(
            source.index("# Existing children may sort before a parent"),
            source.index("for entity_id in ORDERED_ENTITY_IDS:\n    desired = DESIRED[entity_id]"),
        )
        self.assertIn("external_materials = list(old.materials)", source)
        self.assertIn("mesh.materials.append(material)", source)

    def test_state_export_prefers_canonical_spatial_identity_with_legacy_fallback(self):
        source = STATE_PATH.read_text(encoding="utf-8")
        self.assertIn('obj.get("spatial.entity_id") or obj.get("spatial_id")', source)

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

    def test_direct_spatial_actions_are_policy_gated(self):
        request = {
            "representation": {"kind": "spatial", "fallbackAllowed": False},
            "actions": [{"action": "spatial.execute", "sourceFormat": "python", "source": "scene = None"}],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(policy.SpatialPolicyError):
                policy.enforce(root, request, ["spatial.execute"])
            (root / "spatial-mode.json").write_text('{"mode":"opt_in"}', encoding="utf-8")
            self.assertEqual(policy.enforce(root, request, ["spatial.execute"]), "spatial")
            with self.assertRaises(policy.SpatialPolicyError):
                policy.enforce(root, request, ["spatial.execute", "script.execute"])

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

    def test_bundled_runtime_prepares_python_and_json_without_external_cli(self):
        python_source = "\n".join(
            (
                "import spatial",
                "scene = spatial.Scene('lamp_skeleton', units='mm')",
                "scene.cylinder('base', radius=120, length=20, axis='Z', center=(0, 0, 10))",
                "scene.cylinder('arm', radius=12, length=400, axis='Z', center=(0, 0, 220))",
            )
        )
        prepared = runtime.prepare(
            {"sourceFormat": "python", "source": python_source, "compileMode": "update"}
        )
        self.assertEqual(prepared.scene.id, "lamp_skeleton")
        self.assertEqual(prepared.plan.mode, "update")
        self.assertIn("SPATIAL_RESULT_JSON", prepared.compiled.source)

        encoded = prepared.scene.to_json()
        repeated = runtime.prepare(
            {"sourceFormat": "json", "source": encoded, "compileMode": "dry_run"}
        )
        self.assertEqual(prepared.scene.canonical_json(), repeated.scene.canonical_json())
        self.assertEqual(repeated.plan.mode, "dry_run")
        self.assertTrue(runtime.describe()["bundled"])
        self.assertFalse(runtime.describe()["externalPythonPackageRequired"])

    def test_bundled_python_requires_a_scene_result(self):
        with self.assertRaises(runtime.BundledSpatialError):
            runtime.prepare({"sourceFormat": "python", "source": "answer = 42"})


if __name__ == "__main__":
    unittest.main()
