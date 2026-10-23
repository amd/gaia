# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Errors raised by the learning cycle."""


class LearningError(RuntimeError):
    """A learning step failed. The message says what failed and what to do."""


class LearningStopped(LearningError):
    """The cycle stopped early because the user came back or asked it to."""
