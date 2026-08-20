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


def diagnostic_capture(action: dict[str, Any], captures_root: Path, command_id: str) -> dict[str, Any]:
    scene = bpy.context.scene
    view = str(action.get("view") or "north-east").lower()
    mode = str(action.get("mode") or "clay").lower()
    if view not in VIEW_DIRECTIONS:
        raise ValueError(f"Unsupported diagnostic view: {view}")
    if mode not in {"clay", "silhouette", "wireframe", "beauty"}:
        raise ValueError(f"Unsupported diagnostic mode: {mode}")
    resolution = max(64, min(int(action.get("resolution") or 512), 2048))
    margin = max(1.01, min(float(action.get("margin") or 1.2), 4.0))
    bounds = _target_bounds(action)
    center = Vector(bounds["center"])
    size = Vector(bounds["size"])
    radius = max(float(size.length) * 0.5, 0.5)
    direction = VIEW_DIRECTIONS[view]
    camera = ensure_standard_camera(scene)
    camera.location = center + direction * (radius * 3.0 + 1.0)
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera.data.type = "ORTHO" if view not in {"north-east", "north-west"} else "PERSP"
    camera.data.ortho_scale = max(size.x, size.y, size.z, 1.0) * margin
    camera.data.lens = 50.0

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
            shading.color_type = "SINGLE"
            shading.single_color = (0.8, 0.8, 0.8) if mode == "clay" else (0.02, 0.02, 0.02)
            shading.show_shadows = mode == "clay"
            shading.show_cavity = mode in {"clay", "wireframe"}
            if hasattr(shading, "show_outline"):
                shading.show_outline = mode == "wireframe"
            if hasattr(shading, "show_specular_highlight"):
                shading.show_specular_highlight = mode == "clay"
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
