"""Build the Blender 4.2+ extension zip and Blender 4.1 legacy add-on zip."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "extension" / "alepou_blender_bridge"
VERSION = "0.3.1"


def build_runtime_wheel(destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--wheel-dir",
            str(destination),
            str(REPO),
        ],
        check=True,
    )
    wheels = sorted(destination.glob("alepou_blender_bridge-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"Expected one Alepou runtime wheel, found {len(wheels)} in {destination}")
    return wheels[0]


def declare_runtime_wheel(staged: Path, wheel: Path) -> None:
    manifest = staged / "blender_manifest.toml"
    relative = wheel.relative_to(staged).as_posix()
    source = manifest.read_text(encoding="utf-8")
    marker = "\n[permissions]\n"
    if marker not in source:
        raise RuntimeError(f"Manifest has no permissions table marker: {manifest}")
    manifest.write_text(
        source.replace(marker, f'\nwheels = ["./{relative}"]\n{marker}', 1),
        encoding="utf-8",
        newline="\n",
    )


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
        wheel = build_runtime_wheel(staged / "wheels")
        declare_runtime_wheel(staged, wheel)
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
                if source.is_file() and source.name != "blender_manifest.toml" and "wheels" not in source.relative_to(staged).parts:
                    archive.write(source, Path("alepou_blender_bridge") / source.relative_to(staged))

    for package in (extension_zip, legacy_zip):
        if not package.is_file() or package.stat().st_size == 0:
            raise RuntimeError(f"Package build did not produce {package}")
        print(f"built {package} ({package.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
