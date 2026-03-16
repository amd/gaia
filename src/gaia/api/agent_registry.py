# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
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

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from gaia.agents.base.agent import Agent
from gaia.agents.base.api_agent import ApiAgent
from gaia.agents.base.configurable import ConfigurableAgent
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

    def __init__(self, custom_agents_dir: Optional[Path] = None):
        """Initialize registry with hardcoded agents and scan for custom ones"""
        self._loaded_classes: Dict[str, type] = {}
        self._custom_agents: Dict[str, Dict[str, Any]] = {}
        self._scan_custom_agents(custom_agents_dir)

    def _scan_custom_agents(self, custom_dir: Optional[Path] = None):
        """Scan for .json, .yml, .yaml, and .md agent definitions."""
        if custom_dir is None:
            # Determine the custom agents directory
            # In source: src/gaia/api/agent_registry.py -> src/gaia/agents/custom/
            api_dir = Path(__file__).parent
            custom_dir = api_dir.parent / "agents" / "custom"

        if not custom_dir.exists():
            try:
                custom_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"Created custom agents directory: {custom_dir}")
            except Exception as e:
                logger.error(f"Failed to create custom agents directory {custom_dir}: {e}")
                return

        for file_path in custom_dir.glob("*"):
            if file_path.suffix == ".json":
                self._load_json_agent(file_path)
            elif file_path.suffix in [".yml", ".yaml"]:
                self._load_yaml_agent(file_path)
            elif file_path.suffix == ".md":
                self._load_markdown_agent(file_path)

    def _load_json_agent(self, file_path: Path):
        """Load agent definition from a JSON file."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                config = json.load(f)

            model_id = config.get("id") or file_path.stem
            self._register_custom_agent(model_id, config)
            logger.info(f"Loaded custom JSON agent: {model_id}")
        except Exception as e:
            logger.error(f"Failed to load custom JSON agent from {file_path}: {e}")

    def _load_yaml_agent(self, file_path: Path):
        """Load agent definition from a YAML file (.yml or .yaml)."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)

            if not config:
                logger.warning(f"YAML agent file {file_path} is empty")
                return

            model_id = config.get("id") or file_path.stem
            self._register_custom_agent(model_id, config)
            logger.info(f"Loaded custom YAML agent: {model_id}")
            logger.debug(f"YAML agent {model_id} config: {config}")  # Debug log
        except yaml.YAMLError as e:
            logger.error(f"Failed to parse YAML agent file {file_path}: {e}")
        except Exception as e:
            logger.error(f"Failed to load custom YAML agent from {file_path}: {e}")

    def _load_markdown_agent(self, file_path: Path):
        """Load agent definition from a Markdown file with YAML-like frontmatter."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            # Simple YAML frontmatter parser (between ---)
            match = re.search(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
            if not match:
                logger.warning(f"Markdown agent {file_path} missing frontmatter delimiter (---)")
                return

            frontmatter = match.group(1)
            remaining_content = match.group(2)
            
            # Basic key: value parser for frontmatter
            config = {}
            for line in frontmatter.splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    config[key.strip()] = value.strip()
            
            # Use the remaining content as the system prompt if not explicitly provided
            if "system_prompt" not in config:
                config["system_prompt"] = remaining_content.strip()
            
            # Handle tools list if present (comma separated in frontmatter)
            if "tools" in config and isinstance(config["tools"], str):
                config["tools"] = [t.strip() for t in config["tools"].split(",")]

            model_id = config.get("id") or file_path.stem
            self._register_custom_agent(model_id, config)
            logger.info(f"Loaded custom Markdown agent: {model_id}")
        except Exception as e:
            logger.error(f"Failed to load custom Markdown agent from {file_path}: {e}")

    def _register_custom_agent(self, model_id: str, config: Dict[str, Any]):
        """Register a custom agent configuration."""
        # Extract persona fields for direct injection
        persona = config.get("persona", {})

        # Map to ConfigurableAgent class
        self._custom_agents[model_id] = {
            "type": "configurable",
            "config": {
                "name": config.get("name", model_id),
                "description": config.get("description", "Custom Configurable Agent"),
                "system_prompt": config.get("system_prompt", ""),
                "tools": config.get("tools", ["*"]),
                # Persona fields - these get injected into LLM context
                "persona": persona,
                "voice_characteristics": config.get("voice_characteristics"),
                "background": config.get("background"),
                "expertise": config.get("expertise"),
                "communication_style": config.get("communication_style"),
                "init_params": config.get("init_params", {})
            }
        }

    def _get_agent_config(self, model_id: str) -> Optional[Dict[str, Any]]:
        """Get agent configuration from hardcoded or custom models."""
        if model_id in AGENT_MODELS:
            return AGENT_MODELS[model_id]
        return self._custom_agents.get(model_id)

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
        config = self._get_agent_config(model_id)
        if not config:
            available = ", ".join(list(AGENT_MODELS.keys()) + list(self._custom_agents.keys()))
            raise ValueError(
                f"Model '{model_id}' not found. " f"Available models: {available}"
            )

        try:
            # Handle dynamic/configurable agents
            if config.get("type") == "configurable":
                agent_class = ConfigurableAgent
                agent_config = config["config"]
                init_params = agent_config.get("init_params", {}).copy()

                # Direct parameters for ConfigurableAgent
                init_params["name"] = agent_config["name"]
                init_params["description"] = agent_config["description"]
                init_params["system_prompt"] = agent_config["system_prompt"]
                init_params["tools"] = agent_config["tools"]
                # Persona context injection - CRITICAL FIX
                init_params["persona"] = agent_config.get("persona")
                init_params["voice_characteristics"] = agent_config.get("voice_characteristics")
                init_params["background"] = agent_config.get("background")
                init_params["expertise"] = agent_config.get("expertise")
                init_params["communication_style"] = agent_config.get("communication_style")
            else:
                # Handle hardcoded agents
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

        all_configs = {}
        all_configs.update(AGENT_MODELS)
        for mid, cfg in self._custom_agents.items():
            all_configs[mid] = {
                "description": cfg["config"]["description"],
                "class_name": "gaia.agents.base.configurable.ConfigurableAgent" if cfg["type"] == "configurable" else "",
                "init_params": cfg["config"].get("init_params", {}),
                "is_custom": True
            }

        for model_id, config in all_configs.items():
            try:
                # For custom agents, we might not want to fully instantiate them just for metadata
                # if we can avoid it. But list_models currently does it.
                
                if config.get("is_custom"):
                    model_info = {
                        "max_input_tokens": 8192,
                        "max_output_tokens": 4096,
                    }
                else:
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
        """
        return model_id in AGENT_MODELS or model_id in self._custom_agents


# Global registry instance
registry = AgentRegistry()
