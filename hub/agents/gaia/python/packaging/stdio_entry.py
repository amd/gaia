# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Frozen-binary entrypoint for the GAIA flagship agent's stdio transport.

A thin wrapper around :mod:`gaia_agent.stdio`, which is the single source of
truth for the wire. It exists for the same reason ``packaging/server.py`` does:
PyInstaller freezes a *file*, and the stdio transport is published as a console
script (``gaia-agent = "gaia_agent.stdio:main"`` in ``pyproject.toml``), which
has no file to point at. This module is that file.

Named ``stdio_entry`` rather than ``stdio``: PyInstaller puts the entry script's
directory on ``sys.path``, so a top-level ``stdio`` module would sit ahead of
``gaia_agent.stdio`` for anything importing the bare name.

The binary this produces is what the TUI spawns as a child process — bare argv,
one query per stdin line, canonical events back as JSON lines on stdout (see
``client.SubprocessClient`` on the Go side). It is NOT the REST sidecar; freezing
the wrong one feeds uvicorn's startup log to a JSON line scanner (#3062).
"""

from __future__ import annotations

import sys

from gaia_agent.stdio import main  # noqa: F401  (re-exported for the freeze)

if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
