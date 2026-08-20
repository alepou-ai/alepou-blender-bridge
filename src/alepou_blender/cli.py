"""Command-line client for the local Blender bridge contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any


TERMINAL_STATUSES = ("applied", "failed", "rejected", "interrupted")


class ClientError(RuntimeError):
    pass


def utc_id(prefix: str) -> str:
    return f"{prefix}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"


def bridge_root(project: str | os.PathLike[str]) -> Path:
    root = Path(project).expanduser().resolve()
    if not root.is_dir():
        raise ClientError(f"Project root does not exist: {root}")
    return root / "plan" / "blender"


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as stream:
        return json.load(stream)


def result_path(root: Path, command_id: str, query: bool) -> Path | None:
    if query:
        candidate = root / "queries" / "results" / f"{command_id}.json"
        return candidate if candidate.is_file() else None
    for status in TERMINAL_STATUSES:
        candidate = root / "commands" / status / f"{command_id}.json"
        if candidate.is_file():
            return candidate
    return None


def wait_result(root: Path, command_id: str, query: bool, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        candidate = result_path(root, command_id, query)
        if candidate:
            return read_json(candidate)
        time.sleep(0.1)
    raise ClientError(f"Timed out after {timeout:g}s waiting for {command_id}")


def submit(root: Path, request: dict[str, Any], query: bool, timeout: float, wait: bool = True) -> dict[str, Any]:
    command_id = str(request.get("commandId") or "").strip()
    if not command_id:
        raise ClientError("Request requires commandId")
    pending = root / ("queries/pending" if query else "commands/pending") / f"{command_id}.json"
    if pending.exists() or result_path(root, command_id, query):
        raise ClientError(f"commandId already exists: {command_id}")
    atomic_json(pending, request)
    if not wait:
        return {"schemaVersion": 1, "commandId": command_id, "status": "pending", "path": str(pending)}
    return wait_result(root, command_id, query, timeout)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="alepou-blender", description="Thin client for Alepou Blender Bridge")
    result.add_argument("--project", required=True, help="Explicit Alepou project root")
    result.add_argument("--timeout", type=float, default=30.0, help="Seconds to wait for a terminal result")
    result.add_argument("--no-wait", action="store_true", help="Write the request and return immediately")
    subparsers = result.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="Print current bridge health")
    query = subparsers.add_parser("query", help="Submit one observation operation")
    query.add_argument("operation")
    query.add_argument("--arguments", default="{}", help="JSON object merged into the action")
    query.add_argument("--id")
    script = subparsers.add_parser("script", help="Execute a Blender Python file through Trusted Development")
    script.add_argument("path")
    script.add_argument("--session")
    script.add_argument("--id")
    raw = subparsers.add_parser("submit", help="Submit an existing request JSON file")
    raw.add_argument("path")
    raw.add_argument("--query", action="store_true")
    return result


def run(arguments: argparse.Namespace) -> dict[str, Any]:
    root = bridge_root(arguments.project)
    if arguments.command == "status":
        health = root / "bridge-health.json"
        if not health.is_file():
            raise ClientError(f"No Blender Bridge health at {health}")
        return read_json(health)
    if arguments.command == "query":
        try:
            action = json.loads(arguments.arguments)
        except json.JSONDecodeError as error:
            raise ClientError(f"Invalid --arguments JSON: {error}") from error
        if not isinstance(action, dict):
            raise ClientError("--arguments must be a JSON object")
        command_id = arguments.id or utc_id("query")
        action["action"] = arguments.operation
        request = {"schemaVersion": 1, "commandId": command_id, "actions": [action]}
        return submit(root, request, True, arguments.timeout, not arguments.no_wait)
    if arguments.command == "script":
        source_path = Path(arguments.path).expanduser().resolve()
        if not source_path.is_file():
            raise ClientError(f"Script file does not exist: {source_path}")
        source = source_path.read_text(encoding="utf-8")
        command_id = arguments.id or utc_id("script")
        request = {
            "schemaVersion": 1,
            "commandId": command_id,
            "sessionId": arguments.session,
            "title": source_path.name,
            "sourceHash": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            "actions": [{"action": "script.execute", "source": source}],
        }
        return submit(root, request, False, arguments.timeout, not arguments.no_wait)
    request = read_json(Path(arguments.path).expanduser().resolve())
    return submit(root, request, bool(arguments.query), arguments.timeout, not arguments.no_wait)


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        result = run(arguments)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result.get("status") not in {"failed", "rejected", "interrupted"} else 2
    except (ClientError, OSError, ValueError) as error:
        print(json.dumps({"status": "client-error", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
