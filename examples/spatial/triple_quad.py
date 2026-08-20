"""Small Spatial Python example exercising frames, relations and radial identity."""

from __future__ import annotations

import spatial


def make_scene(chamber_length: float = 220) -> spatial.Scene:
    scene = spatial.Scene("triple_quad_demo", units="mm")
    frame = scene.frame("analyser_frame", origin=(0, 0, 430))
    beam = scene.axis("beam", frame=frame, direction=(1, 0, 0))
    rod = spatial.CylinderSpec(radius=18, length=300, axis="X", metadata={"role": "quadrupole_rod"})

    q1 = scene.radial_array(
        "Q1_rods",
        count=4,
        axis=beam,
        radius=55,
        start_angle=45,
        element=rod,
        frame=frame,
        center=(-290, 0, 0),
        metadata={"role": "quadrupole"},
    )
    chamber = scene.cylinder(
        "collision_cell",
        radius=40,
        length=chamber_length,
        axis="X",
        frame=frame,
        metadata={"role": "chamber"},
    )
    q3 = scene.radial_array(
        "Q3_rods",
        count=4,
        axis=beam,
        radius=55,
        start_angle=45,
        element=rod,
        frame=frame,
        metadata={"role": "quadrupole"},
    )
    scene.assembly("analyser", frame=frame, children=[q1, chamber, q3], metadata={"role": "analyser"})

    q1.center_on(beam, id="q1_on_beam")
    chamber.after(q1, gap=60, axis="X", id="cell_after_q1")
    chamber.center_on(beam, id="cell_on_beam")
    q3.after(chamber, gap=60, axis="X", id="q3_after_cell")
    q3.center_on(beam, id="q3_on_beam")
    return scene


if __name__ == "__main__":
    make_scene().write_yaml("triple_quad.spatial.yaml")
