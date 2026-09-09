import re
import sys
import tomllib
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
EXTENSION = REPO / "extension" / "alepou_blender_bridge"
sys.path.insert(0, str(REPO / "src"))

from alepou_blender import __version__ as cli_version


class VersionConsistencyTests(unittest.TestCase):
    def test_release_surfaces_match_extension_manifest(self):
        manifest_version = tomllib.loads(
            (EXTENSION / "blender_manifest.toml").read_text(encoding="utf-8")
        )["version"]
        package_version = tomllib.loads(
            (REPO / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]["version"]
        legacy_source = (EXTENSION / "__init__.py").read_text(encoding="utf-8")
        legacy_match = re.search(r'"version"\s*:\s*\((\d+),\s*(\d+),\s*(\d+)\)', legacy_source)

        self.assertIsNotNone(legacy_match)
        legacy_version = ".".join(legacy_match.groups())
        self.assertEqual(package_version, manifest_version)
        self.assertEqual(cli_version, manifest_version)
        self.assertEqual(legacy_version, manifest_version)


if __name__ == "__main__":
    unittest.main()
