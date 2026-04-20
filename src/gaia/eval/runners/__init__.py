# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Runners that drive agents through their CLI for eval purposes (§10.2).

Each runner encapsulates:

* how to spawn the agent as a subprocess,
* how to feed it a task,
* how to wait for completion,
* how to collect artifacts,
* how to tear it down.

Today there is exactly one runner — :class:`CoderCLIRunner` for
``gaia-coder``. Future agents (e.g. successor coder binaries) slot in
alongside by implementing the same small protocol.
"""

from gaia.eval.runners.coder_cli import AgentHandle, CoderCLIRunner, TaskResult

__all__ = ["AgentHandle", "CoderCLIRunner", "TaskResult"]
