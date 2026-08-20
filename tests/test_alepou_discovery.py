import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO / "extension" / "alepou_blender_bridge" / "alepou_discovery.py"
SPEC = importlib.util.spec_from_file_location("alepou_blender_bridge_discovery", MODULE_PATH)
discovery = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(discovery)


class FakeResponse:
    def __init__(self, value):
        self.body = json.dumps(value).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit):
        return self.body[:limit]


class AlepouDiscoveryTests(unittest.TestCase):
    def tearDown(self):
        discovery.clear_cache()

    def _home(self, record=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        home = Path(temporary.name)
        target = discovery.discovery_file(home)
        target.parent.mkdir(parents=True)
        target.write_text(
            json.dumps(
                record
                or {
                    "schemaVersion": 1,
                    "baseUrl": "http://127.0.0.1:8787",
                    "integrationToken": "a" * 64,
                }
            ),
            encoding="utf-8",
        )
        return home

    def test_discovery_accepts_only_loopback_agent_urls(self):
        good = self._home()
        self.assertEqual(discovery.read_agent_discovery(good)["baseUrl"], "http://127.0.0.1:8787")
        bad = self._home(
            {
                "baseUrl": "http://192.168.1.20:8787",
                "integrationToken": "b" * 64,
            }
        )
        with self.assertRaisesRegex(discovery.AlepouDiscoveryError, "non-loopback"):
            discovery.read_agent_discovery(bad)

    def test_project_catalogue_is_authenticated_bounded_and_keeps_duplicate_names_distinct(self):
        home = self._home()
        seen = {}

        def opener(request, timeout):
            seen["url"] = request.full_url
            seen["token"] = request.get_header("X-alepou-integration-token")
            seen["timeout"] = timeout
            return FakeResponse(
                {
                    "ok": True,
                    "projects": [
                        {
                            "projectId": "first",
                            "name": "Shared",
                            "path": "D:/Projects/First",
                            "runningSessionCount": 1,
                            "connectedInstanceCount": 0,
                            "instances": [],
                        },
                        {
                            "projectId": "second",
                            "name": "Shared",
                            "path": "D:/Projects/Second",
                            "runningSessionCount": "invalid",
                            "connectedInstanceCount": 1,
                            "instances": [
                                {
                                    "instanceId": "blender-second",
                                    "connected": True,
                                    "processorState": "healthy",
                                }
                            ],
                        },
                    ],
                }
            )

        projects = discovery.refresh_projects(home=home, opener=opener)
        self.assertEqual(seen["url"], "http://127.0.0.1:8787/api/integrations/blender/projects")
        self.assertEqual(seen["token"], "a" * 64)
        self.assertLessEqual(seen["timeout"], 2.0)
        self.assertEqual([project["projectId"] for project in projects], ["first", "second"])
        self.assertNotEqual(projects[0]["path"], projects[1]["path"])
        self.assertEqual(projects[1]["instances"][0]["processorState"], "healthy")
        self.assertIs(discovery.project_by_id("second"), projects[1])
        self.assertEqual(len(discovery.enum_items()), 2)

    def test_missing_or_oversized_discovery_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(discovery.AlepouDiscoveryError):
                discovery.read_agent_discovery(Path(directory))
        home = self._home()
        discovery.discovery_file(home).write_bytes(b"{" + b"x" * discovery.DISCOVERY_MAX_BYTES + b"}")
        with self.assertRaisesRegex(discovery.AlepouDiscoveryError, "exceeds"):
            discovery.read_agent_discovery(home)


if __name__ == "__main__":
    unittest.main()
