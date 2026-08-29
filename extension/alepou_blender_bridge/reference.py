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
