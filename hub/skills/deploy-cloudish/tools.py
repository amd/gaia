# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tools provided by the ``deploy-cloudish`` skill.

Wraps Cloudish's HTTP API (https://cloudish.ai/skill.md). Every call goes to a
fixed cloudish.ai endpoint, so the skill declares one network scope and no tool
accepts a URL. Build contexts are read through
:mod:`gaia.skills.context`, which applies the agent's allowed-folder check, and
are packed in memory — nothing is written to disk.
"""

from __future__ import annotations

import io
import json
import tarfile
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests

from gaia.agents.base.tools import tool
from gaia.skills.context import check_read, list_readable, open_read
from gaia.skills.errors import SkillPermissionError

API_BASE = "https://cloudish.ai"
DEFAULT_TIMEOUT_S = 120

#: Folders that are never part of a useful build context.
EXCLUDED_DIRS = frozenset({"node_modules", "__pycache__", "venv", "dist", "build"})

#: Build resources Cloudish accepts, besides 0 (its own default).
BUILD_CPU_CHOICES = frozenset({0.0, 0.5, 1.0, 2.0, 3.0})
BUILD_MEMORY_CHOICES = frozenset({0, 1, 2, 4, 5})
VOLUME_CHOICES = frozenset({0, 1, 5, 20})


def _error(message: str, **extra: Any) -> dict:
    return {"error": message, **extra}


def _request(
    method: str,
    path: str,
    *,
    api_key: Optional[str] = None,
    json_body: Optional[dict] = None,
    data: Optional[dict] = None,
    files: Optional[dict] = None,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> dict:
    """Call one Cloudish endpoint and return its JSON, or an ``error`` dict."""
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        response = requests.request(
            method,
            f"{API_BASE}{path}",
            headers=headers,
            json=json_body,
            data=data,
            files=files,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return _error(
            f"Could not reach Cloudish at {API_BASE}: "
            f"{type(exc).__name__}: {exc}. Check the network and retry."
        )

    if response.status_code == 402:
        return _error(
            "Cloudish returned HTTP 402: this API key's credit balance is "
            "exhausted, so the deploy was refused. Add credits to the key or "
            "create a new one, then retry.",
            status=402,
        )

    if not response.ok:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text[:2000]
        return _error(
            f"Cloudish returned HTTP {response.status_code} for "
            f"{method} {path}: {detail}",
            status=response.status_code,
        )

    try:
        return response.json()
    except ValueError:
        return _error(
            f"Cloudish returned a non-JSON response for {method} {path}: "
            f"{response.text[:2000]}"
        )


def _owner_name(owner: str, name: str) -> str:
    return f"{quote(owner, safe='')}/{quote(name, safe='')}"


def _project_path(owner: str, name: str) -> str:
    return f"/api/v1/projects/{_owner_name(owner, name)}"


def _context_archive(context_dir: str) -> bytes:
    """Pack a source folder into the tar.gz Cloudish expects.

    ``list_readable`` applies the agent's allowed-folder check and leaves out
    hidden files (``.env``, ``.git``) and secrets; this function additionally
    skips dependency and build-output folders that only inflate the upload.
    """
    base = check_read(context_dir)
    if not (base / "Dockerfile").is_file():
        raise ValueError(
            f"{base} has no Dockerfile at its root. Cloudish builds source "
            "contexts from a Dockerfile, so add one or deploy a prebuilt image "
            "instead."
        )

    selected: List[Any] = []
    for path in list_readable(base):
        relative = path.relative_to(base)
        # ``list_readable`` already leaves out hidden files and secrets; repeat
        # the check here so the upload stays clean if that contract changes.
        if any(part.startswith(".") for part in relative.parts):
            continue
        if any(part in EXCLUDED_DIRS for part in relative.parts[:-1]):
            continue
        selected.append((relative, path))

    if not selected:
        raise ValueError(
            f"{base} contains no readable files to deploy. Check that the "
            "folder is inside the agent's allowed folders and is not empty."
        )

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for relative, path in selected:
            info = archive.gettarinfo(str(path), arcname=str(relative))
            with open_read(path, "rb") as handle:
                archive.addfile(info, handle)
    return buffer.getvalue()


def _runtime_fields(
    always_on: bool,
    sleep_after_minutes: int,
    cpu_cores: float,
    memory_gb: float,
    volume_size_gb: int,
    build_cpu_cores: float,
    build_memory_gb: int,
) -> Dict[str, Any]:
    """Validate and collect the optional deployment fields."""
    fields: Dict[str, Any] = {}
    if always_on:
        fields["alwaysOn"] = True
    elif sleep_after_minutes:
        if not 1 <= sleep_after_minutes <= 1440:
            raise ValueError(
                "sleep_after_minutes must be between 1 and 1440, or 0 to use "
                f"the platform default; got {sleep_after_minutes}."
            )
        fields["sleepAfterMinutes"] = sleep_after_minutes

    if cpu_cores:
        if cpu_cores <= 0:
            raise ValueError(f"cpu_cores must be positive or 0; got {cpu_cores}.")
        fields["cpuCores"] = cpu_cores
    if memory_gb:
        if memory_gb <= 0:
            raise ValueError(f"memory_gb must be positive or 0; got {memory_gb}.")
        fields["memoryGb"] = memory_gb

    if volume_size_gb not in VOLUME_CHOICES:
        raise ValueError(
            "volume_size_gb must be 0 (no volume), 1, 5, or 20; "
            f"got {volume_size_gb}."
        )
    if volume_size_gb:
        fields["volumeEnabled"] = True
        fields["volumeSizeGb"] = volume_size_gb

    if build_cpu_cores not in BUILD_CPU_CHOICES:
        raise ValueError(
            "build_cpu_cores must be 0 (platform default), 0.5, 1, 2, or 3; "
            f"got {build_cpu_cores}."
        )
    if build_memory_gb not in BUILD_MEMORY_CHOICES:
        raise ValueError(
            "build_memory_gb must be 0 (platform default), 1, 2, 4, or 5; "
            f"got {build_memory_gb}."
        )
    if build_cpu_cores:
        fields["buildCpuCores"] = build_cpu_cores
    if build_memory_gb:
        fields["buildMemoryGb"] = build_memory_gb
    return fields


@tool
def cloudish_create_key(alias: str = "", agent: str = "gaia") -> dict:
    """Create a Cloudish API key without any existing credential.

    Args:
        alias: Short name for this work, e.g. the app name. Empty lets
            Cloudish choose one. The returned ``apiKey.alias`` is the owner
            in every project path.
        agent: The product making the call, for Cloudish's own tracking.

    Returns:
        ``{"apiKey": {"alias", "balance", ...}, "key", "claimUrl"}`` on
        success, or ``{"error"}``. ``key`` authenticates every other tool;
        ``claimUrl`` is for the user to open.
    """
    body: Dict[str, Any] = {}
    if alias:
        body["alias"] = alias
    if agent:
        body["agent"] = agent
    return _request("POST", "/api/v1/keys", json_body=body)


@tool
def cloudish_deploy(
    api_key: str,
    name: str,
    image: str = "",
    context_dir: str = "",
    port: int = 8080,
    env: Optional[Dict[str, str]] = None,
    always_on: bool = False,
    sleep_after_minutes: int = 0,
    cpu_cores: float = 0.0,
    memory_gb: float = 0.0,
    volume_size_gb: int = 0,
    build_cpu_cores: float = 0.0,
    build_memory_gb: int = 0,
) -> dict:
    """Create or update a Cloudish project and deploy it.

    Passing an existing ``name`` updates that project instead of creating a
    second one, so the same call works for a first deploy and a redeploy.

    Args:
        api_key: Cloudish API key from ``cloudish_create_key``.
        name: Project name. The URL and project path are derived from it.
        image: Prebuilt container image, e.g. ``ghcr.io/acme/app:1.0.0``.
        context_dir: Folder to build, containing a Dockerfile at its root.
            Pass exactly one of ``image`` and ``context_dir``.
        port: Port the process listens on, not the Dockerfile's EXPOSE.
        env: Non-secret environment variables. Credentials belong in
            ``cloudish_set_secrets``, never here or in the build context.
        always_on: Keep the app running and billed continuously.
        sleep_after_minutes: Sleep after this many idle minutes (1-1440).
            0 leaves the platform default of 15. Ignored when always_on.
        cpu_cores: Container CPU size. 0 leaves Cloudish's ``shared`` default.
        memory_gb: Container memory in GB. 0 leaves the ``shared`` default.
        volume_size_gb: Attach a persistent volume of 1, 5, or 20 GB at
            ``/data``. 0 deploys without one.
        build_cpu_cores: Build CPU cores (0.5, 1, 2, or 3). 0 uses the default.
        build_memory_gb: Build memory in GB (1, 2, 4, or 5). 0 uses the default.

    Returns:
        The Cloudish response: ``{"project", "app", "subdomain"}`` for an
        image deploy, ``{"project", "build"}`` for a source deploy, or
        ``{"error"}``. A source deploy is not live until the build succeeds.
    """
    if bool(image) == bool(context_dir):
        return _error(
            "Pass exactly one of image (a prebuilt container image) and "
            "context_dir (a folder with a Dockerfile)."
        )
    if not 1 <= port <= 65535:
        return _error(f"port must be between 1 and 65535; got {port}.")

    try:
        fields = _runtime_fields(
            always_on,
            sleep_after_minutes,
            cpu_cores,
            memory_gb,
            volume_size_gb,
            build_cpu_cores,
            build_memory_gb,
        )
    except ValueError as exc:
        return _error(str(exc))

    if env is not None and not isinstance(env, dict):
        return _error("env must be an object mapping names to string values.")

    if image:
        body: Dict[str, Any] = {"name": name, "image": image, "port": port}
        if env:
            body["env"] = env
        body.update(fields)
        return _request("POST", "/api/v1/projects", api_key=api_key, json_body=body)

    try:
        archive = _context_archive(context_dir)
    except (ValueError, OSError, SkillPermissionError) as exc:
        return _error(str(exc))

    form: Dict[str, Any] = {"name": name, "port": str(port)}
    if env:
        form["env"] = json.dumps(env)
    for key, value in fields.items():
        form[key] = str(value).lower() if isinstance(value, bool) else str(value)
    files = {"context": ("context.tar.gz", archive, "application/gzip")}
    return _request(
        "POST", "/api/v1/projects", api_key=api_key, data=form, files=files
    )


@tool
def cloudish_build_status(api_key: str, build_id: int) -> dict:
    """Check a source build started by ``cloudish_deploy``.

    Args:
        api_key: Cloudish API key.
        build_id: The ``build.id`` returned by ``cloudish_deploy``.

    Returns:
        ``{"build": {"status", "logs", "error"}, "image"}``, or ``{"error"}``.
        Poll only while ``status`` is ``"running"``: every other value, and a
        response without a status, is terminal.
    """
    return _request("GET", f"/api/v1/images/builds/{build_id}", api_key=api_key)


@tool
def cloudish_status(api_key: str, owner: str, name: str) -> dict:
    """Read a Cloudish project's state, URL, resources, and credit rate.

    Args:
        api_key: Cloudish API key.
        owner: The key's alias, i.e. the first part of ``project.path``.
        name: The project name.

    Returns:
        The project record, including ``subdomain.url``, ``app.status``,
        ``app.resources``, ``creditsPerHour``, and ``runway``, or ``{"error"}``.
    """
    return _request("GET", _project_path(owner, name), api_key=api_key)


@tool
def cloudish_logs(api_key: str, owner: str, name: str) -> dict:
    """Read an app's current container logs and Kubernetes events.

    Args:
        api_key: Cloudish API key.
        owner: The key's alias, i.e. the first part of ``project.path``.
        name: The project name.

    Returns:
        ``{"logs": "..."}``, or ``{"error"}``. Logs are fetched live and are
        not stored by Cloudish, so read them before redeploying.
    """
    return _request(
        "GET", f"/api/v1/docker/{_owner_name(owner, name)}/logs", api_key=api_key
    )


@tool
def cloudish_configure(
    api_key: str,
    owner: str,
    name: str,
    always_on: Optional[bool] = None,
    sleep_after_minutes: Optional[int] = None,
    wake_every_minutes: Optional[int] = None,
) -> dict:
    """Change when an app runs without rebuilding it.

    Leave a field as null to keep the app's current value.

    Args:
        api_key: Cloudish API key.
        owner: The key's alias, i.e. the first part of ``project.path``.
        name: The project name.
        always_on: ``true`` to run continuously, ``false`` to allow sleeping.
        sleep_after_minutes: Sleep after this many idle minutes (1-1440).
        wake_every_minutes: Wake on a schedule (5-10080 minutes), applied
            after a sleep setting.

    Returns:
        The updated project record, or ``{"error"}``.
    """
    body: Dict[str, Any] = {}
    if always_on is not None:
        body["alwaysOn"] = bool(always_on)
    if sleep_after_minutes is not None:
        if not 1 <= sleep_after_minutes <= 1440:
            return _error(
                f"sleep_after_minutes must be between 1 and 1440; got "
                f"{sleep_after_minutes}."
            )
        body["sleepAfterMinutes"] = sleep_after_minutes
    if wake_every_minutes is not None:
        if not 5 <= wake_every_minutes <= 10080:
            return _error(
                f"wake_every_minutes must be between 5 and 10080; got "
                f"{wake_every_minutes}."
            )
        body["wakeEveryMinutes"] = wake_every_minutes
    if not body:
        return _error(
            "Nothing to change: pass at least one of always_on, "
            "sleep_after_minutes, or wake_every_minutes."
        )
    return _request(
        "PATCH", _project_path(owner, name), api_key=api_key, json_body=body
    )


@tool
def cloudish_set_secrets(
    api_key: str, owner: str, name: str, secrets: Dict[str, str]
) -> dict:
    """Set encrypted environment secrets on a Cloudish project.

    Saving secrets restarts a running app with the new values. Values take
    effect as environment variables; never put them in ``env`` or the build
    context instead.

    Args:
        api_key: Cloudish API key.
        owner: The key's alias, i.e. the first part of ``project.path``.
        name: The project name.
        secrets: Names mapped to their secret values, e.g.
            ``{"OPENAI_API_KEY": "sk-..."}``.

    Returns:
        The Cloudish response, or ``{"error"}``. Reading secrets returns
        names only, never values.
    """
    if not secrets:
        return _error("secrets must contain at least one name and value.")
    return _request(
        "PUT",
        f"{_project_path(owner, name)}/secrets",
        api_key=api_key,
        json_body=dict(secrets),
    )
