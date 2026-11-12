# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Whether ``gaia api`` can serve its one model, the flagship agent, here.

Kept apart from ``agent_registry``, which reads the ``gaia api start`` flags
from the environment when it is imported.
"""

from __future__ import annotations

import importlib.util
from typing import Optional

from gaia.frozen import is_frozen

#: Why the frozen gaia can't serve ``/v1/chat/completions``.
FROZEN_API_UNAVAILABLE = (
    "`gaia api` can't run in this GAIA install. It runs the GAIA agent inside "
    "its own process, and this install runs the agent as a separate program. "
    'From a script, use `gaia -p "<prompt>"`; to chat, use the Agent UI '
    "(`gaia chat --ui`) or the GAIA terminal app. The OpenAI-compatible API "
    "needs a source install of GAIA: https://amd-gaia.ai/docs/reference/dev"
)


def api_unavailable_reason() -> Optional[str]:
    """Why this install can't serve the flagship over the API, or None.

    Only the frozen build is refused up front: there the agent can never be
    imported. A source install without it gets the install command per request.
    """
    if is_frozen() and importlib.util.find_spec("gaia_agent") is None:
        return FROZEN_API_UNAVAILABLE
    return None
