# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA Routing agent — standalone hub package.

``RoutingAgent`` is GAIA infrastructure: a meta-agent that analyzes a request
and routes it to the right concrete agent (currently CodeAgent). It is NOT a
GAIA registry agent — it does not inherit the base ``Agent`` and is loaded by
class path from the OpenAI-compatible API server
(``gaia.api.agent_registry``). It therefore ships *without* a ``gaia.agent``
entry point; installing this wheel simply makes ``gaia_agent_routing`` importable.
"""

from .agent import RoutingAgent

__all__ = ["RoutingAgent"]

__version__ = "0.1.0"
