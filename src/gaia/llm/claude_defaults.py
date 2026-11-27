# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The default Claude model, kept apart from the provider so importing it is free.

``providers/claude.py`` guards an optional dependency; agents that only run on
Lemonade read this constant without paying for that import.
"""

DEFAULT_CLAUDE_MODEL = "claude-sonnet-5"
