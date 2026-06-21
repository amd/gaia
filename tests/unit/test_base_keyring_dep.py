# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Regression test for issue #1621.

``gaia connectors`` (list, status, …) is a BASE CLI command — registered in
``gaia.cli:main`` unconditionally, without any extras gate.  Its import chain
reaches ``gaia.connectors.store`` which does ``import keyring`` at module load
time and at request time (OAuth token storage via ``gaia.connectors.api``
introduced in #915).

Before this fix, ``keyring`` was only in the ``[ui]``, ``[api]``, and ``[dev]``
extras.  A bare ``pip install amd-gaia`` therefore caused ``gaia connectors
list`` to crash with ``ModuleNotFoundError: No module named 'keyring'``.

Fix: promote ``keyring`` to ``install_requires`` so the base wheel ships a
working ``gaia connectors`` out of the box.  The extras keep their own
declarations independently (``test_api_extras.py`` guards the [api] entry).

This is a static packaging assertion — it works in the CI unit-test venv that
does not install the package at all.  Modelled on ``test_api_extras.py`` (#1617).
"""

from __future__ import annotations

import re
from pathlib import Path

SETUP_PY = Path(__file__).resolve().parents[2] / "setup.py"


def _parse_install_requires() -> list[str]:
    """Extract requirement strings from setup.py install_requires=[...].

    Walks the file line by line so brackets inside ``# comments`` don't
    confuse a naive non-greedy regex.  Skips comment-only lines.  Stops
    at the first ``]`` that closes the block.
    """
    lines = SETUP_PY.read_text().splitlines()
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
        # Skip comment-only lines so brackets in comments don't matter.
        if stripped.startswith("#"):
            continue
        body.append(raw)
    assert in_block, "Could not find install_requires=[ in setup.py"
    return re.findall(r'"([^"]+)"', "\n".join(body))


def test_base_install_requires_declares_keyring() -> None:
    """setup.py install_requires must include keyring — see #1621.

    ``gaia connectors`` is a base CLI command (no extras gate).  Its import
    chain reaches ``gaia.connectors.store -> import keyring`` (OAuth token
    storage introduced in #915).  Without keyring in base, a plain
    ``pip install amd-gaia`` ships a broken ``gaia connectors`` command.
    """
    base_reqs = _parse_install_requires()
    matches = [r for r in base_reqs if r.lower().startswith("keyring")]
    assert matches, (
        "setup.py install_requires is missing 'keyring' (needed by "
        "gaia.connectors.store -> `import keyring` for OAuth token storage, #915).\n"
        "gaia connectors is a base CLI command — keyring must ship with the base wheel.\n"
        f"Current install_requires: {base_reqs}"
    )
