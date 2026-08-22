"""Deterministic diagnostic and pull-only viewport captures."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import bpy
from mathutils import Vector

from . import state

CAMERA_COLLECTION = "Alepou Diagnostics"
CAMERA_NAME = "Alepou Diagnostic Camera"

VIEW_DIRECTIONS = {
    "front": Vector((0.0, -1.0, 0.0)),
    "rear": Vector((0.0, 1.0, 0.0)),
    "left": Vector((-1.0, 0.0, 0.0)),
    "right": Vector((1.0, 0.0, 0.0)),
    "top": Vector((0.0, 0.0, 1.0)),
    "bottom": Vector((0.0, 0.0, -1.0)),
    "north-east": Vector((1.0, -1.0, 0.75)).normalized(),
    "north-west": Vector((-1.0, -1.0, 0.75)).normalized(),
}

PERSPECTIVE_VIEWS = {"north-east", "north-west"}
DIAGNOSTIC_MODES = {"beauty", "clay", "silhouette", "studio", "wireframe"}
DIAGNOSTIC_PROJECTIONS = {"auto", "orthographic", "perspective"}


def ensure_standard_camera(scene: Any | None = None) -> Any:
    scene = scene or bpy.context.scene
    collection = bpy.data.collections.get(CAMERA_COLLECTION)
    if collection is None:
        collection = bpy.data.collections.new(CAMERA_COLLECTION)
        scene.collection.children.link(collection)
    camera = bpy.data.objects.get(CAMERA_NAME)
    if camera is None or camera.type != "CAMERA":
        data = bpy.data.cameras.new(CAMERA_NAME)
        camera = bpy.data.objects.new(CAMERA_NAME, data)
        collection.objects.link(camera)
    elif camera.name not in collection.objects:
        collection.objects.link(camera)
    camera["alepou_diagnostic"] = True
    camera.data.type = "ORTHO"
    return camera


def _target_bounds(action: dict[str, Any]) -> dict[str, Any]:
    target = action.get("target") or {}
    if isinstance(target, str):
        target = {"object": target}
    if target.get("object"):
        return state.union_bounds([state.find_object(target["object"])])
    names = target.get("objects")
    if names:
        return state.union_bounds([state.find_object(name) for name in names[:100]])
    explicit = target.get("bounds")
    if explicit and explicit.get("min") and explicit.get("max"):
        minimum = Vector(explicit["min"])
        maximum = Vector(explicit["max"])
        return {
            "min": state.vector(minimum),
            "max": state.vector(maximum),
            "center": state.vector((minimum + maximum) * 0.5),
            "size": state.vector(maximum - minimum),
        }
    return state.union_bounds()


def _capture_direction(action: dict[str, Any]) -> tuple[str, Vector]:
    custom = action.get("direction")
    if custom is not None:
        if not isinstance(custom, (list, tuple)) or len(custom) != 3:
            raise ValueError("Diagnostic direction must contain exactly three numeric components")
        try:
            direction = Vector(float(component) for component in custom)
        except (TypeError, ValueError) as error:
            raise ValueError("Diagnostic direction must contain exactly three numeric components") from error
        if not all(math.isfinite(component) for component in direction) or direction.length == 0.0:
            raise ValueError("Diagnostic direction must be finite and non-zero")
        return "custom", direction.normalized()

    view = str(action.get("view") or "north-east").lower()
    if view not in VIEW_DIRECTIONS:
        raise ValueError(f"Unsupported diagnostic view: {view}")
    return view, VIEW_DIRECTIONS[view]


def _projected_target_size(size: Vector, rotation: Any) -> tuple[float, float, float]:
    half_size = size * 0.5
    matrix = rotation.to_matrix()
    right = matrix @ Vector((1.0, 0.0, 0.0))
    up = matrix @ Vector((0.0, 1.0, 0.0))
    backward = matrix @ Vector((0.0, 0.0, 1.0))

    def span(axis: Vector) -> float:
        return 2.0 * sum(abs(axis[index]) * half_size[index] for index in range(3))

    return span(right), span(up), span(backward)


def diagnostic_capture(action: dict[str, Any], captures_root: Path, command_id: str) -> dict[str, Any]:
    scene = bpy.context.scene
    view, direction = _capture_direction(action)
    mode = str(action.get("mode") or "clay").lower()
    if mode not in DIAGNOSTIC_MODES:
        raise ValueError(f"Unsupported diagnostic mode: {mode}")
    resolution = max(64, min(int(action.get("resolution") or 512), 2048))
    margin = float(action.get("margin") or 1.15)
    if not math.isfinite(margin) or margin < 1.0:
        raise ValueError("Diagnostic margin must be a finite number greater than or equal to 1.0")
    projection = str(action.get("projection") or "auto").lower()
    if projection not in DIAGNOSTIC_PROJECTIONS:
        raise ValueError(f"Unsupported diagnostic projection: {projection}")
    bounds = _target_bounds(action)
    center = Vector(bounds["center"])
    size = Vector(bounds["size"])
    if not all(math.isfinite(component) and component >= 0.0 for component in size):
        raise ValueError("Diagnostic target has invalid bounds")
    radius = float(size.length) * 0.5
    if radius <= 0.0:
        raise ValueError("Diagnostic target has no measurable extent")

    camera = ensure_standard_camera(scene)
    camera.rotation_euler = (-direction).to_track_quat("-Z", "Y").to_euler()
    projected_width, projected_height, projected_depth = _projected_target_size(size, camera.rotation_euler)
    if max(projected_width, projected_height) <= 0.0:
        raise ValueError("Diagnostic target has no visible extent from the requested direction")

    lens = float(action.get("lens") or 50.0)
    if not math.isfinite(lens) or lens <= 0.0:
        raise ValueError("Diagnostic lens must be a finite positive number")
    camera.data.lens = lens
    use_perspective = projection == "perspective" or (projection == "auto" and view in PERSPECTIVE_VIEWS)
    camera.data.type = "PERSP" if use_perspective else "ORTHO"
    if use_perspective:
        half_angle = min(camera.data.angle_x, camera.data.angle_y) * 0.5
        distance = radius * margin / math.sin(half_angle)
    else:
        camera.data.ortho_scale = max(projected_width, projected_height) * margin
        distance = radius * 3.0
    camera.location = center + direction * distance
    camera.data.clip_start = max(radius * 0.001, 1e-6)
    camera.data.clip_end = max(distance + radius * 4.0, camera.data.clip_start * 2.0)

    suffix = str(action.get("filename") or f"{command_id}-{view}-{mode}.png")
    if not suffix.lower().endswith(".png"):
        suffix += ".png"
    suffix = Path(suffix).name
    destination = captures_root / suffix
    destination.parent.mkdir(parents=True, exist_ok=True)

    backup = {
        "camera": scene.camera,
        "engine": scene.render.engine,
        "filepath": scene.render.filepath,
        "resolution_x": scene.render.resolution_x,
        "resolution_y": scene.render.resolution_y,
        "resolution_percentage": scene.render.resolution_percentage,
        "film_transparent": scene.render.film_transparent,
        "file_format": scene.render.image_settings.file_format,
    }
    shading = scene.display.shading
    shading_backup = {
        name: getattr(shading, name)
        for name in ("light", "color_type", "single_color", "show_shadows", "show_cavity", "show_outline", "show_specular_highlight")
        if hasattr(shading, name)
    }
    try:
        scene.camera = camera
        scene.render.filepath = str(destination)
        scene.render.resolution_x = resolution
        scene.render.resolution_y = resolution
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = "PNG"
        if mode != "beauty":
            scene.render.engine = "BLENDER_WORKBENCH"
            shading.light = "FLAT" if mode == "silhouette" else "STUDIO"
            shading.color_type = "MATERIAL" if mode == "studio" else "SINGLE"
            shading.single_color = (0.8, 0.8, 0.8) if mode == "clay" else (0.02, 0.02, 0.02)
            shading.show_shadows = mode in {"clay", "studio"}
            shading.show_cavity = mode in {"clay", "studio", "wireframe"}
            if hasattr(shading, "show_outline"):
                shading.show_outline = mode == "wireframe"
            if hasattr(shading, "show_specular_highlight"):
                shading.show_specular_highlight = mode in {"clay", "studio"}
            scene.render.film_transparent = mode == "silhouette"
        bpy.ops.render.render(write_still=True)
    finally:
        scene.camera = backup["camera"]
        scene.render.engine = backup["engine"]
        scene.render.filepath = backup["filepath"]
        scene.render.resolution_x = backup["resolution_x"]
        scene.render.resolution_y = backup["resolution_y"]
        scene.render.resolution_percentage = backup["resolution_percentage"]
        scene.render.film_transparent = backup["film_transparent"]
        scene.render.image_settings.file_format = backup["file_format"]
        for name, value in shading_backup.items():
            setattr(shading, name, value)
    return {
        "kind": "diagnostic",
        "path": str(destination.resolve()),
        "filename": destination.name,
        "view": view,
        "mode": mode,
        "resolution": [resolution, resolution],
        "margin": margin,
        "targetBounds": bounds,
        "camera": camera.name_full,
        "cameraType": camera.data.type,
        "cameraDirection": state.vector(direction),
        "cameraDistance": distance,
        "cameraLens": camera.data.lens,
        "projectedTargetSize": [projected_width, projected_height, projected_depth],
        "orthoScale": camera.data.ortho_scale if camera.data.type == "ORTHO" else None,
    }


def viewport_capture(action: dict[str, Any], captures_root: Path, command_id: str) -> dict[str, Any]:
    if bpy.app.background:
        raise RuntimeError("Current viewport capture is unavailable in background mode")
    window = bpy.context.window
    screen = window.screen if window else None
    area = next((candidate for candidate in screen.areas if candidate.type == "VIEW_3D"), None) if screen else None
    if area is None:
        raise RuntimeError("No VIEW_3D area is available for viewport capture")
    destination = captures_root / Path(str(action.get("filename") or f"{command_id}-viewport.png")).name
    destination.parent.mkdir(parents=True, exist_ok=True)
    region = next((candidate for candidate in area.regions if candidate.type == "WINDOW"), None)
    with bpy.context.temp_override(window=window, screen=screen, area=area, region=region):
        bpy.ops.screen.screenshot(filepath=str(destination), full=False)
    return {"kind": "viewport", "path": str(destination.resolve()), "filename": destination.name}
