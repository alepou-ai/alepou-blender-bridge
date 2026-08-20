"""Self-contained Spatial source loading and Blender-plan preparation."""

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass
from typing import Any

import spatial
from spatial_blender import CompiledScript, compile_script
from spatial_core import CompilePlan, ResolvedScene, Scene, build_compile_plan


RUNTIME_VERSION = "0.1.0"
SOURCE_FORMATS = {"python", "json", "yaml"}
COMPILE_MODES = {"build", "update", "dry_run"}


class BundledSpatialError(ValueError):
    """Raised when a bundled Spatial request is invalid or cannot resolve."""


@dataclass(frozen=True)
class PreparedSpatial:
    source: str
    source_format: str
    scene: Scene
    resolved: ResolvedScene
    plan: CompilePlan
    compiled: CompiledScript


def yaml_available() -> bool:
    return importlib.util.find_spec("yaml") is not None


def describe() -> dict[str, Any]:
    formats = ["python", "json"]
    if yaml_available():
        formats.append("yaml")
    return {
        "bundled": True,
        "runtimeVersion": RUNTIME_VERSION,
        "sourceFormats": formats,
        "compileModes": sorted(COMPILE_MODES),
        "actions": ["spatial.execute", "spatial.inspect", "spatial.why"],
        "externalPythonPackageRequired": False,
    }


def _scene_from_python(source: str) -> Scene:
    namespace: dict[str, Any] = {
        "__name__": "__alepou_spatial_source__",
        "__file__": "<spatial-source>",
        "spatial": spatial,
    }
    exec(compile(source, "<spatial-source>", "exec"), namespace, namespace)
    candidate = namespace.get("scene")
    if candidate is None:
        builder = namespace.get("build") or namespace.get("make_scene")
        if callable(builder):
            candidate = builder()
    if not isinstance(candidate, Scene):
        raise BundledSpatialError(
            "Spatial Python must assign a spatial.Scene to 'scene' or define build()/make_scene() returning one"
        )
    return candidate


def load_scene(source: Any, source_format: Any) -> Scene:
    kind = str(source_format or "python").strip().lower().replace("yml", "yaml")
    if kind not in SOURCE_FORMATS:
        raise BundledSpatialError(f"sourceFormat must be one of {sorted(SOURCE_FORMATS)}, got {kind!r}")
    if kind == "python":
        if not isinstance(source, str) or not source.strip():
            raise BundledSpatialError("Spatial Python source must be a non-empty string")
        return _scene_from_python(source)
    if kind == "json":
        if isinstance(source, str):
            try:
                value = json.loads(source)
            except json.JSONDecodeError as error:
                raise BundledSpatialError(f"Invalid Spatial JSON: {error}") from error
        elif isinstance(source, dict):
            value = source
        else:
            raise BundledSpatialError("Spatial JSON source must be an object or encoded object string")
        return Scene.from_dict(value)
    if not isinstance(source, str) or not source.strip():
        raise BundledSpatialError("Spatial YAML source must be a non-empty string")
    try:
        import yaml
    except ImportError as error:
        raise BundledSpatialError(
            "This package does not include a compatible YAML wheel; use Spatial Python or JSON"
        ) from error
    try:
        value = yaml.safe_load(source)
    except Exception as error:
        raise BundledSpatialError(f"Invalid Spatial YAML: {error}") from error
    return Scene.from_dict(value)


def prepare(action: dict[str, Any]) -> PreparedSpatial:
    source_format = str(action.get("sourceFormat") or "python").strip().lower().replace("yml", "yaml")
    raw_source = action.get("source")
    scene = load_scene(raw_source, source_format)
    compile_mode = str(action.get("compileMode") or "update").strip().lower()
    if compile_mode not in COMPILE_MODES:
        raise BundledSpatialError(f"compileMode must be one of {sorted(COMPILE_MODES)}, got {compile_mode!r}")
    resolved = scene.resolve()
    plan = build_compile_plan(resolved, mode=compile_mode, force=bool(action.get("force", False)))
    return PreparedSpatial(
        source=(json.dumps(raw_source, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        if source_format == "json" and isinstance(raw_source, dict)
        else str(raw_source or ""),
        source_format=source_format,
        scene=scene,
        resolved=resolved,
        plan=plan,
        compiled=compile_script(plan),
    )


__all__ = [
    "BundledSpatialError",
    "PreparedSpatial",
    "RUNTIME_VERSION",
    "describe",
    "load_scene",
    "prepare",
    "yaml_available",
]
