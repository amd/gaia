# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Self-correction loop for gaia-coder (§7).

The public surface is :class:`SelfFixToolsMixin` (the tool set exposed to
the base agent) and :class:`FeedbackLoopDriver` (the orchestrator that
drives feedback → plan → fix → publish → verify).
"""

from gaia.coder.self_fix.loop_driver import FeedbackLoopDriver
from gaia.coder.self_fix.mixin import SelfFixToolsMixin

__all__ = [
    "FeedbackLoopDriver",
    "SelfFixToolsMixin",
]
