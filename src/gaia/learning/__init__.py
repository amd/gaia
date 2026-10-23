# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Nightly on-device learning: turn the agent's own history into a LoRA.

Each night, while the user is away, a learning cycle reads the agent's memory
and chat traces, finds what is worth learning (the user's preferences, new
concepts, faster ways through a workflow, corrections), builds a training set
from it, trains a LoRA adapter locally on the GPU and loads it into Lemonade
only if it beats both the base model and the adapter already in use.

Everything stays on the machine. Training runs in its own venv
(``~/.gaia/train/venv``) so GAIA never imports torch.
"""

from gaia.learning.errors import LearningError

__all__ = ["LearningError"]
