import tempfile
import unittest
from pathlib import Path

import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "examples" / "spatial"))

import spatial
from spatial_core import (
    ConstraintConflictError,
    ExistingEntity,
    ExternalModificationConflictError,
    InvalidFrameGraphError,
    InvalidParameterError,
    UnknownReferenceError,
    build_compile_plan,
)
from triple_quad import make_scene as example_triple_quad


def triple_quad(chamber_length=220):
    scene = spatial.Scene("triple_quad", units="mm")
    frame = scene.frame("analyser_frame", origin=(0, 0, 430))
    beam = scene.axis("beam", frame=frame, direction=(1, 0, 0))
    rods = spatial.CylinderSpec(radius=18, length=300, axis="X")
    q1 = scene.radial_array(
        "Q1_rods",
        count=4,
        axis=beam,
        radius=55,
        start_angle=45,
        element=rods,
        frame=frame,
        center=(-290, 0, 0),
    )
    chamber = scene.cylinder("collision_cell", radius=40, length=chamber_length, axis="X", frame=frame)
    q3 = scene.radial_array(
        "Q3_rods",
        count=4,
        axis=beam,
        radius=55,
        start_angle=45,
        element=rods,
        frame=frame,
    )
    scene.assembly("analyser", frame=frame, children=[q1, chamber, q3])
    q1.center_on(beam, id="q1_on_beam")
    chamber.after(q1, gap=60, axis="X", id="cell_after_q1")
    chamber.center_on(beam, id="cell_on_beam")
    q3.after(chamber, gap=60, axis="X", id="q3_after_cell")
    q3.center_on(beam, id="q3_on_beam")
    return scene


class SpatialCoreTests(unittest.TestCase):
    def test_relations_arrays_hierarchy_and_why_are_deterministic(self):
        resolved = triple_quad().resolve()
        self.assertEqual(resolved.entities["collision_cell"].local_center, (30.0, 0.0, 0.0))
        self.assertEqual(resolved.entities["Q3_rods"].local_center, (350.0, 0.0, 0.0))
        self.assertEqual(resolved.entities["Q3_rods"].world_center, (350.0, 0.0, 430.0))
        self.assertEqual(resolved.entities["Q1_rods"].children, [f"Q1_rods[{index}]" for index in range(4)])
        self.assertEqual(resolved.entities["analyser"].children, ["Q1_rods", "collision_cell", "Q3_rods"])
        why = resolved.why("Q3_rods.center.x")
        self.assertEqual(why["source"], "relation:q3_after_cell")
        self.assertIn("collision_cell.center.x", why["dependencies"])
        self.assertEqual(resolved.to_dict(), triple_quad().resolve().to_dict())

    def test_parameter_edit_resolves_adjacent_relationships(self):
        first = triple_quad(220).resolve()
        second = triple_quad(450).resolve()
        self.assertEqual(first.entities["Q1_rods"].local_center, second.entities["Q1_rods"].local_center)
        self.assertEqual(first.entities["collision_cell"].local_center[0], 30.0)
        self.assertEqual(second.entities["collision_cell"].local_center[0], 145.0)
        self.assertEqual(second.entities["Q3_rods"].local_center[0], 580.0)
        self.assertEqual(len(second.entities["Q3_rods"].children), 4)

    def test_python_yaml_json_share_one_canonical_ir(self):
        scene = triple_quad()
        with tempfile.TemporaryDirectory() as directory:
            yaml_path = Path(directory) / "scene.yaml"
            json_path = Path(directory) / "scene.json"
            scene.write_yaml(yaml_path)
            scene.write_json(json_path)
            self.assertEqual(spatial.Scene.load(yaml_path).canonical_json(), scene.canonical_json())
            self.assertEqual(spatial.Scene.load(json_path).canonical_json(), scene.canonical_json())

    def test_committed_python_and_yaml_examples_are_equivalent(self):
        yaml_scene = spatial.Scene.load(REPO / "examples" / "spatial" / "triple_quad.spatial.yaml")
        self.assertEqual(yaml_scene.canonical_json(), example_triple_quad().canonical_json())

    def test_grid_identity_and_anchor_round_trip(self):
        scene = spatial.Scene("rack", units="cm")
        pump = scene.box("pump", size=(24, 16, 18), center=(0, 0, 9))
        pump.anchor("outlet", position=(12, 0, 3), direction=(1, 0, 0))
        rack = scene.grid_array(
            "rack_tubes",
            counts=(3, 2),
            pitch=(4.2, 4.2),
            plane="XY",
            center=(0, 0, 7),
            element=spatial.CylinderSpec(radius=1.1, length=12, axis="Z"),
        )
        scene.assembly("system", children=[pump, rack])
        resolved = scene.resolve()
        self.assertEqual(resolved.entities["rack_tubes"].children, [f"rack_tubes[{index}]" for index in range(6)])
        self.assertEqual(resolved.inspect("pump")["anchors"]["outlet"]["position"], [12.0, 0.0, 3.0])

    def test_geometry_quality_and_shading_are_explicit_and_round_trip(self):
        scene = spatial.Scene("quality")
        scene.cylinder("faceted", radius=1, length=2, segments=8, shading="flat")
        scene.cylinder("mechanical", radius=1, length=2, segments=96, shading=spatial.Shading.smooth_by_angle(35))
        scene.sphere("round", radius=1, segments=64, rings=32, shading="smooth")
        scene.torus("trim", major_radius=2, minor_radius=0.1, major_segments=80, minor_segments=16)
        encoded = scene.to_dict()
        self.assertEqual(encoded["objects"]["faceted"]["segments"], 8)
        self.assertEqual(encoded["objects"]["faceted"]["shading"], {"mode": "flat"})
        self.assertEqual(encoded["objects"]["mechanical"]["segments"], 96)
        self.assertEqual(
            encoded["objects"]["mechanical"]["shading"],
            {"mode": "smooth_by_angle", "angleDegrees": 35.0},
        )
        self.assertEqual(encoded["objects"]["round"]["rings"], 32)
        self.assertEqual(encoded["objects"]["trim"]["major_segments"], 80)
        self.assertEqual(spatial.Scene.from_dict(encoded).canonical_json(), scene.canonical_json())
        with self.assertRaises(InvalidParameterError):
            scene.cylinder("bad_segments", radius=1, length=1, segments=2)
        with self.assertRaises(InvalidParameterError):
            spatial.Shading.smooth_by_angle(180)

    def test_asset_constitution_places_declared_pivot_on_origin_and_ground(self):
        scene = spatial.Scene("lamp_asset", units="mm")
        base_frame = scene.frame("base_frame", origin=(-1050, 0, 780))
        base = scene.cylinder(
            "base_body",
            radius=138,
            length=360,
            axis="Z",
            frame=base_frame,
            center=(0, 0, -560),
        )
        base.anchor("asset_origin", position=(0, 0, -180), direction=(1, 0, 0), up=(0, 0, 1))
        root = scene.assembly("lamp", children=(base,))
        scene.asset(
            root=root,
            origin="base_body.asset_origin",
            center_axes=("X", "Y"),
            ground_axis="Z",
            up="Z",
            forward="X",
        )

        resolved = scene.resolve()
        self.assertEqual(resolved.entities["lamp"].world_center, (0.0, 0.0, 0.0))
        self.assertEqual(resolved.entities["base_body"].world_center, (0.0, 0.0, 180.0))
        self.assertEqual(resolved.entities["lamp"].bounds.minimum[2], 0.0)
        self.assertEqual(resolved.asset["translation"], [1050.0, -0.0, -40.0])
        self.assertEqual(resolved.asset["originWorld"], [0.0, 0.0, 0.0])
        self.assertEqual(resolved.inspect("lamp")["assetConstitution"]["origin"], "base_body.asset_origin")
        self.assertEqual(spatial.Scene.from_dict(scene.to_dict()).canonical_json(), scene.canonical_json())

        outsider_scene = spatial.Scene("bad_asset")
        member = outsider_scene.box("member", size=(1, 1, 1))
        outsider = outsider_scene.box("outsider", size=(1, 1, 1))
        outsider.anchor("origin", position=(0, 0, 0))
        bad_root = outsider_scene.assembly("root", children=(member,))
        outsider_scene.asset(root=bad_root, origin="outsider.origin")
        with self.assertRaises(ConstraintConflictError):
            outsider_scene.resolve()

    def test_conflicting_authored_and_derived_center_is_rejected(self):
        scene = spatial.Scene("conflict")
        a = scene.box("A", size=(2, 2, 2), center=(0, 0, 0))
        b = scene.box("B", size=(2, 2, 2), center=(0, 0, 0))
        b.after(a, gap=1, axis="X", id="b_after_a")
        with self.assertRaises(ConstraintConflictError) as caught:
            scene.resolve()
        self.assertIn("b_after_a", str(caught.exception))

    def test_unknown_reference_and_frame_cycle_are_repairable(self):
        scene = spatial.Scene("unknown")
        scene.box("A", size=(1, 1, 1))
        scene.relate("A", "aligned_with", "missing", axes=("X",), id="bad_ref")
        with self.assertRaises(UnknownReferenceError):
            scene.validate()
        cyclic = spatial.Scene("cycle")
        cyclic.frame("first", parent="second")
        cyclic.frame("second", parent="first")
        with self.assertRaises(InvalidFrameGraphError):
            cyclic.validate()

    def test_update_plan_preserves_raw_objects_and_detects_conflicts(self):
        first = triple_quad(220).resolve()
        first_plan = build_compile_plan(first, mode="update")
        existing = [
            ExistingEntity(
                operation.entity_id,
                operation.desired["fingerprint"],
                managed=True,
                scene_id=first.id,
            )
            for operation in first_plan.operations
            if operation.desired is not None
        ]
        existing.append(ExistingEntity("UserSculpt", "raw", managed=False, scene_id=None, externally_modified=True))
        unchanged = build_compile_plan(first, mode="update", existing=existing)
        self.assertTrue(all(item.action == "unchanged" for item in unchanged.operations))
        changed = triple_quad(450).resolve()
        conflict_existing = [
            ExistingEntity(item.entity_id, item.fingerprint, item.managed, item.scene_id, item.entity_id == "collision_cell")
            for item in existing
        ]
        with self.assertRaises(ExternalModificationConflictError):
            build_compile_plan(changed, mode="update", existing=conflict_existing)
        forced = build_compile_plan(changed, mode="update", existing=conflict_existing, force=True)
        actions = {item.entity_id: item.action for item in forced.operations}
        self.assertEqual(actions["collision_cell"], "update")
        self.assertEqual(actions["Q1_rods"], "unchanged")
        self.assertTrue(all(actions[f"Q1_rods[{index}]"] == "unchanged" for index in range(4)))
        self.assertEqual(actions["Q3_rods"], "update")
        self.assertNotIn("UserSculpt", actions)


if __name__ == "__main__":
    unittest.main()
