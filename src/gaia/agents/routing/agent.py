# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""RoutingAgent - Intelligently routes requests using AgentOrchestrator."""

import json
import os
from typing import Any, Dict, List, Optional
from pathlib import Path

from gaia.agents.base.agent import Agent
from gaia.agents.registry import AgentRegistry
from gaia.agents.base.orchestrator import AgentOrchestrator
from gaia.logger import get_logger

logger = get_logger(__name__)

class RoutingAgent:
    """
    Routes user requests to appropriate agents using AgentRegistry.
    Replaces old LLM-based hardcoded CodeAgent routing.
    """

    def __init__(
        self,
        api_mode: bool = False,
        output_handler=None,
        **agent_kwargs,
    ):
        self.api_mode = api_mode
        self.output_handler = output_handler
        self.agent_kwargs = agent_kwargs

        # Initialize AgentRegistry and Orchestrator
        # Look for agents in default paths
        base_dir = Path(__file__).parent.parent.parent.parent.parent
        agents_dir = base_dir / "config" / "agents"
        
        self.registry = AgentRegistry(agents_dir=str(agents_dir) if agents_dir.exists() else None)
        import asyncio
        # We need to ensure the registry is initialized. Since __init__ is sync,
        # we can't easily await initialize() here unless we use a loop
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We can't run_until_complete if loop is running.
                loop.create_task(self.registry.initialize())
            else:
                loop.run_until_complete(self.registry.initialize())
        except RuntimeError:
            asyncio.run(self.registry.initialize())
            
        self.orchestrator = AgentOrchestrator(self.registry)

    def process_query(
        self,
        query: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        execute: bool = None,
        workspace_root: Optional[str] = None,
        **kwargs,
    ) -> Any:
        """
        Process query by routing to the most appropriate agent via Orchestrator.
        """
        if execute is None:
            execute = self.api_mode

        # Combine kwargs with our agent_kwargs
        context_params = dict(self.agent_kwargs)
        if self.output_handler:
            context_params["output_handler"] = self.output_handler
        if workspace_root:
            context_params["workspace_root"] = workspace_root
        context_params.update(kwargs)

        context = {
            "parameters": context_params,
            "phase": kwargs.get("phase", "UNKNOWN")
        }

        # Route to the best agent
        agent = self.orchestrator.route(query, context=context)

        logger.info(f"Routed query '{query}' to {agent.__class__.__name__}")

        if execute:
            # Check if the agent has a process_query method (legacy interface)
            if hasattr(agent, "process_query"):
                return agent.process_query(query, conversation_history, **kwargs)
            # Check if it has an async execute method (new BaseAgent pipeline interface)
            elif hasattr(agent, "execute"):
                import asyncio
                exec_context = {"goal": query, "phase": context["phase"], "parameters": context_params}
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # Return coroutine if loop is running
                        return agent.execute(exec_context)
                    return loop.run_until_complete(agent.execute(exec_context))
                except RuntimeError:
                    return asyncio.run(agent.execute(exec_context))
            else:
                raise AttributeError(f"Agent {agent.__class__.__name__} does not support execution.")
        
        # Return agent instance
        return agent
