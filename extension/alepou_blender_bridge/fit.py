"""Solve morph weights from marked landmarks.

The chain is: mark landmarks on a photograph, solve the camera so projection
error stops masquerading as anatomy, then solve the weights that move the
projected landmarks onto the marked ones.

The Jacobian is measured, not derived. Perturbing each morph and re-projecting
records how that morph actually moves each landmark at this pose, which avoids a
hand-authored mapping table that would drift as the morph set changes.

Two properties matter more than the fit quality:

* the system is wildly underdetermined - a few marked points against hundreds of
  morphs - so it is regularised toward few, small weights rather than allowed to
  find a pile of large cancelling ones that happens to satisfy the residual;
* landmarks occluded in the photograph must be excluded by the caller, because a
  large residual cannot distinguish a wrong model from a wrong mark.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import bpy
import numpy as np

from . import human, human_data, reference
from .human_data import HumanDataError

DEFAULT_WEIGHT_BOUNDS = (-0.6, 1.2)


def candidate_morphs(pack: str | Path, *, regions: Iterable[str] | None = None) -> list[Path]:
    """Target files worth fitting with, in a stable order."""
    pack = Path(pack)
    wanted = set(regions or (
        "head", "forehead", "nose", "mouth", "chin", "cheek", "eyes", "eyebrows", "ears",
    ))
    found: list[Path] = []
    for path in human_data.iter_targets(pack / "targets"):
        try:
            region = path.relative_to(pack / "targets").parts[0]
        except ValueError:
            continue
        if region in wanted:
            found.append(path)
    return found


def ensure_morphs(obj: Any, targets: Iterable[Path]) -> list[str]:
    """Add every target as a shape key once, left at zero."""
    names: list[str] = []
    for path in targets:
        name = human_data.morph_name(path)
        existing = obj.data.shape_keys.key_blocks.get(name) if obj.data.shape_keys else None
        if existing is None:
            key = human.add_morph(obj, path)
        else:
            key = existing
        key.slider_min, key.slider_max = -2.0, 2.0
        key.value = 0.0
        names.append(name)
    bpy.context.view_layer.update()
    return names


def _projected(scene: Any, camera: Any, obj: Any, pack: Any, order: list[str]) -> np.ndarray:
    points = human.landmarks(obj, pack)
    out = np.empty(len(order) * 2, dtype=np.float64)
    for index, name in enumerate(order):
        u, v = reference.project(scene, camera, points[name])
        out[index * 2], out[index * 2 + 1] = u, v
    return out


def build_jacobian(
    scene: Any,
    camera: Any,
    obj: Any,
    pack: str | Path,
    landmark_order: list[str],
    morph_names: list[str],
    *,
    step: float = 0.5,
    keep_threshold: float = 2e-4,
) -> tuple[np.ndarray, list[str], np.ndarray]:
    """Measure how each morph moves each projected landmark.

    Morphs that barely move any marked landmark are dropped. Keeping them would
    let the solver assign them large values for negligible benefit, since they
    are nearly free in the residual and only weakly penalised.
    """
    keys = obj.data.shape_keys.key_blocks
    baseline = _projected(scene, camera, obj, pack, landmark_order)

    columns: list[np.ndarray] = []
    kept: list[str] = []
    for name in morph_names:
        key = keys.get(name)
        if key is None:
            continue
        previous = key.value
        key.value = previous + step
        bpy.context.view_layer.update()
        moved = _projected(scene, camera, obj, pack, landmark_order)
        key.value = previous
        bpy.context.view_layer.update()

        column = (moved - baseline) / step
        if float(np.abs(column).max()) >= keep_threshold:
            columns.append(column)
            kept.append(name)

    if not columns:
        raise HumanDataError(
            "No morph measurably moves the marked landmarks. Check that the landmarks "
            "are the ones the morphs affect, and that the camera is solved."
        )
    return np.column_stack(columns), kept, baseline


def solve_weights(
    jacobian: np.ndarray,
    residual: np.ndarray,
    *,
    ridge: float = 0.02,
    bounds: tuple[float, float] = DEFAULT_WEIGHT_BOUNDS,
    passes: int = 40,
) -> np.ndarray:
    """Ridge-regularised least squares with box bounds, by projected gradient.

    Ridge rather than plain least squares because the system is underdetermined:
    without it the solver finds enormous cancelling weights that satisfy the
    residual and produce a monstrous face.
    """
    scale = float(np.abs(jacobian).max()) or 1.0
    normalised = jacobian / scale
    target = -residual / scale

    normal = normalised.T @ normalised + ridge * np.eye(normalised.shape[1])
    gradient_target = normalised.T @ target
    weights = np.linalg.solve(normal, gradient_target)
    weights = np.clip(weights, bounds[0], bounds[1])

    # Projected gradient descent to respect the bounds rather than merely clip.
    step = 1.0 / (float(np.linalg.eigvalsh(normal).max()) or 1.0)
    for _ in range(max(0, passes)):
        weights = np.clip(
            weights - step * (normal @ weights - gradient_target), bounds[0], bounds[1]
        )
    return weights


def apply_weights(obj: Any, names: list[str], weights: np.ndarray) -> dict[str, float]:
    keys = obj.data.shape_keys.key_blocks
    applied: dict[str, float] = {}
    for name, value in zip(names, weights):
        key = keys.get(name)
        if key is None:
            continue
        key.value = float(value)
        if abs(float(value)) > 1e-3:
            applied[name] = round(float(value), 4)
    bpy.context.view_layer.update()
    return applied


def fit_landmarks(
    scene: Any,
    camera: Any,
    obj: Any,
    pack: str | Path,
    marks: dict[str, tuple[float, float]],
    *,
    rounds: int = 3,
    ridge: float = 0.06,
    bounds: tuple[float, float] = DEFAULT_WEIGHT_BOUNDS,
    keep_threshold: float = 1.2e-3,
    resolve_camera: bool = True,
    focal_range: tuple[float, float] = (35.0, 200.0),
) -> dict[str, Any]:
    """Alternate camera and shape until neither improves the landmark residual."""
    pack = Path(pack)
    order = [name for name in sorted(marks) if name in human.landmarks(obj, pack)]
    if len(order) < 4:
        raise HumanDataError(
            "Need at least 4 marked landmarks that exist on the mesh, got {}".format(len(order))
        )
    target = np.array([marks[name] for name in order], dtype=np.float64).ravel()

    morph_names = ensure_morphs(obj, candidate_morphs(pack))
    history: list[dict[str, Any]] = []
    applied: dict[str, float] = {}

    for _round in range(max(1, rounds)):
        if resolve_camera:
            points = human.landmarks(obj, pack)
            reference.solve_camera_pose(
                scene, camera,
                [(points[name], marks[name]) for name in order],
                focal_range=focal_range,
            )

        jacobian, kept, baseline = build_jacobian(
            scene, camera, obj, pack, order, morph_names, keep_threshold=keep_threshold)
        residual = baseline - target
        weights = solve_weights(jacobian, residual, ridge=ridge, bounds=bounds)

        # Bound the accumulated weight, not the increment. Clipping only the step
        # let three rounds drift far outside the range and produced a coned
        # skull that still satisfied every marked landmark.
        current = np.array(
            [obj.data.shape_keys.key_blocks[name].value for name in kept], dtype=np.float64
        )
        combined = np.clip(current + weights, bounds[0], bounds[1])
        applied = apply_weights(obj, kept, combined)

        after = _projected(scene, camera, obj, pack, order)
        history.append({
            "round": _round + 1,
            "morphsConsidered": len(morph_names),
            "morphsKept": len(kept),
            "rmsBefore": round(float(np.sqrt((residual ** 2).reshape(-1, 2).sum(1).mean())), 5),
            "rmsAfter": round(float(np.sqrt(((after - target) ** 2).reshape(-1, 2).sum(1).mean())), 5),
        })

    final = _projected(scene, camera, obj, pack, order)
    per_point = np.sqrt(((final - target) ** 2).reshape(-1, 2).sum(1))
    return {
        "landmarks": order,
        "history": history,
        "rmsError": round(float(per_point.mean()), 5),
        "worstPointError": round(float(per_point.max()), 5),
        "perPointError": {name: round(float(value), 5) for name, value in zip(order, per_point)},
        "weights": applied,
        "note": (
            "Residual is in normalised image units. It measures agreement with the "
            "marked points only. Landmarks hidden in the photograph should not be "
            "marked at all, because a large residual cannot tell a wrong model from "
            "a wrong mark."
        ),
    }
