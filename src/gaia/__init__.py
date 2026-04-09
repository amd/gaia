# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
GAIA - Generative AI Is Awesome

AMD's framework for running generative AI applications locally on AMD hardware.
"""

# Load environment variables from .env file BEFORE any other imports
# This ensures all SDK components respect .env configuration
from dotenv import load_dotenv

load_dotenv()

# pylint: disable=wrong-import-position
from gaia.agents.base import Agent, MCPAgent, tool  # noqa: F401, E402
from gaia.database import DatabaseAgent, DatabaseMixin  # noqa: F401, E402
from gaia.hooks import BaseHook, HookRegistry  # noqa: F401, E402

# Pipeline orchestration imports
from gaia.pipeline import (  # noqa: F401, E402
    PipelineContext,
    PipelineEngine,
    PipelineState,
)
from gaia.quality import QualityReport, QualityScorer  # noqa: F401, E402
from gaia.utils import FileChangeHandler, FileWatcher, FileWatcherMixin  # noqa: F401

__all__ = [
    # Existing exports
    "Agent",
    "DatabaseAgent",
    "DatabaseMixin",
    "FileChangeHandler",
    "FileWatcher",
    "FileWatcherMixin",
    "MCPAgent",
    "tool",
    # Pipeline orchestration
    "PipelineEngine",
    "PipelineContext",
    "PipelineState",
    "QualityScorer",
    "QualityReport",
    "HookRegistry",
    "BaseHook",
]
