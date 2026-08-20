"""Bounded, authenticated discovery of projects from the local Alepou agent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen

DISCOVERY_MAX_BYTES = 64 * 1024
PROJECTS_MAX_BYTES = 512 * 1024
PROJECT_LIMIT = 250
TIMEOUT_SECONDS = 1.5

_projects: list[dict[str, Any]] = []
_enum_items: list[tuple[str, str, str]] = []


class AlepouDiscoveryError(RuntimeError):
    """A local Alepou discovery record or response was unavailable or invalid."""


def discovery_file(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".alepou" / "agent.json"


def _read_json_file(path: Path, max_bytes: int) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            body = handle.read(max_bytes + 1)
    except OSError as error:
        raise AlepouDiscoveryError(f"Alepou is not advertising a local agent at {path}") from error
    if len(body) > max_bytes:
        raise AlepouDiscoveryError(f"Alepou discovery file exceeds {max_bytes} bytes")
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AlepouDiscoveryError("Alepou discovery file is not valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise AlepouDiscoveryError("Alepou discovery file must contain a JSON object")
    return value


def _local_base_url(value: Any) -> str:
    text = str(value or "").strip()
    parsed = urlparse(text)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise AlepouDiscoveryError("Alepou discovery refused a non-loopback agent URL")
    try:
        port = parsed.port
    except ValueError as error:
        raise AlepouDiscoveryError("Alepou discovery contains an invalid agent port") from error
    if not port or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise AlepouDiscoveryError("Alepou discovery contains an invalid agent URL")
    return text.rstrip("/")


def read_agent_discovery(home: Path | None = None) -> dict[str, str]:
    record = _read_json_file(discovery_file(home), DISCOVERY_MAX_BYTES)
    token = str(record.get("integrationToken") or record.get("launchToken") or "").strip()
    if len(token) < 32 or len(token) > 256:
        raise AlepouDiscoveryError("Alepou discovery does not contain a valid integration token")
    return {"baseUrl": _local_base_url(record.get("baseUrl")), "integrationToken": token}


def _bounded_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _bounded_count(value: Any) -> int:
    try:
        return min(10_000, max(0, int(value or 0)))
    except (TypeError, ValueError):
        return 0


def _normalize_project(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    project_id = _bounded_text(value.get("projectId"), 200)
    name = _bounded_text(value.get("name"), 300)
    root = _bounded_text(value.get("path"), 2_000)
    if not project_id or not name or not root:
        return None
    instances = []
    for instance in value.get("instances") or []:
        if not isinstance(instance, dict) or len(instances) >= 64:
            continue
        instance_id = _bounded_text(instance.get("instanceId"), 128)
        if not instance_id:
            continue
        instances.append(
            {
                "instanceId": instance_id,
                "connected": instance.get("connected") is True,
                "processorState": _bounded_text(instance.get("processorState"), 40),
                "blendFile": _bounded_text(instance.get("blendFile"), 2_000),
                "owningSession": _bounded_text(instance.get("owningSession"), 200),
            }
        )
    return {
        "projectId": project_id,
        "name": name,
        "path": root,
        "projectKind": _bounded_text(value.get("projectKind"), 80) or "auto",
        "bridgePath": _bounded_text(value.get("bridgePath"), 500) or "plan/blender",
        "bindable": value.get("bindable") is not False,
        "runningSessionCount": _bounded_count(value.get("runningSessionCount")),
        "connectedInstanceCount": _bounded_count(value.get("connectedInstanceCount")),
        "instances": instances,
    }


def fetch_projects(
    *,
    home: Path | None = None,
    opener: Callable[..., Any] = urlopen,
    timeout: float = TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    discovery = read_agent_discovery(home)
    request = Request(
        f"{discovery['baseUrl']}/api/integrations/blender/projects",
        headers={"x-alepou-integration-token": discovery["integrationToken"]},
        method="GET",
    )
    try:
        with opener(request, timeout=timeout) as response:
            body = response.read(PROJECTS_MAX_BYTES + 1)
    except Exception as error:
        raise AlepouDiscoveryError(f"Could not reach the local Alepou agent: {error}") from error
    if len(body) > PROJECTS_MAX_BYTES:
        raise AlepouDiscoveryError(f"Alepou project catalogue exceeds {PROJECTS_MAX_BYTES} bytes")
    try:
        envelope = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AlepouDiscoveryError("Alepou project catalogue is not valid UTF-8 JSON") from error
    if not isinstance(envelope, dict) or envelope.get("ok") is not True:
        raise AlepouDiscoveryError("Alepou rejected the Blender project catalogue request")
    normalized = []
    for candidate in (envelope.get("projects") or [])[:PROJECT_LIMIT]:
        project = _normalize_project(candidate)
        if project:
            normalized.append(project)
    return normalized


def refresh_projects(**kwargs: Any) -> list[dict[str, Any]]:
    global _projects, _enum_items
    projects = fetch_projects(**kwargs)
    _projects = projects
    _enum_items = []
    for project in projects:
        status = []
        if project["runningSessionCount"]:
            status.append(f"{project['runningSessionCount']} Alepou session(s)")
        if project["connectedInstanceCount"]:
            status.append(f"{project['connectedInstanceCount']} Blender instance(s)")
        suffix = f" [{', '.join(status)}]" if status else ""
        _enum_items.append(
            (
                project["projectId"],
                f"{project['name']}{suffix}",
                project["path"],
            )
        )
    return list(_projects)


def enum_items(_self: Any = None, _context: Any = None) -> list[tuple[str, str, str]]:
    return _enum_items or [("__none__", "Refresh Alepou projects", "No project catalogue has been loaded")]


def project_by_id(project_id: str) -> dict[str, Any] | None:
    return next((project for project in _projects if project["projectId"] == project_id), None)


def clear_cache() -> None:
    global _projects, _enum_items
    _projects = []
    _enum_items = []
