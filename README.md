# Alepou Blender Bridge

Alepou Blender Bridge gives AI coding sessions a durable, inspectable way to
observe and work through Blender. It exports compact exact state, accepts
atomic local requests, captures deterministic diagnostic renders, records
script evidence, and keeps the normal Blender integration on the main thread.

This repository contains two independent pieces:

- `extension/alepou_blender_bridge/` — the Blender extension/add-on;
- `src/alepou_blender/` — a thin CLI for the local file contract.

The bridge does not require Alepou to use. Alepou is the recommended control
plane for project continuity, task tracking, and AI sessions.

> **Alpha:** use a version-controlled project or disposable `.blend` while
> evaluating script execution. Arbitrary Blender Python is powerful and a
> failed script may already have mutated the scene.

## Architecture

The user binds the open Blender process to an explicit project root. The
extension writes and watches `<project>/plan/blender/` using atomic local file
operations. A persistent `bpy.app.timers` callback claims work; all `bpy`
access stays on Blender's main thread. No Python socket worker is used.

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
Disk** and choose `dist/alepou_blender_bridge-0.1.0.zip`. For Blender 4.1, use
the Add-ons install control and choose the `-legacy.zip` package. Enable
**Alepou Blender Bridge**. During source development you can instead add
`extension/` to Blender's Python path and register the package directly.

In the add-on preferences:

1. choose the Alepou project root;
2. enable processing;
3. leave the default observation mode for reads and captures, or explicitly
   choose Local Trusted Development for recorded scripts and scene mutations;
4. optionally bind trusted work to an Alepou session id.

Trusted Development remains active until revoked, the project binding changes,
or Blender closes. **STOP Bridge** remains available in Blender's 3D View
sidebar and blocks new claims immediately.

## CLI

Install the thin client into any regular Python environment:

```powershell
python -m pip install -e .
alepou-blender --project D:\path\to\project status
alepou-blender --project D:\path\to\project query scene.summary
alepou-blender --project D:\path\to\project script .\build_scene.py --session my-session
```

Every command prints one JSON envelope. `submit` accepts an existing request
file for advanced use. The CLI never imports `bpy`.

## Request envelope

```json
{
  "schemaVersion": 1,
  "commandId": "cmd-build-chair-001",
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

Diagnostic renders support named orthographic and perspective views, explicit
or object/scene bounds, fixed resolution and margin, plus clay, silhouette,
wireframe, or beauty modes. Authored and dependency-graph-evaluated bounds are
reported separately.

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
