# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Regression test for issue #1617.

The [api] server (gaia.api.openai_server) auto-mounts the gaia-agent-email
REST router when that wheel is importable. Its import chain reaches
``import keyring`` at module load time (openai_server -> email_router ->
gaia.connectors.api -> gaia.connectors.store) AND at request time via
``connected_mailbox_providers()``. Because ``keyring`` was only declared in
the ``[ui]`` and ``[dev]`` extras — not in ``[api]`` — a deployment of
``pip install 'amd-gaia[api]' gaia-agent-email`` would crash on startup with
``ModuleNotFoundError: No module named 'keyring'``.

This is a packaging assertion, not a runtime import test, so it works in the
CI unit-tests venv that does not actually install ``[api]``. Same framing as
test_ui_extras.py's #845 docstring.
"""

from __future__ import annotations

import re
from pathlib import Path

SETUP_PY = Path(__file__).resolve().parents[2] / "setup.py"
EMAIL_PYPROJECT = (
    Path(__file__).resolve().parents[2]
    / "hub"
    / "agents"
    / "email"
    / "python"
    / "pyproject.toml"
)


def _parse_api_extra() -> list[str]:
    """Extract the list of requirement strings from setup.py[api].

    Walks the file line by line so brackets that appear inside ``# comments``
    don't confuse a naive non-greedy regex match.
    """
    lines = SETUP_PY.read_text().splitlines()
    in_block = False
    body: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if not in_block:
            if re.match(r'"api"\s*:\s*\[', stripped):
                in_block = True
            continue
        if stripped.startswith("]"):
            break
        # Skip comment-only lines so brackets in comments don't matter.
        if stripped.startswith("#"):
            continue
        body.append(raw)
    assert in_block, 'Could not find "api" extra in setup.py extras_require'
    return re.findall(r'"([^"]+)"', "\n".join(body))


def test_api_extra_declares_keyring() -> None:
    """setup.py[api] must declare keyring — see #1617.

    The import chain openai_server -> email_router -> gaia.connectors.api ->
    gaia.connectors.store reaches ``import keyring`` at module load AND at
    request time via ``connected_mailbox_providers()``.
    """
    api_reqs = _parse_api_extra()
    matches = [r for r in api_reqs if r.lower().startswith("keyring")]
    assert matches, (
        "setup.py[api] is missing 'keyring' (needed by "
        "openai_server -> email_router -> gaia.connectors.api -> store -> `import keyring`).\n"
        f"Current [api] extra: {api_reqs}"
    )


def test_email_wheel_requires_amd_gaia_api_extra() -> None:
    """gaia-agent-email must depend on ``amd-gaia[api]`` — see #1617.

    A bare ``amd-gaia`` dependency let a consuming app's
    ``pip install gaia-agent-email`` resolve core WITHOUT the [api] extra, so
    the REST-server deps (fastapi/uvicorn) and keyring were absent and
    ``gaia api`` crashed. Requesting the [api] extra pulls them automatically.
    """
    deps = re.findall(r'"(amd-gaia[^"]*)"', EMAIL_PYPROJECT.read_text())
    assert deps, f"no amd-gaia dependency found in {EMAIL_PYPROJECT}"
    assert any("[api]" in d for d in deps), (
        "gaia-agent-email must depend on amd-gaia[api] so consumers get the "
        f"REST-server deps + keyring automatically (got {deps})."
    )
