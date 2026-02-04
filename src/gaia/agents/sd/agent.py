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
    ctx_size: int = 16384  # 16K context for multi-step planning with dynamic parameters

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
                config.model_id,
                auto_download=True,
                prompt=False,
                timeout=120,
                ctx_size=config.ctx_size,  # Ensure 16K context for SD workflow
                save_options=True,  # Persist context setting
            )
            logger.debug(
                f"Loaded LLM model: {config.model_id} with {config.ctx_size} token context"
            )
        except Exception as e:
            logger.warning(f"LLM load warning: {e}")

        # Initialize Agent base class with 16K context requirement
        # SD multi-step planning requires 16K context for dynamic parameters
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
        """
        Agent-specific system prompt additions.

        Documents the custom create_story_from_image tool and workflow.
        """
        return """You are an image generation and storytelling agent.

WORKFLOW for image + story requests:
1. Create a 2-step plan using dynamic parameter placeholders
2. The plan executes automatically - you'll see results after completion
3. Provide final answer with the complete story text

DYNAMIC PARAMETER PLACEHOLDERS:
Use these in multi-step plans to reference previous results:
- $PREV.field - Get field from previous step result
- $STEP_0.field - Get field from specific step (0-indexed)

CORRECT multi-step plan:
{
  "thought": "Creating plan to generate image and story",
  "plan": [
    {
      "tool": "generate_image",
      "tool_args": {
        "prompt": "adorable robot kitten with glowing LED eyes...",
        "model": "SDXL-Turbo",
        "size": "512x512",
        "steps": 4
      }
    },
    {
      "tool": "create_story_from_image",
      "tool_args": {
        "image_path": "$PREV.image_path",
        "story_style": "whimsical"
      }
    }
  ]
}

How it works:
- Step 1 returns: {"image_path": ".gaia/cache/sd/images/robot_kitten_SDXL_20260203.png", ...}
- Step 2 receives: {"image_path": ".gaia/cache/sd/images/robot_kitten_SDXL_20260203.png", "story_style": "whimsical"}
- The system automatically substitutes $PREV.image_path with the actual path

OTHER RULES:
- Generate ONE image by default (multiple only if explicitly requested: "3 images", "variations")
- Match story_style to user's request: "whimsical" (cute/playful), "adventure" (action), "dramatic" (intense), "any" (default)
- Include full story text in answer - users want to read it immediately"""

    def _register_tools(self):
        """Register custom SD-specific tools."""
        from pathlib import Path

        from gaia.agents.base.tools import tool

        @tool(atomic=True)
        def create_story_from_image(image_path: str, story_style: str = "any") -> dict:
            """Generate a creative short story (2-3 paragraphs) based on an image."""
            path = Path(image_path)
            if not path.exists():
                return {"status": "error", "error": f"Image not found: {image_path}"}

            # Read image bytes
            image_bytes = path.read_bytes()

            # Build story prompt based on style
            style_map = {
                "whimsical": "playful and lighthearted",
                "dramatic": "intense and emotionally charged",
                "adventure": "exciting with action and discovery",
                "educational": "informative and teaches something",
                "any": "engaging and imaginative",
            }
            style_desc = style_map.get(story_style, "engaging and imaginative")

            # Call VLM to generate story
            prompt = f"Create a short creative story (2-3 paragraphs) that is {style_desc}. Bring the image to life with narrative. Include sensory details and character."
            story = self.vlm_client.extract_from_image(image_bytes, prompt=prompt)

            # Save story to text file
            base_path, _ = os.path.splitext(str(path))
            story_path = f"{base_path}_story.txt"

            with open(story_path, "w", encoding="utf-8") as f:
                f.write("=" * 80 + "\n")
                f.write("STORY\n")
                f.write("=" * 80 + "\n\n")
                f.write(story + "\n")

            return {
                "status": "success",
                "story": story,
                "story_style": story_style,
                "image_path": str(path),
                "story_file": story_path,
            }
