# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Hand-rolled pure-Python wheel builder for hub-installer tests (#2358).

Builds a minimal, spec-valid ``.whl`` in memory -- no ``build``/``wheel``
package required -- so tests can run a REAL ``pip install --target`` against
a real artifact instead of mocking pip. The wheel declares one ``gaia.agent``
entry point so it is discoverable by ``importlib.metadata.entry_points()``
once its ``site-packages`` install dir lands on ``sys.path``, exactly what
``gaia.hub.installer.install()`` does for a Python-agent artifact.
"""

import base64
import hashlib
import zipfile
from io import BytesIO
from typing import Dict, Optional, Tuple


def _b64_nopad(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _normalize_dist_name(dist_name: str) -> str:
    """Normalize a distribution name for use in a wheel FILENAME / dist-info
    dir, per the wheel spec (PEP 427): hyphens are the segment separator in
    ``{distribution}-{version}-{python tag}-{abi tag}-{platform tag}.whl``,
    so any hyphen already IN the distribution name must become an
    underscore, or pip rejects the filename as "wrong number of parts". The
    human-readable ``Name:`` field in METADATA keeps the original hyphenated
    form -- only the filename/dist-info-dir segment is normalized.
    """
    return dist_name.replace("-", "_")


def build_fixture_wheel_bytes(
    *,
    dist_name: str,
    version: str,
    module_name: str,
    entry_point_group: str,
    entry_point_name: str,
    entry_point_target: str,
    module_source: str,
    extra_modules: Optional[Dict[str, str]] = None,
) -> bytes:
    """Build a minimal, real, installable wheel and return its raw bytes.

    ``extra_modules`` maps extra relative-path -> source for additional files
    inside ``module_name`` (e.g. ``{"app.py": "..."}``), for fixtures that
    need more than one module (e.g. mimicking ``gaia_agent_chat.agent`` +
    ``gaia_agent_chat.app`` for a hardcoded-import call site).
    """
    dist_info = f"{_normalize_dist_name(dist_name)}-{version}.dist-info"
    files: Dict[str, str] = {
        f"{module_name}/__init__.py": "",
        f"{module_name}/agent.py": module_source,
    }
    for rel_path, source in (extra_modules or {}).items():
        files[f"{module_name}/{rel_path}"] = source
    files.update(
        {
            f"{dist_info}/METADATA": (
                "Metadata-Version: 2.1\n"
                f"Name: {dist_name}\n"
                f"Version: {version}\n"
                "Summary: fixture wheel for gaia hub install tests\n"
            ),
            f"{dist_info}/WHEEL": (
                "Wheel-Version: 1.0\n"
                "Generator: gaia-test-fixture\n"
                "Root-Is-Purelib: true\n"
                "Tag: py3-none-any\n"
            ),
            f"{dist_info}/entry_points.txt": (
                f"[{entry_point_group}]\n{entry_point_name} = {entry_point_target}\n"
            ),
        }
    )
    buf = BytesIO()
    record_lines = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            data = content.encode("utf-8")
            zf.writestr(name, data)
            digest = hashlib.sha256(data).digest()
            record_lines.append(f"{name},sha256={_b64_nopad(digest)},{len(data)}")
        record_lines.append(f"{dist_info}/RECORD,,")
        zf.writestr(f"{dist_info}/RECORD", "\n".join(record_lines) + "\n")
    return buf.getvalue()


_DEFAULT_AGENT_MODULE_SOURCE = '''\
"""Fixture agent module -- proves a hub-installed wheel is real & importable."""

from gaia.agents.registry import AgentRegistration


def _factory(**kwargs):
    return object()


def build_registration():
    return AgentRegistration(
        id={agent_id!r},
        name="Fixture Test Agent",
        description="Fixture agent for hub-install cross-process import tests",
        source="installed",
        conversation_starters=[],
        factory=_factory,
        agent_dir=None,
        models=[],
    )
'''


def build_chat_shaped_fixture_wheel(
    agent_id: str = "fixturetestagent", version: str = "0.1.0"
) -> bytes:
    """A wheel shaped like a hub Python-agent package, registering *agent_id*.

    Doesn't need to be the literal ``gaia_agent_chat`` package -- the point is
    proving the wheel-install -> cross-process-import *mechanism* (#2358),
    the same mechanism a real ``gaia-agent-chat`` install would exercise.
    """
    module_name = f"{agent_id}_pkg"
    module_source = _DEFAULT_AGENT_MODULE_SOURCE.format(agent_id=agent_id)
    return build_fixture_wheel_bytes(
        dist_name=agent_id,
        version=version,
        module_name=module_name,
        entry_point_group="gaia.agent",
        entry_point_name=agent_id,
        entry_point_target=f"{module_name}.agent:build_registration",
        module_source=module_source,
    )


def build_wheel_manifest(
    agent_id: str,
    version: str,
    wheel_bytes: bytes,
    *,
    dist_name: Optional[str] = None,
) -> Tuple[Dict, str]:
    """Return ``(manifest, artifact_path)`` for :func:`gaia.hub.installer.install`.

    Mirrors the manifest shape used by ``tests/unit/test_hub_installer.py``'s
    ``_manifest()`` helper (id, language, latest_version, requirements,
    versions[v].artifact{filename,path,size_bytes,sha256,content_type}).

    ``dist_name`` (defaults to *agent_id*) names the wheel FILE itself --
    per wheel spec the filename's distribution segment must match the
    wheel's own ``.dist-info`` dir name, which is independent of the hub
    *agent_id* (the install-directory name). Pass it explicitly when the
    wheel's ``dist_name`` (as built by :func:`build_fixture_wheel_bytes`)
    differs from *agent_id* -- e.g. a fixture installed under the hub id
    ``chat`` whose wheel is distributed as ``gaia-agent-chat``.
    """
    dist_name = dist_name or agent_id
    sha = hashlib.sha256(wheel_bytes).hexdigest()
    filename = f"{_normalize_dist_name(dist_name)}-{version}-py3-none-any.whl"
    path = f"agents/{agent_id}/{version}/{filename}"
    manifest = {
        "id": agent_id,
        "language": "python",
        "latest_version": version,
        "requirements": {"platforms": []},
        "versions": {
            version: {
                "version": version,
                "artifact": {
                    "filename": filename,
                    "path": path,
                    "size_bytes": len(wheel_bytes),
                    "sha256": sha,
                    "content_type": "application/octet-stream",
                },
            }
        },
    }
    return manifest, path


def build_wheel_fetcher(
    base_url: str, artifact_path: str, wheel_bytes: bytes, agent_id: str
):
    """Fetcher callable serving *wheel_bytes* + a stub ``gaia-agent.yaml``."""

    def fetcher(url: str) -> bytes:
        if url.endswith("/gaia-agent.yaml"):
            return f"id: {agent_id}\nname: Fixture\n".encode("utf-8")
        if url == f"{base_url}/{artifact_path}":
            return wheel_bytes
        raise AssertionError(f"unexpected fetch url: {url}")

    return fetcher
