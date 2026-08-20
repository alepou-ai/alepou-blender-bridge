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

    def test_spatial_mode_defaults_off_and_can_be_set(self):
        with tempfile.TemporaryDirectory() as directory:
            common = {"project": directory, "timeout": 0.1, "no_wait": True, "representation_mode": None}
            read = cli.run(argparse.Namespace(command="spatial-mode", value=None, **common))
            self.assertEqual(read["mode"], "off")
            written = cli.run(argparse.Namespace(command="spatial-mode", value="opt_in", **common))
            self.assertEqual(written["mode"], "opt_in")
            path = Path(directory) / "plan" / "blender" / "spatial-mode.json"
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["mode"], "opt_in")

    def test_spatial_dry_run_is_gated_and_does_not_submit(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            source = project / "scene.json"
            source.write_text(
                json.dumps(
                    {
                        "spatial": "0.1",
                        "scene": {"id": "cli_demo", "units": "m", "up": "Z"},
                        "objects": {"box": {"type": "box", "size": [1, 2, 3], "center": [0, 0, 1.5]}},
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                project=str(project),
                command="spatial",
                path=str(source),
                compile_mode="dry_run",
                force=False,
                session=None,
                id=None,
                timeout=0.1,
                no_wait=True,
                representation_mode=None,
            )
            with self.assertRaises(cli.ClientError):
                cli.run(args)
            cli.run(
                argparse.Namespace(
                    project=str(project),
                    command="spatial-mode",
                    value="opt_in",
                    timeout=0.1,
                    no_wait=True,
                    representation_mode=None,
                )
            )
            result = cli.run(args)
            self.assertEqual(result["status"], "dry-run")
            self.assertFalse(result["plan"]["fallbackAllowed"])
            self.assertFalse((project / "plan" / "blender" / "commands" / "pending").exists())
            inspected = cli.run(
                argparse.Namespace(
                    project=str(project),
                    command="spatial-inspect",
                    path=str(source),
                    entity="box",
                    timeout=0.1,
                    no_wait=True,
                    representation_mode=None,
                )
            )
            self.assertEqual(inspected["value"]["id"], "box")

    def test_required_mode_rejects_raw_bpy_script(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            source = project / "raw.py"
            source.write_text("import bpy\n", encoding="utf-8")
            cli.run(
                argparse.Namespace(
                    project=str(project),
                    command="spatial-mode",
                    value="required",
                    timeout=0.1,
                    no_wait=True,
                    representation_mode=None,
                )
            )
            with self.assertRaises(cli.ClientError):
                cli.run(
                    argparse.Namespace(
                        project=str(project),
                        command="script",
                        path=str(source),
                        session=None,
                        id=None,
                        timeout=0.1,
                        no_wait=True,
                        representation_mode=None,
                    )
                )


if __name__ == "__main__":
    unittest.main()
