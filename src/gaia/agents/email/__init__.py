# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Email Triage Agent — public re-exports.

``EmailTriageAgent`` / ``EmailAgentConfig`` are imported lazily (PEP 562) so
that ``from gaia.agents.email.contract import ...`` — the dependency-light
request/response contract used by the REST surface (#1229) and the MCP stdio
interface (#1104) — does NOT drag the agent and its Gmail / connector backends
into the importing process.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import only for type checkers
    from gaia.agents.email.agent import EmailTriageAgent
    from gaia.agents.email.config import EmailAgentConfig

__all__ = ["EmailTriageAgent", "EmailAgentConfig"]


def __getattr__(name: str):
    # PEP 562 lazy attribute access — keeps the heavy agent import off the path
    # of dependency-light consumers (e.g. the contract module).
    if name == "EmailTriageAgent":
        from gaia.agents.email.agent import EmailTriageAgent

        return EmailTriageAgent
    if name == "EmailAgentConfig":
        from gaia.agents.email.config import EmailAgentConfig

        return EmailAgentConfig
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
