"""Pure-Python filesystem protocol helpers for Alepou Blender Bridge."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = 1
BRIDGE_VERSION = "0.3.0"
DEFAULT_MAX_JSON_BYTES = 4 * 1024 * 1024
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

BRIDGE_DIRS = (
    "state",
    "queries/pending",
    "queries/results",
    "captures",
    "commands/pending",
    "commands/processing",
    "commands/applied",
    "commands/failed",
    "commands/rejected",
    "commands/interrupted",
    "recovery",
    "runs",
)


class ProtocolError(ValueError):
    """Raised when a request or filesystem path violates the bridge contract."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical_project_root(value: str | os.PathLike[str]) -> Path:
    if not value or not str(value).strip():
        raise ProtocolError("An explicit project root is required")
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise ProtocolError(f"Project root does not exist: {path}")
    return path


def bridge_root(project_root: str | os.PathLike[str]) -> Path:
    return canonical_project_root(project_root) / "plan" / "blender"


def instance_root(root: Path, instance_id: Any) -> Path:
    return safe_child(root / "instances", validate_instance_id(instance_id))


def ensure_layout(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for relative in BRIDGE_DIRS:
        (root / relative).mkdir(parents=True, exist_ok=True)


def ensure_project_layout(root: Path) -> None:
    ensure_layout(root)
    (root / "instances").mkdir(parents=True, exist_ok=True)


def validate_request_id(value: Any) -> str:
    request_id = str(value or "").strip()
    if not ID_PATTERN.fullmatch(request_id):
        raise ProtocolError("commandId must match [A-Za-z0-9][A-Za-z0-9._-]{0,127}")
    return request_id


def validate_instance_id(value: Any) -> str:
    instance_id = str(value or "").strip()
    if not ID_PATTERN.fullmatch(instance_id):
        raise ProtocolError("instanceId must match [A-Za-z0-9][A-Za-z0-9._-]{0,127}")
    return instance_id


def request_target_instance(value: Mapping[str, Any]) -> str | None:
    target = value.get("target")
    if target is None:
        return None
    if not isinstance(target, Mapping):
        raise ProtocolError("target must be an object")
    if "instanceId" not in target:
        raise ProtocolError("target.instanceId is required when target is present")
    return validate_instance_id(target.get("instanceId"))


def safe_child(root: Path, relative: str | os.PathLike[str]) -> Path:
    candidate = (root / Path(relative)).resolve()
    resolved_root = root.resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as error:
        raise ProtocolError(f"Path escapes allowed root: {relative}") from error
    return candidate


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def request_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: Any) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    atomic_write_bytes(path, payload)


def atomic_write_text(path: Path, value: str) -> None:
    atomic_write_bytes(path, value.encode("utf-8"))


def read_json(path: Path, max_bytes: int = DEFAULT_MAX_JSON_BYTES) -> Any:
    size = path.stat().st_size
    if size > max_bytes:
        raise ProtocolError(f"JSON file exceeds {max_bytes} bytes: {path}")
    with path.open("r", encoding="utf-8-sig") as stream:
        return json.load(stream)


def claim_file(source: Path, destination: Path) -> bool:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(source, destination)
        return True
    except FileNotFoundError:
        return False


def first_json_file(directory: Path) -> Path | None:
    try:
        return next(iter(sorted(directory.glob("*.json"), key=lambda path: path.name.lower())), None)
    except FileNotFoundError:
        return None


def terminal_command_paths(root: Path, command_id: str) -> Iterable[Path]:
    for status in ("applied", "failed", "rejected", "interrupted"):
        yield root / "commands" / status / f"{command_id}.json"


def find_terminal_result(root: Path, command_id: str, query: bool = False) -> Path | None:
    if query:
        result = root / "queries" / "results" / f"{command_id}.json"
        return result if result.is_file() else None
    return next((path for path in terminal_command_paths(root, command_id) if path.is_file()), None)


def bounded_strings(values: Iterable[Any], limit: int = 100) -> list[str]:
    return [str(value) for index, value in enumerate(values) if index < limit]
