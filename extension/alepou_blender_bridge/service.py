"""Main-thread Blender service for the Alepou local file contract."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Callable

import bpy
from bpy.app.handlers import persistent

from . import capture, protocol, state

OBSERVATION_ACTIONS = {
    "bridge.ping",
    "state.refresh",
    "scene.summary",
    "scene.list_objects",
    "object.inspect",
    "object.bounds",
    "scene.distance",
    "scene.alignment",
    "scene.intersections",
    "mesh.stats",
    "material.inspect",
    "capture.diagnostic",
    "capture.viewport",
}
MUTATION_ACTIONS = {
    "script.execute",
    "scene.save_copy",
    "scene.restore_snapshot",
    "object.select",
    "camera.ensure_standard",
}
SUPPORTED_ACTIONS = OBSERVATION_ACTIONS | MUTATION_ACTIONS


class RejectedRequest(RuntimeError):
    pass


class ActionFailed(RuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.details = details or {}


def _addon_preferences() -> Any | None:
    addon = bpy.context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


def _setting(name: str, environment_name: str, default: Any = None) -> Any:
    environment = os.environ.get(environment_name)
    if environment is not None:
        return environment
    preferences = _addon_preferences()
    return getattr(preferences, name, default) if preferences else default


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


class BridgeService:
    def __init__(self) -> None:
        self.generation = uuid.uuid4().hex
        self.started_at = protocol.utc_now()
        self.last_export_monotonic = 0.0
        self.last_export_at: str | None = None
        self.last_heartbeat_monotonic = 0.0
        self.state_revision = 0
        self.dirty = True
        self.active_command: str | None = None
        self.blocked_reason: str | None = None
        self.last_error: str | None = None
        self._known_project: Path | None = None
        self._started = False
        self.session_trust_mode = "observation"
        self.session_id: str | None = None
        self.authority_initialized = False

    def project_root(self) -> Path | None:
        configured = str(_setting("project_root", "ALEPOU_BLENDER_PROJECT_ROOT", "") or "").strip()
        if not configured:
            return None
        try:
            return protocol.canonical_project_root(configured)
        except protocol.ProtocolError as error:
            self.blocked_reason = str(error)
            return None

    def root(self) -> Path | None:
        project = self.project_root()
        return protocol.bridge_root(project) if project else None

    def processor_enabled(self) -> bool:
        return _truthy(_setting("processor_enabled", "ALEPOU_BLENDER_PROCESSOR_ENABLED", True), True)

    def trust_mode(self) -> str:
        environment = os.environ.get("ALEPOU_BLENDER_TRUST_MODE")
        value = str(environment if environment is not None else self.session_trust_mode).lower()
        return value if value in {"observation", "trusted_development"} else "observation"

    def owning_session(self) -> str | None:
        environment = os.environ.get("ALEPOU_BLENDER_SESSION_ID")
        value = str(environment if environment is not None else self.session_id or "").strip()
        return value or None

    def initialize_authority(self) -> None:
        if self.authority_initialized:
            return
        self.authority_initialized = True
        self.session_trust_mode = "observation"
        self.session_id = None
        if os.environ.get("ALEPOU_BLENDER_TRUST_MODE") is not None:
            return
        preferences = _addon_preferences()
        if preferences:
            preferences.trust_mode = "observation"
            preferences.session_id = ""

    def allow_external_save_paths(self) -> bool:
        return _truthy(_setting("allow_external_save_paths", "ALEPOU_BLENDER_ALLOW_EXTERNAL_SAVE_PATHS", False))

    def start(self) -> None:
        self._started = True
        root = self.root()
        if root:
            protocol.ensure_layout(root)
            if self._known_project != self.project_root():
                self._known_project = self.project_root()
                self.generation = uuid.uuid4().hex
                self.started_at = protocol.utc_now()
                self.state_revision = 0
                self._interrupt_processing(root)
            self.export_capabilities(root)
            self.export_state(root, reason="service_start")
            self.write_health(root)

    def stop(self, reason: str = "stopped") -> None:
        self._started = False
        self.blocked_reason = reason
        root = self.root()
        if root:
            self._reject_pending(root, reason)
            self.write_health(root, forced_active=False)

    def mark_dirty(self, _reason: str = "scene_changed") -> None:
        self.dirty = True

    def tick(self) -> float:
        try:
            root = self.root()
            if root is None:
                return 1.0
            protocol.ensure_layout(root)
            if self._known_project != self.project_root():
                self.start()
            if not self.processor_enabled():
                self.blocked_reason = "processor_stopped"
                self.write_health(root, forced_active=False)
                return 0.5
            self.blocked_reason = None
            now = time.monotonic()
            if self.dirty or now - self.last_export_monotonic >= 2.0:
                self.export_state(root, reason="dirty" if self.dirty else "periodic")
            self._process_next(root, query=True)
            self._process_next(root, query=False)
            if now - self.last_heartbeat_monotonic >= 0.45:
                self.write_health(root)
            return 0.25
        except Exception as error:  # timer callbacks must survive and expose degradation
            self.last_error = f"{type(error).__name__}: {error}"
            root = self.root()
            if root:
                self.write_health(root)
            traceback.print_exc()
            return 1.0

    def export_capabilities(self, root: Path) -> None:
        protocol.atomic_write_json(
            root / "capabilities.json",
            {
                "schemaVersion": protocol.SCHEMA_VERSION,
                "bridgeVersion": protocol.BRIDGE_VERSION,
                "transport": "local-filesystem",
                "mainThreadExecution": True,
                "actions": sorted(SUPPORTED_ACTIONS),
                "observationActions": sorted(OBSERVATION_ACTIONS),
                "mutationActions": sorted(MUTATION_ACTIONS),
                "diagnosticViews": sorted(capture.VIEW_DIRECTIONS),
                "diagnosticModes": ["beauty", "clay", "silhouette", "wireframe"],
                "limits": {"objectsSummary": 500, "jsonRequestBytes": protocol.DEFAULT_MAX_JSON_BYTES},
            },
        )

    def export_state(self, root: Path, reason: str) -> dict[str, Any]:
        self.state_revision += 1
        exported_at = protocol.utc_now()
        summary = state.scene_summary()
        summary.update({"schemaVersion": 1, "exportedAt": exported_at, "stateRevision": self.state_revision, "reason": reason})
        protocol.atomic_write_json(root / "scene-summary.json", summary)
        protocol.atomic_write_json(root / "selection.json", {**state.selection_summary(), "exportedAt": exported_at, "stateRevision": self.state_revision})
        protocol.atomic_write_json(root / "state" / "objects-summary.json", {**state.objects_summary(), "exportedAt": exported_at, "stateRevision": self.state_revision})
        protocol.atomic_write_json(root / "state" / "collections-summary.json", {**state.collections_summary(), "exportedAt": exported_at, "stateRevision": self.state_revision})
        protocol.atomic_write_json(
            root / "state" / "recent-changes.json",
            {"exportedAt": exported_at, "stateRevision": self.state_revision, "reason": reason, "note": "Dependency graph marked state dirty; this bounded vertical slice does not claim a complete object-level diff."},
        )
        diagnostics = self._diagnostics()
        protocol.atomic_write_json(root / "diagnostics.json", diagnostics)
        protocol.atomic_write_text(root / "status.md", self._status_markdown(summary, diagnostics))
        self.last_export_monotonic = time.monotonic()
        self.last_export_at = exported_at
        self.dirty = False
        return summary

    def _diagnostics(self) -> dict[str, Any]:
        missing_images = []
        for image in bpy.data.images:
            filepath = bpy.path.abspath(image.filepath) if image.filepath else ""
            if filepath and not image.packed_file and not Path(filepath).is_file():
                missing_images.append({"image": image.name_full, "path": filepath})
        return {
            "schemaVersion": 1,
            "capturedAt": protocol.utc_now(),
            "missingImages": missing_images[:200],
            "missingImagesTruncated": len(missing_images) > 200,
            "linkedLibraries": [library.filepath for library in list(bpy.data.libraries)[:200]],
            "lastBridgeError": self.last_error,
        }

    def _status_markdown(self, summary: dict[str, Any], diagnostics: dict[str, Any]) -> str:
        return "\n".join(
            [
                "# Blender Bridge Status",
                "",
                f"- Bridge: {protocol.BRIDGE_VERSION}",
                f"- Blender: {bpy.app.version_string}",
                f"- Scene: {summary['scene']}",
                f"- Blend file: {summary['blendFile'] or '(unsaved)'}",
                f"- Objects: {summary['objectCount']}",
                f"- Selected: {summary['selectedCount']}",
                f"- State revision: {self.state_revision}",
                f"- Trust mode: {self.trust_mode()}",
                f"- Missing images: {len(diagnostics['missingImages'])}",
                "",
            ]
        )

    def write_health(self, root: Path, forced_active: bool | None = None) -> None:
        active = self.processor_enabled() and self._started if forced_active is None else forced_active
        command_counts = {}
        for status in ("pending", "processing", "applied", "failed", "rejected", "interrupted"):
            command_counts[status] = len(list((root / "commands" / status).glob("*.json")))
        active_obj = bpy.context.view_layer.objects.active if bpy.context.view_layer else None
        health = {
            "schemaVersion": 1,
            "bridgeVersion": protocol.BRIDGE_VERSION,
            "blenderVersion": bpy.app.version_string,
            "processorActive": active,
            "heartbeatAt": protocol.utc_now(),
            "executorGeneration": self.generation,
            "startedAt": self.started_at,
            "projectRoot": str(self.project_root()) if self.project_root() else None,
            "bridgeRoot": str(root.resolve()),
            "blendFile": bpy.data.filepath or None,
            "unsavedBlend": not bool(bpy.data.filepath),
            "background": bool(bpy.app.background),
            "activeObjectMode": active_obj.mode if active_obj else "OBJECT",
            "rendering": bool(bpy.app.is_job_running("RENDER")) if hasattr(bpy.app, "is_job_running") else False,
            "activeCommand": self.active_command,
            "commandCounts": command_counts,
            "stateRevision": self.state_revision,
            "lastExportAt": self.last_export_at,
            "trustMode": self.trust_mode(),
            "owningSession": self.owning_session(),
            "recoverySnapshotStatus": "available" if any((root / "recovery").glob("*.blend")) else "none",
            "blockedReason": self.blocked_reason,
            "degradedReason": self.last_error,
        }
        protocol.atomic_write_json(root / "bridge-health.json", health)
        self.last_heartbeat_monotonic = time.monotonic()

    def _interrupt_processing(self, root: Path) -> None:
        processing = root / "commands" / "processing"
        for path in sorted(processing.glob("*.json")):
            command_id = path.stem
            try:
                request = protocol.read_json(path)
                command_id = protocol.validate_request_id(request.get("commandId") or command_id)
            except Exception:
                request = None
            result = {
                "schemaVersion": 1,
                "commandId": command_id,
                "status": "interrupted",
                "finishedAt": protocol.utc_now(),
                "executorGeneration": self.generation,
                "reason": "executor_restarted",
                "message": "The previous executor stopped after claiming this request. Inspect scene state before retrying with a new command id.",
                "request": request,
            }
            protocol.atomic_write_json(root / "commands" / "interrupted" / f"{command_id}.json", result)
            path.unlink(missing_ok=True)
        for path in sorted((root / "queries" / "pending").glob(".*.processing")):
            path.unlink(missing_ok=True)

    def _reject_pending(self, root: Path, reason: str) -> None:
        for query in (True, False):
            pending = root / "queries" / "pending" if query else root / "commands" / "pending"
            for path in sorted(pending.glob("*.json")):
                try:
                    request = protocol.read_json(path)
                    command_id = protocol.validate_request_id(request.get("commandId") or path.stem)
                except Exception:
                    request = None
                    command_id = path.stem if protocol.ID_PATTERN.fullmatch(path.stem) else f"invalid-{uuid.uuid4().hex[:12]}"
                result = {
                    "schemaVersion": 1,
                    "commandId": command_id,
                    "status": "rejected",
                    "finishedAt": protocol.utc_now(),
                    "executorGeneration": self.generation,
                    "reason": reason,
                    "message": "The Blender-local STOP control rejected this queued request before claim.",
                }
                destination = root / "queries" / "results" / f"{command_id}.json" if query else root / "commands" / "rejected" / f"{command_id}.json"
                if not destination.exists():
                    protocol.atomic_write_json(destination, result)
                run_root = root / "runs" / command_id
                run_root.mkdir(parents=True, exist_ok=True)
                if request is not None:
                    protocol.atomic_write_json(run_root / "request.json", request)
                protocol.atomic_write_json(run_root / "result.json", result)
                path.unlink(missing_ok=True)

    def _process_next(self, root: Path, query: bool) -> None:
        pending = root / "queries" / "pending" if query else root / "commands" / "pending"
        source = protocol.first_json_file(pending)
        if source is None:
            return
        if query:
            processing = pending / f".{source.stem}.processing"
        else:
            processing = root / "commands" / "processing" / source.name
        if not protocol.claim_file(source, processing):
            return
        self._execute_claimed(root, processing, query)

    def _execute_claimed(self, root: Path, claimed: Path, query: bool) -> None:
        started = time.monotonic()
        started_at = protocol.utc_now()
        command_id = claimed.stem
        if query and command_id.startswith("."):
            command_id = command_id[1:].removesuffix(".processing")
        request: dict[str, Any] | None = None
        request_digest: str | None = None
        result_status = "failed"
        outputs: list[dict[str, Any]] = []
        error_details: dict[str, Any] | None = None
        try:
            request = protocol.read_json(claimed)
            if not isinstance(request, dict):
                raise protocol.ProtocolError("Request root must be a JSON object")
            if int(request.get("schemaVersion", 0)) != protocol.SCHEMA_VERSION:
                raise protocol.ProtocolError(f"Unsupported schemaVersion: {request.get('schemaVersion')}")
            command_id = protocol.validate_request_id(request.get("commandId") or command_id)
            request_digest = protocol.request_hash(request)
            existing_result = protocol.find_terminal_result(root, command_id, query=query)
            if existing_result:
                duplicate_root = root / "runs" / command_id / "duplicates"
                duplicate_root.mkdir(parents=True, exist_ok=True)
                duplicate = {
                    "schemaVersion": 1,
                    "commandId": command_id,
                    "status": "rejected",
                    "finishedAt": protocol.utc_now(),
                    "reason": "duplicate_terminal_command_id",
                    "requestHash": request_digest,
                    "existingTerminalResult": str(existing_result.resolve()),
                }
                suffix = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
                protocol.atomic_write_json(duplicate_root / f"{suffix}.request.json", request)
                protocol.atomic_write_json(duplicate_root / f"{suffix}.result.json", duplicate)
                claimed.unlink(missing_ok=True)
                self.write_health(root)
                return
            actions = request.get("actions")
            if actions is None and request.get("query"):
                action = dict(request.get("arguments") or {})
                action["action"] = request["query"]
                actions = [action]
            if not isinstance(actions, list) or not actions:
                raise protocol.ProtocolError("Request must contain a non-empty actions array")
            action_names = [str(action.get("action") or "") if isinstance(action, dict) else "" for action in actions]
            unsupported = [name for name in action_names if name not in SUPPORTED_ACTIONS]
            if unsupported:
                raise protocol.ProtocolError(f"Unsupported actions: {', '.join(unsupported)}")
            if query and any(name in MUTATION_ACTIONS for name in action_names):
                raise RejectedRequest("Mutation actions must use commands/pending")
            self._authorize(request, action_names)
            self.active_command = command_id
            self.write_health(root)
            run_root = root / "runs" / command_id
            run_root.mkdir(parents=True, exist_ok=True)
            protocol.atomic_write_json(run_root / "request.json", request)
            for index, action in enumerate(actions):
                outputs.append(self._execute_action(root, run_root, command_id, index, action))
            result_status = "applied"
        except RejectedRequest as error:
            result_status = "rejected"
            error_details = {"type": type(error).__name__, "message": str(error)}
        except ActionFailed as error:
            result_status = "failed"
            error_details = {"type": type(error).__name__, "message": str(error), "details": error.details}
        except Exception as error:
            result_status = "failed"
            error_details = {"type": type(error).__name__, "message": str(error), "traceback": traceback.format_exc()}
        finally:
            self.active_command = None
        result = {
            "schemaVersion": 1,
            "commandId": command_id,
            "status": result_status,
            "requestHash": request_digest,
            "startedAt": started_at,
            "finishedAt": protocol.utc_now(),
            "elapsedSeconds": round(time.monotonic() - started, 6),
            "executorGeneration": self.generation,
            "stateRevision": self.state_revision,
            "outputs": outputs,
            "error": error_details,
        }
        run_root = root / "runs" / command_id
        run_root.mkdir(parents=True, exist_ok=True)
        protocol.atomic_write_json(run_root / "result.json", result)
        if query:
            destination = root / "queries" / "results" / f"{command_id}.json"
        else:
            destination = root / "commands" / result_status / f"{command_id}.json"
        protocol.atomic_write_json(destination, result)
        claimed.unlink(missing_ok=True)
        self.mark_dirty("request_completed")
        self.export_state(root, reason=f"request_{result_status}")
        self.write_health(root)

    def _authorize(self, request: dict[str, Any], action_names: list[str]) -> None:
        if not any(name in MUTATION_ACTIONS for name in action_names):
            return
        if self.trust_mode() != "trusted_development":
            raise RejectedRequest("Mutation requires Local Trusted Development in Blender")
        expected_session = self.owning_session()
        actual_session = str(request.get("sessionId") or "").strip() or None
        if expected_session and actual_session != expected_session:
            raise RejectedRequest("Mutation sessionId does not match the Blender-bound Alepou session")

    def _execute_action(self, root: Path, run_root: Path, command_id: str, index: int, action: dict[str, Any]) -> dict[str, Any]:
        name = str(action.get("action") or "")
        handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
            "bridge.ping": lambda _action: {"pong": True, "bridgeVersion": protocol.BRIDGE_VERSION, "blenderVersion": bpy.app.version_string},
            "state.refresh": lambda _action: self.export_state(root, reason="requested"),
            "scene.summary": lambda _action: state.scene_summary(),
            "scene.list_objects": lambda item: state.objects_summary(limit=max(1, min(int(item.get("limit") or 200), 500)), offset=max(0, int(item.get("offset") or 0))),
            "object.inspect": lambda item: state.inspect_object(item.get("object") or item.get("name")),
            "object.bounds": lambda item: state.bounds_for_object(state.find_object(item.get("object") or item.get("name"))),
            "scene.distance": lambda item: state.distance(item.get("first"), item.get("second")),
            "scene.alignment": lambda item: state.alignment(list(item.get("objects") or []), item.get("axis") or "X", float(item.get("tolerance") or 1e-4)),
            "scene.intersections": lambda item: state.intersections(item.get("objects"), max(2, min(int(item.get("limit") or 200), 500))),
            "mesh.stats": lambda item: state.mesh_stats(item.get("object") or item.get("name"), bool(item.get("evaluated", True))),
            "material.inspect": lambda item: state.material_inspect(item.get("material") or item.get("name")),
            "capture.diagnostic": lambda item: capture.diagnostic_capture(item, root / "captures", command_id),
            "capture.viewport": lambda item: capture.viewport_capture(item, root / "captures", command_id),
            "script.execute": lambda item: self._execute_script(root, run_root, command_id, index, item),
            "scene.save_copy": lambda item: self._save_copy(item),
            "scene.restore_snapshot": lambda item: self._restore_snapshot(root, item),
            "object.select": lambda item: self._select_object(item),
            "camera.ensure_standard": lambda _item: {"camera": capture.ensure_standard_camera().name_full},
        }
        value = handlers[name](action)
        return {"index": index, "action": name, "value": value}

    def _fingerprint(self) -> dict[str, Any]:
        names = sorted(obj.name_full for obj in bpy.data.objects)
        payload = json.dumps(names, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return {
            "objects": len(bpy.data.objects),
            "meshes": len(bpy.data.meshes),
            "materials": len(bpy.data.materials),
            "collections": len(bpy.data.collections),
            "objectNamesHash": hashlib.sha256(payload).hexdigest(),
            "isDirty": bool(bpy.data.is_dirty),
        }

    def _recovery_snapshot(self, root: Path, command_id: str) -> Path:
        destination = root / "recovery" / f"{command_id}-before.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(destination), copy=True, check_existing=False)
        return destination

    def _execute_script(self, root: Path, run_root: Path, command_id: str, index: int, action: dict[str, Any]) -> dict[str, Any]:
        source = action.get("source")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("script.execute requires non-empty inline source")
        source_path = run_root / f"action-{index:02d}.py"
        protocol.atomic_write_text(source_path, source)
        before = self._fingerprint()
        recovery = self._recovery_snapshot(root, command_id)
        stdout = io.StringIO()
        stderr = io.StringIO()
        failure = None
        started = time.monotonic()
        namespace = {"__name__": "__alepou_blender_script__", "__file__": str(source_path), "bpy": bpy}
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exec(compile(source, str(source_path), "exec"), namespace, namespace)
        except Exception as error:
            failure = {"type": type(error).__name__, "message": str(error), "traceback": traceback.format_exc()}
        stdout_value = stdout.getvalue()
        stderr_value = stderr.getvalue()
        protocol.atomic_write_text(run_root / f"action-{index:02d}.stdout.txt", stdout_value)
        protocol.atomic_write_text(run_root / f"action-{index:02d}.stderr.txt", stderr_value)
        after = self._fingerprint()
        details = {
            "sourcePath": str(source_path.resolve()),
            "sourceHash": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            "stdout": stdout_value[-20000:],
            "stderr": stderr_value[-20000:],
            "stdoutTruncated": len(stdout_value) > 20000,
            "stderrTruncated": len(stderr_value) > 20000,
            "before": before,
            "after": after,
            "recoverySnapshot": str(recovery.resolve()),
            "elapsedSeconds": round(time.monotonic() - started, 6),
            "failure": failure,
            "rollbackClaimed": False,
        }
        if failure:
            raise ActionFailed("Blender Python raised after possible scene mutation; inspect state before restoring or retrying", details)
        return details

    def _allowed_destination(self, value: Any) -> Path:
        if not value:
            raise ValueError("An explicit destination path is required")
        destination = Path(str(value)).expanduser().resolve()
        project = self.project_root()
        if project is None:
            raise RejectedRequest("No project is bound")
        if not self.allow_external_save_paths():
            try:
                destination.relative_to(project)
            except ValueError as error:
                raise RejectedRequest("Destination is outside the bound project") from error
        destination.parent.mkdir(parents=True, exist_ok=True)
        return destination

    def _save_copy(self, action: dict[str, Any]) -> dict[str, Any]:
        destination = self._allowed_destination(action.get("destination"))
        if destination.suffix.lower() != ".blend":
            raise ValueError("scene.save_copy destination must end in .blend")
        bpy.ops.wm.save_as_mainfile(filepath=str(destination), copy=True, check_existing=False)
        return {"path": str(destination), "bytes": destination.stat().st_size}

    def _restore_snapshot(self, root: Path, action: dict[str, Any]) -> dict[str, Any]:
        source = Path(str(action.get("source") or "")).expanduser().resolve()
        recovery_root = (root / "recovery").resolve()
        project = self.project_root()
        allowed = False
        for allowed_root in (recovery_root, project):
            if allowed_root:
                try:
                    source.relative_to(allowed_root)
                    allowed = True
                except ValueError:
                    pass
        if not allowed or not source.is_file() or source.suffix.lower() != ".blend":
            raise RejectedRequest("Restore source must be an existing .blend inside the project or recovery directory")
        bpy.ops.wm.open_mainfile(filepath=str(source))
        return {"path": str(source), "note": "Opening a snapshot changes the active .blend and executor lifecycle; inspect fresh health before further work."}

    def _select_object(self, action: dict[str, Any]) -> dict[str, Any]:
        obj = state.find_object(action.get("object") or action.get("name"))
        if bpy.context.object and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        if bool(action.get("exclusive", True)):
            for candidate in bpy.context.selected_objects:
                candidate.select_set(False)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        return {"object": obj.name_full, "exclusive": bool(action.get("exclusive", True))}


_SERVICE = BridgeService()


def get_service() -> BridgeService:
    return _SERVICE


def timer_callback() -> float:
    _SERVICE.initialize_authority()
    return _SERVICE.tick()


@persistent
def load_post_handler(_unused: Any) -> None:
    _SERVICE.generation = uuid.uuid4().hex
    _SERVICE.started_at = protocol.utc_now()
    _SERVICE.mark_dirty("file_loaded")
    _SERVICE.start()


@persistent
def depsgraph_update_handler(_scene: Any, _depsgraph: Any) -> None:
    _SERVICE.mark_dirty("dependency_graph")


def install() -> None:
    if load_post_handler not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(load_post_handler)
    if depsgraph_update_handler not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(depsgraph_update_handler)
    if not bpy.app.timers.is_registered(timer_callback):
        bpy.app.timers.register(timer_callback, first_interval=0.1, persistent=True)
    _SERVICE._started = True


def uninstall() -> None:
    _SERVICE.stop("addon_disabled")
    if bpy.app.timers.is_registered(timer_callback):
        bpy.app.timers.unregister(timer_callback)
    if load_post_handler in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(load_post_handler)
    if depsgraph_update_handler in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(depsgraph_update_handler)
