# Alepou Blender Bridge

Alepou Blender Bridge gives AI coding sessions a durable, inspectable way to
observe and work through Blender. It exports compact exact state, accepts
atomic local requests, captures deterministic diagnostic renders, records
script evidence, and keeps the normal Blender integration on the main thread.

This repository contains two independently usable surfaces. The Blender
extension is the complete normal-user installation; the CLI and wheel are
optional developer surfaces.

- `extension/alepou_blender_bridge/` — the Blender extension/add-on;
- `src/alepou_blender/` — a thin CLI for the local file contract.

The bridge does not require Alepou to use. Alepou is the recommended control
plane for project continuity, task tracking, and AI sessions.

The repository also contains an **experimental, disabled-by-default Spatial
vertical slice**. Spatial is a semantic authoring representation layered on the
working bridge; it does not replace raw Blender Python and is not yet a claim
of superior modelling quality.

> **Alpha:** use a version-controlled project or disposable `.blend` while
> evaluating script execution. Arbitrary Blender Python is powerful and a
> failed script may already have mutated the scene.

## Architecture

The user binds the open Blender process to an explicit project root. The
extension writes and watches `<project>/plan/blender/` using atomic local file
operations. A persistent `bpy.app.timers` callback claims work; all `bpy`
access stays on Blender's main thread. No Python socket worker is used.

Each running Blender processor has a stable process-lifetime `instanceId` and
owns an isolated workspace at
`<project>/plan/blender/instances/<instanceId>/`. Instance-targeted requests
must include `target.instanceId`; a processor never applies a request addressed
to another Blender window. One live instance also holds a short heartbeat lease
for the original `<project>/plan/blender/` queues and state files, preserving
single-instance CLI compatibility without allowing multiple processors to race
over them. Opening another `.blend` keeps the instance identity and changes the
executor generation.

Always-on state is deliberately bounded:

- `bridge-health.json` — fresh processor heartbeat and binding identity;
- `capabilities.json` — protocol and supported operations;
- `scene-summary.json`, `selection.json`, and `diagnostics.json`;
- `state/objects-summary.json` and collection/change summaries;
- `commands/` — durable mutation lifecycle and terminal results;
- `queries/` — bounded targeted queries;
- `captures/`, `recovery/`, and `runs/` — inspectable evidence.

## Install for development

Build both install formats with:

```powershell
python scripts\build_packages.py `
  --blender 'C:\Program Files\Blender Foundation\Blender 4.3\blender.exe'
```

For Blender 4.2+, open **Edit → Preferences → Get Extensions → Install from
Disk** and choose `dist/alepou_blender_bridge-0.3.2.zip`. The extension package
includes the Spatial Python runtime used inside Blender; normal Alepou users do
not install a Python wheel or run `pip`. For Blender 4.1, use the Add-ons install
control and choose the `-legacy.zip` package. That legacy package retains the
raw bridge but does not include Blender's extension-managed Spatial wheel. Enable
**Alepou Blender Bridge**. During source development you can instead add
`extension/` to Blender's Python path and register the package directly.

With Alepou running, open the **Alepou** tab in Blender's 3D View sidebar
(`N`), click **Refresh Alepou Projects**, select the exact project, and click
**Bind and Initialize**. The list uses a loopback-only, per-run authenticated
agent capability and shows the canonical root so projects with the same name
remain unambiguous. Binding publishes health and scene state immediately but
always returns authority to Observation Only. The manual project-folder field
remains available when Alepou is offline.

In the add-on preferences you can also:

1. enter a project root manually for offline use;
2. enable processing;
3. leave the default observation mode for reads and captures, or explicitly
   choose Local Trusted Development for recorded scripts and scene mutations;
4. optionally bind trusted work to an Alepou session id.

Trusted Development remains active until revoked, the project binding changes,
or Blender closes. **STOP Bridge** remains available in Blender's 3D View
sidebar and blocks new claims immediately.

## Optional developer CLI

The wheel and editable install remain useful for library development, external
automation, and CI. They are not part of the normal Blender installation flow:

```powershell
python -m pip install -e .
alepou-blender --project D:\path\to\project status
alepou-blender --project D:\path\to\project query scene.summary
alepou-blender --project D:\path\to\project script .\build_scene.py --session my-session
```

Every command prints one JSON envelope. `submit` accepts an existing request
file for advanced use. The CLI never imports `bpy`.

## Experimental Spatial authoring

Spatial Python and Spatial YAML/JSON normalize to the same backend-neutral IR.
The core records stable entity identity, frames, axes, anchors, assemblies,
linear/grid/radial repetition, simple relations, resolved state, and `why`
provenance. The Blender backend turns a resolved compile plan into one recorded
`script.execute` request; Spatial Core never imports `bpy`.

The project mode is explicit and enforced by both the CLI and Blender service:

```powershell
alepou-blender --project D:\path\to\project spatial-mode
alepou-blender --project D:\path\to\project spatial-mode opt_in
alepou-blender --project D:\path\to\project spatial-mode required
alepou-blender --project D:\path\to\project spatial-mode off
```

- `off` is the default: raw `bpy` works and Spatial authoring is rejected;
- `opt_in` allows either representation when the request selects it;
- `required` rejects raw `script.execute` authoring for truthful benchmarks;
- no mode silently falls back from Spatial to raw `bpy`.

Blender 4.2+ extension packages accept bundled `spatial.execute`,
`spatial.inspect`, and `spatial.why` actions directly. A normal Alepou session
can therefore submit Spatial Python or JSON through the existing file contract
without installing anything into the user's system Python. The extension
records the authored source, normalized IR, resolved state, compile plan, and
generated `bpy` under the command run. YAML is available when a compatible
PyYAML wheel is present; Python and JSON are the self-contained package formats.

```json
{
  "schemaVersion": 1,
  "commandId": "spatial-analyser-001",
  "representation": {
    "kind": "spatial",
    "version": "0.1",
    "fallbackAllowed": false
  },
  "actions": [
    {
      "action": "spatial.execute",
      "sourceFormat": "python",
      "compileMode": "update",
      "source": "import spatial\nscene = spatial.Scene('demo', units='mm')\nscene.box('housing', size=(400, 300, 200))"
    },
    {"action": "state.refresh"}
  ]
}
```

A compact Python authoring example:

```python
import spatial

scene = spatial.Scene("analyser", units="mm")
frame = scene.frame("analyser_frame", origin=(0, 0, 430))
beam = scene.axis("beam", frame=frame, direction=(1, 0, 0))

q1 = scene.radial_array(
    "Q1_rods",
    count=4,
    axis=beam,
    radius=55,
    start_angle=45,
    element=spatial.CylinderSpec(radius=18, length=300, axis="X"),
    frame=frame,
    center=(-290, 0, 0),
)
cell = scene.cylinder("collision_cell", radius=40, length=220, axis="X", frame=frame)
cell.after(q1, gap=60, axis="X", id="cell_after_q1")
cell.center_on(beam, id="cell_on_beam")

resolved = scene.resolve()
print(resolved.inspect("collision_cell"))
print(resolved.why("collision_cell.center.x"))
scene.write_yaml("analyser.spatial.yaml")
```

Circular quality and surface shading are explicit authoring choices rather than
hidden backend constants. Applicable primitives accept segment/ring counts, and
all geometry accepts `flat`, `smooth`, or deterministic smooth-by-angle shading:

```python
base = scene.cylinder(
    "base",
    radius=140,
    length=36,
    segments=96,
    shading=spatial.Shading.smooth_by_angle(30),
)
low_poly_knob = scene.cylinder(
    "knob",
    radius=12,
    length=18,
    segments=8,
    shading="flat",
)
```

The values are retained in normalized IR and object provenance, so a later edit
can distinguish an intentional eight-sided control from a mechanical cylinder
that must retain a round silhouette.

Reusable assets can also declare their semantic root, pivot, ground plane, and
orientation. Spatial uses the named anchor for horizontal centring and the root
assembly bounds for grounding; it does not guess the pivot from the overall
visual bounding box. That matters for articulated assets such as a desk lamp,
whose shade can extend far beyond its base:

```python
base.anchor(
    "asset_origin",
    position=(0, 0, -18),
    direction=(1, 0, 0),
    up=(0, 0, 1),
)
lamp = scene.assembly("lamp", children=(base, lower_arm, upper_arm, shade))
scene.asset(
    root=lamp,
    origin="base.asset_origin",
    center_axes=("X", "Y"),
    ground_axis="Z",
    up="Z",
    forward="X",
)
```

The resolved asset root becomes the Blender origin, the declared base anchor is
centred on X/Y, and the asset rests on Z=0. The normalized constitution is
included in resolved state, compile plans, Blender scene provenance, and Bridge
scene summaries.

Compile a serialized source through the live bridge only after opting in:

```powershell
alepou-blender --project D:\path\to\project spatial analyser.spatial.yaml --compile-mode dry_run
alepou-blender --project D:\path\to\project spatial-inspect analyser.spatial.yaml collision_cell
alepou-blender --project D:\path\to\project spatial-why analyser.spatial.yaml collision_cell.center.x
alepou-blender --project D:\path\to\project spatial analyser.spatial.yaml --compile-mode update --session my-session
```

Compilation writes the normalized source, resolved state, compile plan, and
generated Blender Python into the command's bridge run directory. Managed
objects carry `spatial.*` provenance. Updates preserve semantic object identity,
leave unrelated raw Blender objects untouched, and reject conflicting external
changes unless an inspected caller explicitly uses `--force`.

The initial relation solver is intentionally small: `after`, `before`,
`centered_on`, and `aligned_with` operate deterministically on supported shared,
axis-aligned frames. Unsupported orientations and constraint cycles fail with
repairable errors. This is not a general CAD solver, organic sculpting system,
or custom language.

## Request envelope

```json
{
  "schemaVersion": 1,
  "commandId": "cmd-build-chair-001",
  "target": {"instanceId": "blender-7f4c91a2b038"},
  "sessionId": "optional-owning-session",
  "title": "Build chair prototype",
  "intent": "Create the first inspectable blockout",
  "actions": [
    {
      "action": "script.execute",
      "source": "import bpy\nbpy.ops.mesh.primitive_cube_add()"
    },
    {
      "action": "state.refresh"
    }
  ]
}
```

Observation requests may go to `queries/pending/`; mutation requests go to
`commands/pending/`. The extension atomically claims command files into
`commands/processing/` and terminates them in `applied`, `failed`, `rejected`,
or `interrupted`. A completed command id is never silently replayed.

## Supported vertical-slice operations

Observation:

- `bridge.ping`, `state.refresh`, `scene.summary`, `scene.list_objects`;
- `object.inspect`, `object.bounds`, `mesh.stats`, `material.inspect`;
- `scene.distance`, `scene.alignment`, `scene.intersections`;
- `capture.diagnostic`, `capture.viewport`.

Trusted mutation:

- `script.execute`, `scene.save_copy`, `scene.restore_snapshot`;
- `object.select`, `camera.ensure_standard`.

Diagnostic renders offer named or caller-supplied directions, orthographic or
perspective projection, and clay, studio-lit material, silhouette, wireframe,
or scene-lit beauty modes. Framing is fitted to the requested target's actual
bounds with no metre-scale floor, so small real-scale products remain readable.
Agents may instead author their own cameras and lighting when that better exposes
the subject. Authored and dependency-graph-evaluated bounds are reported
separately.

## Validation

Pure protocol and CLI tests run with:

```powershell
python -m unittest discover -s tests -v
```

The real Blender smoke script accepts a temporary project root:

```powershell
& 'C:\Program Files\Blender Foundation\Blender 4.3\blender.exe' `
  --background --factory-startup --python scripts\blender_smoke.py -- `
  --project D:\temp\alepou-blender-smoke
```

It exercises health, atomic claims, exact state, trusted script execution,
object inspection, diagnostic rendering, and save-copy evidence.

The real Spatial compiler smoke is separate:

```powershell
& 'C:\Program Files\Blender Foundation\Blender 4.3\blender.exe' `
  --background --factory-startup --python scripts\spatial_blender_smoke.py -- `
  --output D:\temp\spatial-smoke.blend
```

It builds a semantic triple-quadrupole assembly, edits the chamber length,
re-solves both declared gaps, preserves stable managed Blender object identity,
and proves that an unrelated raw Blender object survives the update.

Targeted real-Blender regressions also cover hierarchy/material update
preservation, ordinary Bridge Spatial-ID export, geometry quality/shading, and
reusable-asset origin constitution:

```powershell
& 'C:\Program Files\Blender Foundation\Blender 4.3\blender.exe' `
  --background --factory-startup --python scripts\spatial_update_regression.py
& 'C:\Program Files\Blender Foundation\Blender 4.3\blender.exe' `
  --background --factory-startup --python scripts\spatial_state_identity_regression.py
& 'C:\Program Files\Blender Foundation\Blender 4.3\blender.exe' `
  --background --factory-startup --python scripts\spatial_geometry_quality_regression.py
& 'C:\Program Files\Blender Foundation\Blender 4.3\blender.exe' `
  --background --factory-startup --python scripts\spatial_asset_constitution_regression.py
```
