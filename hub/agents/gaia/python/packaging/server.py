# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Frozen-binary / dev-mode entrypoint for the GAIA flagship agent.

A thin re-export of :mod:`gaia_agent.server`, which is the single source of
truth for the app wiring AND for the transport dispatch: running this module
(so, running the frozen binary) serves HTTP on ``--serve``/``--host``/``--port``
and the stdio JSONL wire on anything else. Kept as a separate top-level module
because two launchers reach the sidecar by *module path* rather than by import:

- the daemon's dev mode runs ``uvicorn server:app --app-dir <this dir>``
  (``AgentSidecarSpec.dev_app_dir`` / ``dev_module``),
- PyInstaller freezes this file as the binary entry.

Both must therefore resolve a module-level ``app``. Because the frozen binary
and a source ``uvicorn gaia_agent.server:app`` share that one app object,
they serve a byte-for-byte identical ``/v1/gaia/*`` contract.

    GET /health   -> {"status": "ok", "service": "gaia-agent-gaia"}
    GET /version  -> {"apiVersion": <wire contract>, "agentVersion": <package>}

A frozen build has no Python interpreter, so ``run_python`` re-launches this
binary with ``--gaia-run-python-file <script>``. That argv runs the
script and exits before the agent stack is imported or argv is parsed. So do
the ``python`` forms a script uses to start ``sys.executable`` itself (``-m``,
``-c``, a file); ``-m pip`` explains that nothing can be installed. A
script's ``multiprocessing`` workers start the same way and are handed off by
``freeze_support``; on Windows they cannot import functions defined in the
script itself, so such a pool fails rather than runs.
"""

from __future__ import annotations

import multiprocessing
import sys

from gaia_agent_chat.python_runner import dispatch as python_dispatch

if __name__ == "__main__":
    # A script's multiprocessing workers re-launch this binary too.
    multiprocessing.freeze_support()
    _python_exit = python_dispatch(sys.argv[1:])
    if _python_exit is not None:
        sys.exit(_python_exit)

from gaia_agent.server import (  # noqa: E402,F401  (re-exported for the freeze/daemon)
    DEFAULT_HOST,
    DEFAULT_PORT,
    app,
    build_app,
    main,
)

if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
