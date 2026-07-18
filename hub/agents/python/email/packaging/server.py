# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Frozen-binary entrypoint for the GAIA Email Triage agent REST sidecar
(milestone #49, Phase 2 of email-agent-packaging).

This is the module PyInstaller freezes (see ``freeze.py`` /
``release_agent_email.yml``). The actual app wiring now lives in the importable
``gaia_agent_email.server`` module (single source of truth, importable from a
wheel and an editable checkout); this file is a thin re-export so:

- the freeze entry and ``--collect-submodules gaia_agent_email`` are unchanged,
- ``uvicorn server:app --app-dir <this dir>`` still works (module-level ``app``),
- ``tests/test_caller_auth.py`` (which loads this file by path and calls
  ``build_app()``) keeps passing,

and the frozen binary + a source ``uvicorn gaia_agent_email.server:app`` serve a
byte-for-byte identical ``/v1/email/*`` contract.

    GET /health   -> {"status": "ok", "service": "gaia-agent-email"}
    GET /version  -> {"apiVersion": <contract SCHEMA_VERSION>,
                      "agentVersion": <package __version__>}

Triage uses the real local Lemonade model. If Lemonade is unreachable,
``POST /v1/email/triage`` returns HTTP 502 (``local LLM triage failed``).
"""

from __future__ import annotations

import sys

from gaia_agent_email.server import (  # noqa: F401  (re-exported for the freeze/tests)
    DEFAULT_HOST,
    DEFAULT_PORT,
    app,
    build_app,
    main,
)

# Loaded as the TOP-LEVEL module `server` via `uvicorn server:app --app-dir
# <this dir>` — NOT `packaging.server:app`, which would resolve to the PyPI
# `packaging` library (this dir has no __init__.py by design). The module-level
# `app` is imported from gaia_agent_email.server, so it's the same instance the
# freeze and the source dev server serve.

if __name__ == "__main__":
    sys.exit(main())
