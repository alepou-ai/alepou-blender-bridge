"""Compare a render against a reference photograph.

Deliberately not human-specific. The asset construction protocol's whole review
loop is "compare the render with the source evidence and enumerate every
material defect", and that applies to a reconstructed mouse as much as to a
face. This module gives that comparison a shared, exact footing.

The point is to stop estimating and start comparing. Judging that a nose is too
long is reliable; judging that its length ratio is 0.34 is not. An overlay turns
the second question into the first.

A caveat this module cannot fix: an overlay is only meaningful when the render
camera matches the camera that took the photograph. Until pose is solved, a
difference in projection reads as a difference in the subject.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import bpy
import numpy as np

BACKGROUND_NAME = "AlepouReference"
OVERLAY_MODES = ("over", "difference", "edges", "split")


class ReferenceError(ValueError):
    """Raised when a reference image or overlay request cannot be honoured."""


def load_image(path: str | Path) -> bpy.types.Image:
    """Load an image datablock, reusing one already loaded from the same file."""
    path = Path(path)
    if not path.is_file():
        raise ReferenceError("No reference image at {}".format(path))
    resolved = str(path.resolve())
    for image in bpy.data.images:
        if image.filepath and str(Path(bpy.path.abspath(image.filepath)).resolve()) == resolved:
            try:
                image.reload()
            except RuntimeError:
                pass
            return image
    return bpy.data.images.load(resolved)


def image_pixels(image: bpy.types.Image) -> np.ndarray:
    """RGBA float array shaped (height, width, 4), top row first."""
    width, height = image.size
    if width == 0 or height == 0:
        raise ReferenceError("Image {} has no pixel data".format(image.name))
    buffer = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(buffer)
    # Blender stores pixels bottom-up; flip so array row 0 is the top of the image.
    return buffer.reshape(height, width, 4)[::-1]


def write_image(pixels: np.ndarray, path: str | Path, *, name: str = "AlepouOverlay") -> Path:
    """Write an RGBA float array back out as a PNG."""
    path = Path(path)
    height, width = pixels.shape[0], pixels.shape[1]
    existing = bpy.data.images.get(name)
    if existing is not None:
        bpy.data.images.remove(existing)
    image = bpy.data.images.new(name, width=width, height=height, alpha=True)
    flipped = np.ascontiguousarray(pixels[::-1].astype(np.float32).ravel())
    image.pixels.foreach_set(flipped)
    image.filepath_raw = str(path)
    image.file_format = "PNG"
    image.save()
    return path


def match_resolution_to_reference(scene: Any, image_path: str | Path) -> tuple[int, int]:
    """Render at the reference's pixel dimensions so no resampling is needed.

    Comparing at the reference's own resolution keeps the photograph pristine and
    puts every resampling artefact on the render, where it belongs.
    """
    image = load_image(image_path)
    width, height = image.size
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = 100
    return (width, height)


def set_camera_background(
    camera: Any,
    image_path: str | Path,
    *,
    alpha: float = 0.5,
    in_front: bool = False,
) -> Any:
    """Show the reference behind (or in front of) the camera view.

    This is Blender's own background-image feature; it appears in the camera
    viewport, not in renders. Useful when a person is driving, while
    render_against_reference covers the headless case.
    """
    if getattr(camera, "type", None) != "CAMERA":
        raise ReferenceError("{!r} is not a camera".format(getattr(camera, "name", camera)))
    image = load_image(image_path)
    data = camera.data
    data.show_background_images = True
    for existing in list(data.background_images):
        if existing.image is image:
            data.background_images.remove(existing)
    background = data.background_images.new()
    background.image = image
    background.alpha = max(0.0, min(1.0, alpha))
    background.display_depth = "FRONT" if in_front else "BACK"
    background.frame_method = "FIT"
    return background


def _luminance(pixels: np.ndarray) -> np.ndarray:
    return pixels[..., 0] * 0.2126 + pixels[..., 1] * 0.7152 + pixels[..., 2] * 0.0722


def _edges(gray: np.ndarray) -> np.ndarray:
    """Sobel magnitude, normalised to 0..1."""
    padded = np.pad(gray, 1, mode="edge")
    gx = (
        padded[:-2, 2:] + 2 * padded[1:-1, 2:] + padded[2:, 2:]
        - padded[:-2, :-2] - 2 * padded[1:-1, :-2] - padded[2:, :-2]
    )
    gy = (
        padded[2:, :-2] + 2 * padded[2:, 1:-1] + padded[2:, 2:]
        - padded[:-2, :-2] - 2 * padded[:-2, 1:-1] - padded[:-2, 2:]
    )
    magnitude = np.sqrt(gx * gx + gy * gy)
    peak = float(magnitude.max())
    return magnitude / peak if peak > 1e-9 else magnitude


def composite(
    render_path: str | Path,
    reference_path: str | Path,
    out_path: str | Path,
    *,
    mode: str = "over",
    alpha: float = 0.5,
) -> Path:
    """Combine a render and a reference photograph into one comparison image.

    over        the render laid over the photograph at `alpha`, respecting the
                render's own transparency so the photograph shows around it
    difference  absolute luminance difference, so disagreement is bright
    edges       render edges in red over photograph edges in cyan, which is the
                most useful mode for silhouette and feature alignment
    split       photograph on the left half, render on the right
    """
    if mode not in OVERLAY_MODES:
        raise ReferenceError(
            "Unknown overlay mode {!r}; expected one of {}".format(mode, ", ".join(OVERLAY_MODES))
        )

    render = image_pixels(load_image(render_path))
    reference = image_pixels(load_image(reference_path))
    if render.shape[:2] != reference.shape[:2]:
        raise ReferenceError(
            "Render is {}x{} but the reference is {}x{}. Call "
            "match_resolution_to_reference() before rendering so the two align.".format(
                render.shape[1], render.shape[0], reference.shape[1], reference.shape[0]
            )
        )

    photo = reference[..., :3]
    if mode == "over":
        coverage = render[..., 3:4] * max(0.0, min(1.0, alpha))
        rgb = photo * (1.0 - coverage) + render[..., :3] * coverage
    elif mode == "difference":
        delta = np.abs(_luminance(render) - _luminance(photo))
        rgb = np.repeat(delta[..., None], 3, axis=2)
    elif mode == "edges":
        render_edges = _edges(_luminance(render) * render[..., 3])
        photo_edges = _edges(_luminance(photo))
        # A photographed subject usually sits in a cluttered room, and the room
        # out-edges the face. Damp the photograph and keep the render's own
        # outline at full strength so the thing being judged stays legible.
        photo_edges = np.clip(photo_edges * 2.2, 0.0, 1.0) * 0.55
        base = photo * 0.18
        rgb = base.copy()
        rgb[..., 0] = np.clip(base[..., 0] + render_edges * 1.6, 0.0, 1.0)
        rgb[..., 1] = np.clip(base[..., 1] + photo_edges, 0.0, 1.0)
        rgb[..., 2] = np.clip(base[..., 2] + photo_edges, 0.0, 1.0)
    else:  # split
        rgb = photo.copy()
        midpoint = rgb.shape[1] // 2
        coverage = render[:, midpoint:, 3:4]
        rgb[:, midpoint:] = (
            rgb[:, midpoint:] * (1.0 - coverage) + render[:, midpoint:, :3] * coverage
        )

    out = np.ones(render.shape, dtype=np.float32)
    out[..., :3] = np.clip(rgb, 0.0, 1.0)
    return write_image(out, out_path)


def render_against_reference(
    reference_path: str | Path,
    out_path: str | Path,
    *,
    mode: str = "edges",
    alpha: float = 0.5,
    scene: Any = None,
    keep_render: str | Path | None = None,
) -> Path:
    """Render the current camera at the reference's resolution and overlay it.

    Renders with a transparent film so the photograph remains visible around the
    subject rather than being hidden behind a background colour.
    """
    scene = scene or bpy.context.scene
    if scene.camera is None:
        raise ReferenceError("The scene has no active camera to render from")

    match_resolution_to_reference(scene, reference_path)

    previous_transparent = scene.render.film_transparent
    previous_filepath = scene.render.filepath
    render_path = Path(keep_render) if keep_render else Path(out_path).with_suffix(".render.png")
    scene.render.film_transparent = True
    scene.render.filepath = str(render_path)
    try:
        bpy.ops.render.render(write_still=True)
    finally:
        scene.render.film_transparent = previous_transparent
        scene.render.filepath = previous_filepath

    result = composite(render_path, reference_path, out_path, mode=mode, alpha=alpha)
    if keep_render is None:
        try:
            render_path.unlink()
        except OSError:
            pass
    return result


def alignment_report(
    render_path: str | Path, reference_path: str | Path
) -> dict[str, Any]:
    """Coarse numbers describing how far a render is from its reference.

    Not a fitting metric - the camera is not solved yet, so these numbers mix
    projection error with subject error. They are here to make a change
    measurable rather than to be optimised against.
    """
    render = image_pixels(load_image(render_path))
    reference = image_pixels(load_image(reference_path))
    if render.shape[:2] != reference.shape[:2]:
        raise ReferenceError("Render and reference differ in size; cannot compare")

    coverage = render[..., 3] > 0.5
    if not coverage.any():
        raise ReferenceError("The render is empty; nothing to compare")

    rows, columns = np.nonzero(coverage)
    height, width = coverage.shape
    delta = np.abs(_luminance(render) - _luminance(reference))
    return {
        "subjectPixels": int(coverage.sum()),
        "subjectFraction": float(coverage.mean()),
        "boundingBox": {
            "left": int(columns.min()), "right": int(columns.max()),
            "top": int(rows.min()), "bottom": int(rows.max()),
        },
        "centroid": {
            "x": float(columns.mean() / width), "y": float(rows.mean() / height),
        },
        "meanLuminanceDelta": float(delta[coverage].mean()),
        "note": (
            "Camera pose is not solved, so these mix projection error with subject "
            "error. Use them to compare two attempts, not as a fitting objective."
        ),
    }


# --- Aligning the render to the reference ------------------------------------


def project(scene: Any, camera: Any, world_position: Any) -> tuple[float, float]:
    """Where a world point lands in the image, normalised with (0,0) top-left."""
    from bpy_extras.object_utils import world_to_camera_view

    projected = world_to_camera_view(scene, camera, world_position)
    return (float(projected.x), 1.0 - float(projected.y))


def _separation(a: tuple[float, float], b: tuple[float, float], aspect: float) -> float:
    dx = (b[0] - a[0]) * aspect
    dy = b[1] - a[1]
    return (dx * dx + dy * dy) ** 0.5


def align_camera_to_pair(
    scene: Any,
    camera: Any,
    world_a: Any,
    world_b: Any,
    target_a: tuple[float, float],
    target_b: tuple[float, float],
    *,
    iterations: int = 8,
) -> dict[str, Any]:
    """Match two known points to where they appear in the reference.

    Two correspondences pin scale, image translation and roll. Distance is moved
    along the view axis until the projected separation matches, and camera shift
    until the projected midpoint matches. Yaw, pitch and focal length are left
    alone, so this aligns rather than fits - see the pose solve for those.

    The shift derivatives are measured rather than reasoned about. Blender's
    shift sign and its interaction with sensor fit are easy to get backwards, and
    a two-sample numerical derivative removes the question entirely.
    """
    from mathutils import Vector

    world_a = Vector(world_a)
    world_b = Vector(world_b)
    render = scene.render
    aspect = (render.resolution_x * render.pixel_aspect_x) / max(
        1e-9, render.resolution_y * render.pixel_aspect_y
    )

    target_separation = _separation(target_a, target_b, aspect)
    if target_separation < 1e-6:
        raise ReferenceError("The two reference points coincide; cannot derive a scale")
    target_mid = ((target_a[0] + target_b[0]) / 2.0, (target_a[1] + target_b[1]) / 2.0)

    pivot = (world_a + world_b) / 2.0
    history: list[dict[str, float]] = []

    for _ in range(max(1, iterations)):
        projected_a = project(scene, camera, world_a)
        projected_b = project(scene, camera, world_b)
        separation = _separation(projected_a, projected_b, aspect)
        if separation < 1e-9:
            raise ReferenceError("The subject projects to a single point; check the camera")

        # Scale: move along the view axis. Halving the distance roughly doubles
        # the projected size, so damp the step and iterate rather than jumping.
        ratio = target_separation / separation
        direction = (camera.location - pivot)
        distance = direction.length
        if distance > 1e-9:
            step = 1.0 + (1.0 / ratio - 1.0) * 0.8
            camera.location = pivot + direction * max(0.05, step)
        bpy.context.view_layer.update()

        # Translation: measure how the projection responds to shift, then solve.
        before = project(scene, camera, pivot)
        delta = 0.02
        camera.data.shift_x += delta
        bpy.context.view_layer.update()
        after_x = project(scene, camera, pivot)
        camera.data.shift_x -= delta

        camera.data.shift_y += delta
        bpy.context.view_layer.update()
        after_y = project(scene, camera, pivot)
        camera.data.shift_y -= delta
        bpy.context.view_layer.update()

        dudx = (after_x[0] - before[0]) / delta
        dvdy = (after_y[1] - before[1]) / delta
        if abs(dudx) > 1e-6:
            camera.data.shift_x += (target_mid[0] - before[0]) / dudx
        if abs(dvdy) > 1e-6:
            camera.data.shift_y += (target_mid[1] - before[1]) / dvdy
        bpy.context.view_layer.update()

        projected_a = project(scene, camera, world_a)
        projected_b = project(scene, camera, world_b)
        mid = ((projected_a[0] + projected_b[0]) / 2.0, (projected_a[1] + projected_b[1]) / 2.0)
        history.append({
            "separationError": abs(_separation(projected_a, projected_b, aspect) - target_separation),
            "midpointError": ((mid[0] - target_mid[0]) ** 2 + (mid[1] - target_mid[1]) ** 2) ** 0.5,
        })

    final = history[-1]
    return {
        "separationError": final["separationError"],
        "midpointError": final["midpointError"],
        "iterations": len(history),
        "cameraDistance": float((camera.location - pivot).length),
        "shift": [float(camera.data.shift_x), float(camera.data.shift_y)],
        "converged": final["separationError"] < 5e-3 and final["midpointError"] < 5e-3,
        "note": (
            "Scale, translation and roll only. Yaw, pitch and focal length are "
            "unsolved, so residual disagreement may still be projection rather "
            "than subject."
        ),
    }


# --- Verifying landmarks ------------------------------------------------------
#
# A solver that converges on the wrong target is indistinguishable from one that
# converges on the right one, unless somebody looks. These draw the assumption
# back onto the photograph so a bad landmark is visible before it propagates.


def _draw_marks(
    pixels: np.ndarray,
    points: Iterable[tuple[float, float]],
    colour: tuple[float, float, float],
    *,
    radius: int = 14,
    thickness: int = 2,
) -> None:
    """Crosshair each normalised point, in place. (0,0) is the top-left."""
    height, width = pixels.shape[0], pixels.shape[1]
    for u, v in points:
        cx = int(round(u * (width - 1)))
        cy = int(round(v * (height - 1)))
        if not (0 <= cx < width and 0 <= cy < height):
            continue
        for offset in range(-radius, radius + 1):
            for spread in range(-thickness, thickness + 1):
                x, y = cx + offset, cy + spread
                if 0 <= x < width and 0 <= y < height:
                    pixels[y, x, :3] = colour
                x, y = cx + spread, cy + offset
                if 0 <= x < width and 0 <= y < height:
                    pixels[y, x, :3] = colour
        # Leave the exact point readable rather than buried under the crosshair.
        gap = max(1, radius // 4)
        pixels[max(0, cy - gap):cy + gap + 1, max(0, cx - gap):cx + gap + 1, :3] = 1.0 - np.array(colour)


def annotate(
    image_path: str | Path,
    out_path: str | Path,
    *,
    grid: int = 20,
    points: Iterable[tuple[float, float]] | None = None,
    compare: Iterable[tuple[float, float]] | None = None,
) -> Path:
    """Overlay a labelled grid and landmark marks on a photograph.

    grid     number of divisions; every fifth line is drawn brighter so a
             coordinate can be read off without counting from the edge
    points   assumed landmarks, drawn in green
    compare  the same landmarks as currently projected from the mesh, drawn in
             magenta, so the two can be seen disagreeing

    Reading a coordinate against labelled gridlines is far more accurate than
    judging a fraction of an image, which is how the t-938 targets were wrong.
    """
    pixels = image_pixels(load_image(image_path)).copy()
    height, width = pixels.shape[0], pixels.shape[1]

    if grid > 0:
        for index in range(1, grid):
            strong = index % 5 == 0
            value = 1.0 if strong else 0.45
            span = 2 if strong else 1
            x = int(round(index / grid * (width - 1)))
            y = int(round(index / grid * (height - 1)))
            pixels[:, max(0, x - span + 1):x + 1, 0] = value
            pixels[:, max(0, x - span + 1):x + 1, 1] = value * 0.85
            pixels[:, max(0, x - span + 1):x + 1, 2] = 0.0
            pixels[max(0, y - span + 1):y + 1, :, 0] = value
            pixels[max(0, y - span + 1):y + 1, :, 1] = value * 0.85
            pixels[max(0, y - span + 1):y + 1, :, 2] = 0.0

    if points:
        _draw_marks(pixels, points, (0.0, 1.0, 0.2))
    if compare:
        _draw_marks(pixels, compare, (1.0, 0.0, 0.9))

    return write_image(pixels, out_path, name="AlepouAnnotated")


def grid_reading_hint(grid: int = 20) -> str:
    """How to convert a gridline count into a normalised coordinate."""
    return (
        "Grid has {n} divisions, so each cell is {step:.3f} of the image and every "
        "fifth line is brighter. A point k cells from the left is k/{n} across; k "
        "cells from the top is k/{n} down.".format(n=grid, step=1.0 / grid)
    )


# --- Solving the camera -------------------------------------------------------


def _camera_parameters(camera: Any, solve_focal: bool) -> list[float]:
    values = list(camera.location) + list(camera.rotation_euler)
    if solve_focal:
        values.append(camera.data.lens)
    return values


def _apply_parameters(
    camera: Any,
    values: Iterable[float],
    solve_focal: bool,
    focal_range: tuple[float, float] = (4.0, 400.0),
) -> None:
    values = list(values)
    camera.location = values[0:3]
    camera.rotation_euler = values[3:6]
    if solve_focal:
        camera.data.lens = min(max(values[6], focal_range[0]), focal_range[1])
    bpy.context.view_layer.update()


def _residuals(
    scene: Any, camera: Any, correspondences: list[tuple[Any, tuple[float, float]]], aspect: float
) -> np.ndarray:
    out = np.empty(len(correspondences) * 2, dtype=np.float64)
    for index, (world_position, target) in enumerate(correspondences):
        u, v = project(scene, camera, world_position)
        out[index * 2] = (u - target[0]) * aspect
        out[index * 2 + 1] = v - target[1]
    return out


def solve_camera_pose(
    scene: Any,
    camera: Any,
    correspondences: list[tuple[Any, tuple[float, float]]],
    *,
    solve_focal: bool = True,
    focal_range: tuple[float, float] = (18.0, 300.0),
    iterations: int = 60,
) -> dict[str, Any]:
    """Fit camera position, orientation and focal length to marked landmarks.

    Levenberg-Marquardt over the camera parameters, with the Jacobian taken by
    finite difference through Blender's own projection. Going through Blender
    rather than reimplementing the projection means sensor fit, shift and aspect
    are handled by the same code that will render the result.

    This is what turns perspective into a modelled property: a photograph taken
    at arm's length solves to a near camera with a short focal length, and the
    enlarged nose is explained rather than absorbed into the anatomy.

    Bound the focal length. When the subject's shape does not match the mesh, an
    unbounded solve will happily invent an extreme wide angle to drive the
    residual down, trading a plausible camera for a lower number. A solve that
    parks against its bound is reporting a shape problem, not a camera one, and
    focalAtBound says so.
    """
    minimum = 6 if not solve_focal else 4
    if len(correspondences) < minimum:
        raise ReferenceError(
            "Need at least {} correspondences to solve the camera, got {}".format(
                minimum, len(correspondences)
            )
        )

    render = scene.render
    aspect = (render.resolution_x * render.pixel_aspect_x) / max(
        1e-9, render.resolution_y * render.pixel_aspect_y
    )

    parameters = np.array(_camera_parameters(camera, solve_focal), dtype=np.float64)
    steps = np.array([0.004] * 3 + [0.004] * 3 + ([0.5] if solve_focal else []), dtype=np.float64)

    _apply_parameters(camera, parameters, solve_focal, focal_range)
    residual = _residuals(scene, camera, correspondences, aspect)
    cost = float(residual @ residual)
    damping = 1e-3
    history = [cost]

    for _ in range(max(1, iterations)):
        jacobian = np.empty((residual.size, parameters.size), dtype=np.float64)
        for column in range(parameters.size):
            probe = parameters.copy()
            probe[column] += steps[column]
            _apply_parameters(camera, probe, solve_focal, focal_range)
            jacobian[:, column] = (
                _residuals(scene, camera, correspondences, aspect) - residual
            ) / steps[column]
        _apply_parameters(camera, parameters, solve_focal, focal_range)

        normal = jacobian.T @ jacobian
        gradient = jacobian.T @ residual
        improved = False
        for _attempt in range(8):
            try:
                delta = np.linalg.solve(
                    normal + damping * np.diag(np.diag(normal) + 1e-12), -gradient
                )
            except np.linalg.LinAlgError:
                damping *= 10.0
                continue
            candidate = parameters + delta
            _apply_parameters(camera, candidate, solve_focal, focal_range)
            trial = _residuals(scene, camera, correspondences, aspect)
            trial_cost = float(trial @ trial)
            if trial_cost < cost:
                parameters, residual, cost = candidate, trial, trial_cost
                damping = max(1e-9, damping * 0.4)
                improved = True
                break
            damping *= 10.0
        _apply_parameters(camera, parameters, solve_focal, focal_range)
        history.append(cost)
        if not improved or (len(history) > 2 and abs(history[-2] - cost) < 1e-12):
            break

    per_point = np.sqrt(residual.reshape(-1, 2) ** 2 @ np.ones(2))
    return {
        "rmsError": float(np.sqrt(cost / max(1, len(correspondences)))),
        "worstPointError": float(per_point.max()),
        "perPointError": [round(float(v), 5) for v in per_point],
        "iterations": len(history) - 1,
        "focalLength_mm": round(float(camera.data.lens), 2),
        "cameraDistance": None,
        "solvedFocal": bool(solve_focal),
        "focalRange_mm": [focal_range[0], focal_range[1]],
        "focalAtBound": bool(
            solve_focal
            and (camera.data.lens <= focal_range[0] + 1e-6
                 or camera.data.lens >= focal_range[1] - 1e-6)
        ),
        "note": (
            "Errors are normalised image units, so 0.01 is one percent of the "
            "image. A low error only means the camera explains the marked points; "
            "it says nothing about whether the points were marked correctly."
        ),
    }
