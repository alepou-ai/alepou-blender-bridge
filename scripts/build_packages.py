"""Build the Blender 4.2+ extension zip and Blender 4.1 legacy add-on zip."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "extension" / "alepou_blender_bridge"
VERSION = "0.1.0"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender", required=True, help="Blender 4.2+ executable used for extension validation/build")
    parser.add_argument("--output", default=str(REPO / "dist"))
    arguments = parser.parse_args()
    blender = Path(arguments.blender).expanduser().resolve()
    output = Path(arguments.output).expanduser().resolve()
    if not blender.is_file():
        parser.error(f"Blender executable does not exist: {blender}")
    output.mkdir(parents=True, exist_ok=True)
    extension_zip = output / f"alepou_blender_bridge-{VERSION}.zip"
    legacy_zip = output / f"alepou_blender_bridge-{VERSION}-legacy.zip"

    with tempfile.TemporaryDirectory(prefix="alepou-blender-package-") as directory:
        staged = Path(directory) / "alepou_blender_bridge"
        shutil.copytree(SOURCE, staged, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copy2(REPO / "LICENSE", staged / "LICENSE")
        subprocess.run(
            [
                str(blender),
                "--background",
                "--factory-startup",
                "--command",
                "extension",
                "build",
                "--source-dir",
                str(staged),
                "--output-filepath",
                str(extension_zip),
                "--verbose",
            ],
            check=True,
        )
        with zipfile.ZipFile(legacy_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for source in sorted(staged.rglob("*")):
                if source.is_file() and source.name != "blender_manifest.toml":
                    archive.write(source, Path("alepou_blender_bridge") / source.relative_to(staged))

    for package in (extension_zip, legacy_zip):
        if not package.is_file() or package.stat().st_size == 0:
            raise RuntimeError(f"Package build did not produce {package}")
        print(f"built {package} ({package.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
