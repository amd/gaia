# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
SD Agent - LLM-powered prompt enhancement for Stable Diffusion image generation.

This agent analyzes user requests, enhances prompts with quality/lighting/style keywords,
and optimizes generation parameters based on the selected SD model.
"""

import os
from dataclasses import dataclass
from typing import Optional

from gaia.agents.base.agent import Agent
from gaia.agents.sd.prompts import (
    BASE_GUIDELINES,
    MODEL_SPECIFIC_PROMPTS,
    WORKFLOW_INSTRUCTIONS,
)
from gaia.logger import get_logger
from gaia.sd import SDToolsMixin
from gaia.vlm import VLMToolsMixin

logger = get_logger(__name__)


@dataclass
class SDAgentConfig:
    """Configuration for SD Agent."""

    # SD settings
    sd_model: str = "SDXL-Turbo"
    output_dir: str = ".gaia/cache/sd/images"
    prompt_to_open: bool = True

    # LLM settings (for prompt enhancement)
    use_claude: bool = False
    use_chatgpt: bool = False
    claude_model: str = "claude-sonnet-4-20250514"
    base_url: str = "http://localhost:8000/api/v1"
    model_id: str = "Qwen3-8B-GGUF"  # 8B model for robust agentic reasoning

    # Execution settings
    max_steps: int = 10
    streaming: bool = False
    ctx_size: int = 8192  # 8K context (sufficient for prompt enhancement)

    # Debug/output settings
    debug: bool = False
    show_stats: bool = False


class SDAgent(Agent, SDToolsMixin, VLMToolsMixin):
    """
    Image generation agent with LLM-powered prompt enhancement.

    This agent:
    - Analyzes user intent from natural language
    - Enhances prompts with quality/lighting/style keywords
    - Optimizes generation parameters per SD model
    - Uses research-based best practices for each model
    """

    def __init__(self, config: Optional[SDAgentConfig] = None, **kwargs):
        """
        Initialize SD agent.

        Args:
            config: SDAgentConfig instance (or None for defaults)
            **kwargs: Override specific config values
        """
        # Merge config with kwargs
        if config is None:
            config = SDAgentConfig()

        for key, value in kwargs.items():
            if hasattr(config, key):
                setattr(config, key, value)

        self.config = config

        # Load LLM model BEFORE initializing Agent base class to avoid context warnings
        # Agent will check context size, so we need the model loaded first
        from gaia.llm.lemonade_client import LemonadeClient

        llm_client = LemonadeClient(verbose=False)
        try:
            llm_client.load_model(
                config.model_id, auto_download=True, prompt=False, timeout=120
            )
            logger.debug(f"Loaded LLM model: {config.model_id}")
        except Exception as e:
            logger.warning(f"LLM load warning: {e}")

        # Initialize Agent base class with reduced context requirement
        # SD prompt enhancement doesn't need 32K context, 8K is sufficient
        super().__init__(
            use_claude=config.use_claude,
            use_chatgpt=config.use_chatgpt,
            claude_model=config.claude_model,
            base_url=config.base_url,
            model_id=config.model_id,
            max_steps=config.max_steps,
            streaming=config.streaming,
            show_stats=config.show_stats,
            min_context_size=config.ctx_size,  # 8K sufficient for prompt enhancement
        )

        # Initialize SD tools (auto-registers tools)
        self.sd_prompt_to_open = config.prompt_to_open
        self.init_sd(
            output_dir=config.output_dir,
            default_model=config.sd_model,
        )

        # Initialize VLM tools (auto-registers tools)
        self.init_vlm(model="Qwen3-VL-4B-Instruct-GGUF")

        logger.debug(
            f"SD Agent initialized with SD model: {config.sd_model}, VLM: Qwen3-VL-4B"
        )

    def _get_system_prompt(self) -> str:
        """System prompt with model-specific enhancement guidelines."""
        # Get model-specific prompt from prompts.py
        model_specific = MODEL_SPECIFIC_PROMPTS.get(
            self.config.sd_model, MODEL_SPECIFIC_PROMPTS["SD-1.5"]
        )

        return BASE_GUIDELINES + model_specific + WORKFLOW_INSTRUCTIONS

    def _register_tools(self):
        """Register custom SD-specific tools."""
        # SD tools and VLM tools are already registered in __init__
        # via init_sd() and init_vlm() (they auto-register)

        # Define SD-specific custom tool that wraps VLM functionality
        # The @tool decorator automatically registers it in the global tool registry
        from gaia.agents.base.tools import tool

        @tool(
            atomic=True,
            name="create_story_from_last_image",
            description="SD-specific convenience: Analyze the last generated SD image and create a whimsical story. Automatically finds the most recent image from this session. Can optionally specify image_path.",
            parameters={
                "image_path": {
                    "type": "string",
                    "description": "Optional: path to specific image. If not provided, uses last generated image.",
                    "required": False,
                }
            },
        )
        def create_story_from_last_image(image_path: str = None) -> dict:
            """
            Custom SD-Agent tool that wraps generic VLM tools for convenience.

            Demonstrates tool composition: an SD-specific wrapper that calls
            generic VLM tools under the hood.

            Args:
                image_path: Optional path to specific image. If None or empty, uses last generated image.
            """
            # Treat empty string same as None for auto-find
            if not image_path:
                # Auto-find last generated image
                if not self.sd_generations:
                    return {
                        "status": "error",
                        "error": "No images generated yet. Generate an image first.",
                    }

                # Get last generated image path
                last_gen = self.sd_generations[-1]
                image_path = last_gen["image_path"]
            else:
                # Use provided path, try to find it in generation history
                last_gen = None
                for gen in reversed(self.sd_generations):
                    if (
                        gen["image_path"] == image_path
                        or image_path in gen["image_path"]
                    ):
                        last_gen = gen
                        image_path = gen["image_path"]
                        break

            # Call the generic VLM tool (if available)
            if hasattr(self, "_create_story_from_image"):
                result = self._create_story_from_image(
                    image_path, story_style="whimsical"
                )
                if result.get("status") == "success":
                    # Add SD-specific metadata if available
                    if last_gen:
                        result["original_prompt"] = last_gen["prompt"]
                        result["sd_model"] = last_gen["model"]

                    # Save story to text file
                    story_text = result.get("story", "")
                    description = result.get("description", "")

                    # Create story filename based on image filename
                    img_path = result.get("image_path", image_path)
                    base_path, _ = os.path.splitext(img_path)
                    story_path = f"{base_path}_story.txt"

                    # Write story and description to file
                    with open(story_path, "w", encoding="utf-8") as f:
                        f.write("=" * 80 + "\n")
                        f.write("STORY\n")
                        f.write("=" * 80 + "\n\n")
                        f.write(story_text + "\n\n")
                        f.write("=" * 80 + "\n")
                        f.write("IMAGE DESCRIPTION\n")
                        f.write("=" * 80 + "\n\n")
                        f.write(description + "\n")

                    result["story_file"] = story_path
                    logger.debug(f"Story saved to: {story_path}")

                return result
            else:
                return {
                    "status": "error",
                    "error": "VLM tools not initialized. Agent needs VLMToolsMixin.",
                }

        # No need to call register_tool() - the @tool decorator does it automatically
