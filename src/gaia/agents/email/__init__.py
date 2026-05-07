# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Email Triage Agent — public re-exports."""

from gaia.agents.email.agent import EmailTriageAgent
from gaia.agents.email.config import EmailAgentConfig

__all__ = ["EmailTriageAgent", "EmailAgentConfig"]
