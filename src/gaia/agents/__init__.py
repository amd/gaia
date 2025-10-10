# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

from gaia.logger import get_logger

logger = get_logger(__name__)

# Optional imports for other agents
try:
    from gaia.apps.llm.app import LlmApp as llm
except ImportError:
    logger.debug("llm app not available")
    llm = None

try:
    from gaia.chat.app import ChatApp as chat
except ImportError:
    logger.debug("Chat app not available")
    chat = None
