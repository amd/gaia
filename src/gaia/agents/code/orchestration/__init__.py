# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Orchestration framework for Code Agent.

This module provides LLM-driven workflow orchestration using checklist mode.
The LLM generates a checklist of template invocations, which are then
executed deterministically with error recovery.
"""

from .orchestrator import ExecutionResult, Orchestrator
from .steps.base import BaseStep, ErrorCategory, StepResult, StepStatus, UserContext

__all__ = [
    # Core
    "Orchestrator",
    "ExecutionResult",
    # Steps
    "BaseStep",
    "StepResult",
    "StepStatus",
    "ErrorCategory",
    "UserContext",
]
