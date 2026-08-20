import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = REPO / "extension" / "alepou_blender_bridge" / "protocol.py"
SPEC = importlib.util.spec_from_file_location("alepou_blender_bridge_protocol", PROTOCOL_PATH)
protocol = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(protocol)


class ProtocolTests(unittest.TestCase):
    def test_layout_and_atomic_json(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            root = protocol.bridge_root(project)
            protocol.ensure_layout(root)
            self.assertTrue((root / "commands" / "processing").is_dir())
            protocol.atomic_write_json(root / "bridge-health.json", {"ok": True})
            self.assertEqual(json.loads((root / "bridge-health.json").read_text(encoding="utf-8")), {"ok": True})

    def test_safe_child_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(protocol.ProtocolError):
                protocol.safe_child(root, "../escape")

    def test_request_ids_are_bounded(self):
        self.assertEqual(protocol.validate_request_id("cmd-1.ok"), "cmd-1.ok")
        for invalid in ("", "../bad", "has space", "a" * 129):
            with self.assertRaises(protocol.ProtocolError):
                protocol.validate_request_id(invalid)

    def test_claim_is_atomic_and_single_use(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "pending.json"
            destination = root / "processing.json"
            source.write_text("{}", encoding="utf-8")
            self.assertTrue(protocol.claim_file(source, destination))
            self.assertFalse(protocol.claim_file(source, root / "other.json"))
            self.assertTrue(destination.is_file())


if __name__ == "__main__":
    unittest.main()
