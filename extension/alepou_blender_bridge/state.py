"""Bounded, exact Blender state inspection."""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Iterable

import bpy
from mathutils import Vector


def vector(value: Iterable[float], digits: int = 6) -> list[float]:
    return [round(float(component), digits) for component in value]


def matrix(value: Any, digits: int = 6) -> list[list[float]]:
    return [[round(float(component), digits) for component in row] for row in value]


def _corners(obj: Any, world_matrix: Any) -> list[Vector]:
    try:
        return [world_matrix @ Vector(corner) for corner in obj.bound_box]
    except (AttributeError, TypeError, ValueError):
        return [world_matrix.translation.copy()]


def _aabb(corners: list[Vector]) -> dict[str, Any]:
    minimum = Vector((min(point[index] for point in corners) for index in range(3)))
    maximum = Vector((max(point[index] for point in corners) for index in range(3)))
    center = (minimum + maximum) * 0.5
    size = maximum - minimum
    return {
        "min": vector(minimum),
        "max": vector(maximum),
        "center": vector(center),
        "size": vector(size),
    }


def bounds_for_object(obj: Any, depsgraph: Any | None = None) -> dict[str, Any]:
    depsgraph = depsgraph or bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    authored_local = _aabb([Vector(corner) for corner in obj.bound_box]) if hasattr(obj, "bound_box") else None
    evaluated_local = _aabb([Vector(corner) for corner in evaluated.bound_box]) if hasattr(evaluated, "bound_box") else None
    return {
        "method": "object_bound_box_aabb",
        "authored": {
            "local": authored_local,
            "world": _aabb(_corners(obj, obj.matrix_world)),
            "matrixWorld": matrix(obj.matrix_world),
        },
        "evaluated": {
            "local": evaluated_local,
            "world": _aabb(_corners(evaluated, evaluated.matrix_world)),
            "matrixWorld": matrix(evaluated.matrix_world),
        },
        "conservative": True,
    }


def _identity(obj: Any) -> dict[str, Any]:
    library = getattr(getattr(obj, "library", None), "filepath", None)
    return {
        "name": obj.name,
        "nameFull": obj.name_full,
        "type": obj.type,
        "library": library,
        "alepouId": obj.get("alepou_id"),
        "spatialId": obj.get("spatial.entity_id") or obj.get("spatial_id"),
    }


def object_summary(obj: Any, depsgraph: Any | None = None, include_bounds: bool = True) -> dict[str, Any]:
    depsgraph = depsgraph or bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    result = {
        **_identity(obj),
        "parent": obj.parent.name_full if obj.parent else None,
        "collections": [collection.name_full for collection in obj.users_collection[:20]],
        "visible": obj.visible_get(),
        "hideViewport": bool(obj.hide_viewport),
        "hideRender": bool(obj.hide_render),
        "selected": bool(obj.select_get()),
        "authoredTransform": {
            "location": vector(obj.location),
            "rotationEuler": vector(obj.rotation_euler),
            "scale": vector(obj.scale),
            "matrixWorld": matrix(obj.matrix_world),
        },
        "evaluatedTransform": {
            "locationWorld": vector(evaluated.matrix_world.translation),
            "matrixWorld": matrix(evaluated.matrix_world),
        },
    }
    if include_bounds:
        result["bounds"] = bounds_for_object(obj, depsgraph)
    return result


def find_object(name: str) -> Any:
    obj = bpy.data.objects.get(str(name or ""))
    if obj is None:
        raise ValueError(f"Object not found: {name}")
    return obj


def scene_summary(max_types: int = 50) -> dict[str, Any]:
    scene = bpy.context.scene
    objects = list(scene.objects)
    type_counts = Counter(obj.type for obj in objects)
    active = bpy.context.view_layer.objects.active
    return {
        "scene": scene.name,
        "viewLayer": bpy.context.view_layer.name,
        "blendFile": bpy.data.filepath or None,
        "isSaved": bool(bpy.data.filepath),
        "isDirty": bool(bpy.data.is_dirty),
        "frame": scene.frame_current,
        "frameRange": [scene.frame_start, scene.frame_end],
        "objectCount": len(objects),
        "objectTypes": dict(type_counts.most_common(max_types)),
        "collectionCount": len(bpy.data.collections),
        "materialCount": len(bpy.data.materials),
        "meshCount": len(bpy.data.meshes),
        "selectedCount": len(bpy.context.selected_objects),
        "activeObject": active.name_full if active else None,
        "activeMode": active.mode if active else "OBJECT",
        "render": {
            "engine": scene.render.engine,
            "resolution": [scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage],
            "camera": scene.camera.name_full if scene.camera else None,
        },
    }


def selection_summary(limit: int = 100) -> dict[str, Any]:
    selected = list(bpy.context.selected_objects)
    active = bpy.context.view_layer.objects.active
    return {
        "count": len(selected),
        "truncated": len(selected) > limit,
        "activeObject": active.name_full if active else None,
        "objects": [_identity(obj) for obj in selected[:limit]],
    }


def objects_summary(limit: int = 500, offset: int = 0) -> dict[str, Any]:
    objects = sorted(bpy.context.scene.objects, key=lambda obj: obj.name_full.lower())
    page = objects[offset : offset + limit]
    depsgraph = bpy.context.evaluated_depsgraph_get()
    return {
        "total": len(objects),
        "offset": offset,
        "limit": limit,
        "truncated": offset + len(page) < len(objects),
        "objects": [object_summary(obj, depsgraph, include_bounds=False) for obj in page],
    }


def collections_summary(limit: int = 250) -> dict[str, Any]:
    collections = sorted(bpy.data.collections, key=lambda item: item.name_full.lower())
    return {
        "total": len(collections),
        "truncated": len(collections) > limit,
        "collections": [
            {
                "name": collection.name_full,
                "objects": len(collection.objects),
                "children": [child.name_full for child in collection.children[:50]],
                "hideViewport": collection.hide_viewport,
                "hideRender": collection.hide_render,
            }
            for collection in collections[:limit]
        ],
    }


def inspect_object(name: str) -> dict[str, Any]:
    obj = find_object(name)
    result = object_summary(obj)
    result["modifiers"] = [
        {"name": modifier.name, "type": modifier.type, "showViewport": modifier.show_viewport, "showRender": modifier.show_render}
        for modifier in obj.modifiers[:100]
    ]
    result["constraints"] = [
        {"name": constraint.name, "type": constraint.type, "influence": round(float(constraint.influence), 6)}
        for constraint in obj.constraints[:100]
    ]
    result["materials"] = [slot.material.name_full if slot.material else None for slot in obj.material_slots[:100]]
    result["data"] = {
        "name": getattr(obj.data, "name_full", None),
        "users": getattr(obj.data, "users", None),
    }
    return result


def mesh_stats(name: str, evaluated: bool = True) -> dict[str, Any]:
    obj = find_object(name)
    if obj.type != "MESH":
        raise ValueError(f"Object is not a mesh: {name}")
    depsgraph = bpy.context.evaluated_depsgraph_get()
    source = obj.evaluated_get(depsgraph) if evaluated else obj
    mesh = source.to_mesh(preserve_all_data_layers=False, depsgraph=depsgraph) if evaluated else obj.data
    try:
        mesh.calc_loop_triangles()
        edge_face_counts = Counter()
        for polygon in mesh.polygons:
            for key in polygon.edge_keys:
                edge_face_counts[tuple(sorted(key))] += 1
        loose_edges = sum(1 for edge in mesh.edges if edge.is_loose)
        boundary_edges = sum(1 for count in edge_face_counts.values() if count == 1)
        non_manifold_edges = sum(1 for count in edge_face_counts.values() if count != 2)
        degenerate_faces = sum(1 for polygon in mesh.polygons if polygon.area <= 1e-12)
        return {
            "object": obj.name_full,
            "state": "evaluated" if evaluated else "authored",
            "vertices": len(mesh.vertices),
            "edges": len(mesh.edges),
            "faces": len(mesh.polygons),
            "triangles": len(mesh.loop_triangles),
            "looseEdges": loose_edges,
            "boundaryEdges": boundary_edges,
            "nonManifoldEdges": non_manifold_edges,
            "degenerateFaces": degenerate_faces,
        }
    finally:
        if evaluated:
            source.to_mesh_clear()


def material_inspect(name: str) -> dict[str, Any]:
    material = bpy.data.materials.get(str(name or ""))
    if material is None:
        raise ValueError(f"Material not found: {name}")
    nodes = []
    links = []
    if material.use_nodes and material.node_tree:
        nodes = [
            {"name": node.name, "label": node.label, "type": node.bl_idname, "location": vector(node.location)}
            for node in list(material.node_tree.nodes)[:200]
        ]
        links = [
            {
                "fromNode": link.from_node.name,
                "fromSocket": link.from_socket.name,
                "toNode": link.to_node.name,
                "toSocket": link.to_socket.name,
            }
            for link in list(material.node_tree.links)[:300]
        ]
    return {
        "name": material.name_full,
        "users": material.users,
        "useNodes": material.use_nodes,
        "surfaceRenderMethod": getattr(material, "surface_render_method", None),
        "nodes": nodes,
        "links": links,
        "truncated": len(nodes) >= 200 or len(links) >= 300,
    }


def distance(first: str, second: str) -> dict[str, Any]:
    a = find_object(first)
    b = find_object(second)
    delta = b.matrix_world.translation - a.matrix_world.translation
    return {
        "first": a.name_full,
        "second": b.name_full,
        "method": "evaluated_world_origins",
        "delta": vector(delta),
        "distance": round(float(delta.length), 6),
    }


def alignment(names: list[str], axis: str = "X", tolerance: float = 1e-4) -> dict[str, Any]:
    if len(names) < 2:
        raise ValueError("scene.alignment requires at least two object names")
    axis_index = {"X": 0, "Y": 1, "Z": 2}.get(axis.upper())
    if axis_index is None:
        raise ValueError("axis must be X, Y, or Z")
    values = [(find_object(name).name_full, float(find_object(name).matrix_world.translation[axis_index])) for name in names]
    minimum = min(value for _, value in values)
    maximum = max(value for _, value in values)
    return {
        "axis": axis.upper(),
        "method": "evaluated_world_origins",
        "tolerance": tolerance,
        "spread": round(maximum - minimum, 6),
        "aligned": maximum - minimum <= tolerance,
        "values": [{"object": name, "value": round(value, 6)} for name, value in values],
    }


def intersections(names: list[str] | None = None, limit: int = 200) -> dict[str, Any]:
    objects = [find_object(name) for name in names] if names else [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    depsgraph = bpy.context.evaluated_depsgraph_get()
    bounds = {obj.name_full: bounds_for_object(obj, depsgraph)["evaluated"]["world"] for obj in objects[:limit]}
    overlaps = []
    entries = list(bounds.items())
    for index, (first_name, first) in enumerate(entries):
        for second_name, second in entries[index + 1 :]:
            if all(first["min"][axis] <= second["max"][axis] and second["min"][axis] <= first["max"][axis] for axis in range(3)):
                overlaps.append({"first": first_name, "second": second_name})
    return {
        "method": "evaluated_world_aabb_overlap",
        "conservative": True,
        "objectCount": len(entries),
        "overlaps": overlaps[:1000],
        "truncated": len(overlaps) > 1000 or len(objects) > limit,
    }


def union_bounds(objects: Iterable[Any] | None = None) -> dict[str, Any]:
    chosen = list(objects) if objects is not None else [obj for obj in bpy.context.scene.objects if obj.type not in {"CAMERA", "LIGHT"} and obj.visible_get()]
    if not chosen:
        center = Vector((0.0, 0.0, 0.0))
        minimum = Vector((-1.0, -1.0, -1.0))
        maximum = Vector((1.0, 1.0, 1.0))
    else:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        boxes = [bounds_for_object(obj, depsgraph)["evaluated"]["world"] for obj in chosen]
        minimum = Vector(tuple(min(box["min"][axis] for box in boxes) for axis in range(3)))
        maximum = Vector(tuple(max(box["max"][axis] for box in boxes) for axis in range(3)))
        center = (minimum + maximum) * 0.5
    return {"min": vector(minimum), "max": vector(maximum), "center": vector(center), "size": vector(maximum - minimum)}
