import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from alepou_blender import cli


class CliTests(unittest.TestCase):
    def test_submit_no_wait_writes_atomic_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "plan" / "blender"
            request = {"schemaVersion": 1, "commandId": "query-1", "actions": [{"action": "scene.summary"}]}
            result = cli.submit(root, request, query=True, timeout=0.1, wait=False)
            self.assertEqual(result["status"], "pending")
            written = json.loads((root / "queries" / "pending" / "query-1.json").read_text(encoding="utf-8"))
            self.assertEqual(written, request)

    def test_status_reads_health(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            root = project / "plan" / "blender"
            root.mkdir(parents=True)
            (root / "bridge-health.json").write_text('{"processorActive": true}', encoding="utf-8")
            args = argparse.Namespace(project=str(project), command="status")
            self.assertTrue(cli.run(args)["processorActive"])

    def test_duplicate_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pending = root / "commands" / "pending"
            pending.mkdir(parents=True)
            (pending / "cmd-1.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(cli.ClientError):
                cli.submit(root, {"commandId": "cmd-1"}, query=False, timeout=0.1, wait=False)


if __name__ == "__main__":
    unittest.main()
