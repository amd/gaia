# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Agent Registry - Exposes GAIA agents as OpenAI "models"

GAIA doesn't manage LLM models (Lemonade does that). Instead, we expose
GAIA agents as "models" in the OpenAI API, allowing users to select which
agent type they want to use.

Example:
    User selects "gaia-code" model -> Routes to CodeAgent
    User selects "gaia-jira" model -> Routes to JiraAgent

This is a simple hardcoded mapping for users to select agent types.
"""

import logging
import os
import time
from typing import Any, Dict, List

from gaia.agents.base.agent import Agent
from gaia.agents.base.api_agent import ApiAgent
from gaia.api.sse_handler import SSEOutputHandler

logger = logging.getLogger(__name__)


# Hardcoded agent mappings: "model" name -> (Agent class, init params)
# These are the "models" exposed in /v1/models and selectable in VSCode
AGENT_MODELS = {
    "gaia-code": {
        "class_name": "gaia.agents.routing.agent.RoutingAgent",
        "init_params": {
            "api_mode": True,  # Skip interactive questions, use defaults/best-guess
            "silent_mode": True,
            "streaming": False,
            "max_steps": 100,
        },
        "description": "Intelligent routing agent that detects language/project type and routes to CodeAgent",
    }
}


# Apply environment variable overrides to all agent init_params
# These are set by app.py when starting the API server with debug flags
def _apply_env_overrides():
    """
    Read environment variables set by `gaia api start` and override agent init_params.

    Environment variables:
        GAIA_API_DEBUG: Enable debug logging and console output
        GAIA_API_SHOW_PROMPTS: Display prompts sent to LLM
        GAIA_API_STREAMING: Enable real-time streaming of LLM responses
        GAIA_API_STEP_THROUGH: Enable step-through debugging mode
    """
    debug = os.environ.get("GAIA_API_DEBUG") == "1"
    show_prompts = os.environ.get("GAIA_API_SHOW_PROMPTS") == "1"
    streaming = os.environ.get("GAIA_API_STREAMING") == "1"

    # Apply overrides to all agents
    for model_id, config in AGENT_MODELS.items():
        init_params = config["init_params"]

        # When debug is enabled, disable silent_mode to show console output
        if debug:
            init_params["debug"] = True
            init_params["silent_mode"] = False
            logger.info(f"Debug mode enabled for {model_id}")

        if show_prompts:
            init_params["show_prompts"] = True
            logger.info(f"Show prompts enabled for {model_id}")

        if streaming:
            init_params["streaming"] = True
            logger.info(f"Streaming enabled for {model_id}")


# Apply environment overrides at module import time
_apply_env_overrides()


class AgentRegistry:
    """
    Registry that exposes GAIA agents as OpenAI-compatible "models".

    Note: These aren't LLM models - they're GAIA agent types.
    Lemonade handles the actual LLM models underneath.
    """

    def __init__(self):
        """Initialize registry with hardcoded agents"""
        self._loaded_classes: Dict[str, type] = {}

    def _load_agent_class(self, class_path: str) -> type:
        """
        Dynamically load agent class from module path.

        Args:
            class_path: Full module path (e.g., "gaia.agents.code.agent.CodeAgent")

        Returns:
            Agent class
        """
        if class_path in self._loaded_classes:
            return self._loaded_classes[class_path]

        module_path, class_name = class_path.rsplit(".", 1)
        module = __import__(module_path, fromlist=[class_name])
        cls = getattr(module, class_name)

        self._loaded_classes[class_path] = cls
        return cls

    def get_agent(self, model_id: str) -> Agent:
        """
        Instantiate and return agent for model ID with SSE output handler.

        Args:
            model_id: Model ID (e.g., "gaia-code", "gaia-jira")

        Returns:
            Agent instance configured for API streaming

        Raises:
            ValueError: If model_id not found

        Example:
            >>> registry = AgentRegistry()
            >>> agent = registry.get_agent("gaia-code")
            >>> result = agent.process_query("Write hello world")
        """
        if model_id not in AGENT_MODELS:
            available = ", ".join(AGENT_MODELS.keys())
            raise ValueError(
                f"Model '{model_id}' not found. " f"Available models: {available}"
            )

        config = AGENT_MODELS[model_id]

        try:
            agent_class = self._load_agent_class(config["class_name"])
            init_params = config["init_params"].copy()

            # Check if debug mode is enabled
            debug_mode = os.environ.get("GAIA_API_DEBUG") == "1"

            # API layer always uses SSEOutputHandler for streaming to clients
            # Pass debug_mode flag to control verbosity
            init_params["output_handler"] = SSEOutputHandler(debug_mode=debug_mode)

            if debug_mode:
                logger.debug(f"Creating agent {model_id} with debug mode enabled")

            return agent_class(**init_params)
        except ImportError as e:
            logger.error(f"Failed to load agent {model_id}: {e}")
            raise ValueError(f"Agent {model_id} not available: {e}")

    def list_models(self) -> List[Dict[str, Any]]:
        """
        Return OpenAI-compatible model list.

        Note: These are GAIA agents exposed as "models", not LLM models.

        Returns:
            List of model metadata dicts for /v1/models endpoint

        Example:
            >>> registry = AgentRegistry()
            >>> models = registry.list_models()
            >>> [m["id"] for m in models]
            ['gaia-code', 'gaia-jira']
        """
        models = []

        for model_id, config in AGENT_MODELS.items():
            try:
                # Try to load agent to get metadata (if it implements ApiAgent)
                agent_class = self._load_agent_class(config["class_name"])
                agent = agent_class(**config["init_params"])

                # Get model info (custom if ApiAgent, default otherwise)
                if isinstance(agent, ApiAgent):
                    model_info = agent.get_model_info()
                    logger.debug(
                        f"Agent {model_id} provides custom model info: {model_info}"
                    )
                else:
                    model_info = {
                        "max_input_tokens": 8192,
                        "max_output_tokens": 4096,
                    }
                    logger.debug(f"Agent {model_id} using default model info")
            except Exception as e:
                # Agent not available or initialization failed, use defaults
                logger.warning(f"Agent {model_id} not available ({e}), using defaults")
                model_info = {
                    "max_input_tokens": 8192,
                    "max_output_tokens": 4096,
                }

            models.append(
                {
                    "id": model_id,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "amd-gaia",
                    "description": config.get("description", ""),
                    **model_info,
                }
            )

        return models

    def model_exists(self, model_id: str) -> bool:
        """
        Check if model ID exists.

        Args:
            model_id: Model ID to check

        Returns:
            True if model exists, False otherwise

        Example:
            >>> registry = AgentRegistry()
            >>> registry.model_exists("gaia-code")
            True
            >>> registry.model_exists("nonexistent")
            False
        """
        return model_id in AGENT_MODELS


# Global registry instance
registry = AgentRegistry()
