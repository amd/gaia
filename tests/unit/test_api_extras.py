# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Packaging + wiring guards for the [api] server (issues #1617, #2176).

History: [api] used to auto-mount the gaia-agent-email REST router in-process
(openai_server.py), whose import chain reached ``import keyring``, so #1617
declared keyring in [api]. That in-process mount was the last one after the v2
thin-host migration and has now been removed (#2176): the API server reaches
every agent — email included — only through the daemon relay (/v1/<agent>/*).

So this file now asserts the thin-host arrangement instead:

* the API server no longer imports gaia_agent_email in-process (#2176);
* keyring is still guaranteed for ``pip install 'amd-gaia[api]'`` because it is
  a *core* install_requires dep (``gaia connectors`` needs it, #1621) — it does
  not need to be re-declared per-agent in [api];
* gaia-agent-email still depends on ``amd-gaia[api]`` so its sidecar gets the
  REST-server deps (fastapi/uvicorn) automatically.

These are static packaging/source assertions (no runtime import), so they work
in the CI unit-tests venv that does not actually install [api]. Same framing as
test_ui_extras.py's #845 docstring.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SETUP_PY = REPO_ROOT / "setup.py"
OPENAI_SERVER = REPO_ROOT / "src" / "gaia" / "api" / "openai_server.py"
EMAIL_PYPROJECT = REPO_ROOT / "hub" / "agents" / "python" / "email" / "pyproject.toml"


def _parse_extra(name: str) -> list[str]:
    """Extract the requirement strings from a named extras_require block.

    Walks the file line by line so brackets that appear inside ``# comments``
    don't confuse a naive non-greedy regex match.
    """
    lines = SETUP_PY.read_text(encoding="utf-8").splitlines()
    in_block = False
    body: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if not in_block:
            if re.match(rf'"{re.escape(name)}"\s*:\s*\[', stripped):
                in_block = True
            continue
        if stripped.startswith("]"):
            break
        if stripped.startswith("#"):
            continue
        body.append(raw)
    assert in_block, f'Could not find "{name}" extra in setup.py extras_require'
    return re.findall(r'"([^"]+)"', "\n".join(body))


def _parse_install_requires() -> list[str]:
    """Extract the core install_requires list from setup.py."""
    lines = SETUP_PY.read_text(encoding="utf-8").splitlines()
    in_block = False
    body: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if not in_block:
            if re.match(r"install_requires\s*=\s*\[", stripped):
                in_block = True
            continue
        if stripped.startswith("]"):
            break
        if stripped.startswith("#"):
            continue
        body.append(raw)
    assert in_block, "Could not find install_requires in setup.py"
    return re.findall(r'"([^"]+)"', "\n".join(body))


def test_api_server_does_not_mount_email_in_process() -> None:
    """The API server must not import/mount the email agent in-process (#2176).

    After the thin-host migration the only path to any agent surface is the
    daemon relay; a re-introduced ``import gaia_agent_email`` here would resurrect
    the last in-process mount and force the API server to hold sidecar deps again.
    """
    src = OPENAI_SERVER.read_text(encoding="utf-8")
    offending = re.findall(r"^\s*(?:import|from)\s+gaia_agent_email\b", src, re.M)
    assert not offending, (
        "openai_server.py must not import gaia_agent_email — the in-process "
        "email mount was removed in #2176; agents are reached via the daemon "
        f"relay (/v1/<agent>/*). Found: {offending}"
    )


def test_keyring_guaranteed_for_api_installs_via_core() -> None:
    """``pip install 'amd-gaia[api]'`` still gets keyring — from core, not [api].

    keyring moved to core install_requires with ``gaia connectors`` (#1621), so
    [api] no longer needs to re-declare it (#2176). This asserts the guarantee is
    still in place at its correct home and that [api] doesn't redundantly repeat
    it now that the email mount (its only [api]-level consumer) is gone.
    """
    core = _parse_install_requires()
    assert any(
        r.lower().startswith("keyring") for r in core
    ), f"core install_requires must declare keyring (#1621); got {core}"

    api_reqs = _parse_extra("api")
    assert not any(r.lower().startswith("keyring") for r in api_reqs), (
        "setup.py[api] should not re-declare keyring — it is a core "
        "install_requires dep (#1621) and the in-process email mount that "
        f"needed it in [api] was removed (#2176). Current [api] extra: {api_reqs}"
    )


def test_email_wheel_requires_amd_gaia_api_extra() -> None:
    """gaia-agent-email must depend on ``amd-gaia[api]`` — see #1617.

    A bare ``amd-gaia`` dependency let a consuming app's
    ``pip install gaia-agent-email`` resolve core WITHOUT the [api] extra, so the
    REST-server deps (fastapi/uvicorn) the email sidecar serves from were absent.
    Requesting the [api] extra pulls them automatically (keyring rides along via
    core install_requires, #1621).
    """
    deps = re.findall(r'"(amd-gaia[^"]*)"', EMAIL_PYPROJECT.read_text(encoding="utf-8"))
    assert deps, f"no amd-gaia dependency found in {EMAIL_PYPROJECT}"
    assert any("[api]" in d for d in deps), (
        "gaia-agent-email must depend on amd-gaia[api] so the sidecar gets the "
        f"REST-server deps automatically (got {deps})."
    )
