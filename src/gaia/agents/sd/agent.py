# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
SD Agent - LLM-powered prompt enhancement for Stable Diffusion image generation.

This agent analyzes user requests, enhances prompts with quality/lighting/style keywords,
and optimizes generation parameters based on the selected SD model.
"""

from dataclasses import dataclass
from typing import Optional

from gaia.agents.base.agent import Agent
from gaia.agents.sd.mixin import SDToolsMixin
from gaia.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SDAgentConfig:
    """Configuration for SD Agent."""

    # SD settings
    sd_model: str = "SD-Turbo"
    output_dir: str = ".gaia/cache/sd/images"
    prompt_to_open: bool = True

    # LLM settings
    use_claude: bool = False
    use_chatgpt: bool = False
    claude_model: str = "claude-sonnet-4-20250514"
    base_url: str = "http://localhost:8000/api/v1"
    model_id: Optional[str] = None  # None = use default

    # Execution settings
    max_steps: int = 5
    streaming: bool = False

    # Debug/output settings
    debug: bool = False
    show_stats: bool = False


class SDAgent(Agent, SDToolsMixin):
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

        # Initialize Agent base class
        super().__init__(
            use_claude=config.use_claude,
            use_chatgpt=config.use_chatgpt,
            claude_model=config.claude_model,
            base_url=config.base_url,
            model_id=config.model_id,
            max_steps=config.max_steps,
            streaming=config.streaming,
            show_stats=config.show_stats,
        )

        # Initialize SD tools
        self.sd_prompt_to_open = config.prompt_to_open
        self.init_sd(
            output_dir=config.output_dir,
            default_model=config.sd_model,
        )
        self.register_sd_tools()

        logger.debug(f"SD Agent initialized with model: {config.sd_model}")

    def _get_system_prompt(self) -> str:
        """System prompt with model-specific enhancement guidelines."""
        # Base guidelines from research:
        # - Stable Diffusion Art: https://stable-diffusion-art.com/prompt-guide/
        # - HuggingFace SDXL docs: https://huggingface.co/docs/diffusers/en/using-diffusers/sdxl_turbo
        # - IBM Prompt Engineering: https://www.ibm.com/think/prompt-engineering
        base_guidelines = """You are an expert image generation assistant using Stable Diffusion.

TASK: Enhance user prompts and generate high-quality images.

PROMPT ENHANCEMENT STRATEGY (based on SD research):
1. Identify subject and user intent
2. Add quality keywords: highly detailed, sharp focus, high resolution, 8K, photorealistic, DSLR-quality
3. Add lighting: golden hour, studio lighting, soft diffused light, dramatic lighting, volumetric lighting, rim lighting
4. Add style: digital art, oil painting, photorealistic, anime, concept art, ArtStation, Unreal Engine
5. Add composition: rule of thirds, centered, wide angle, close-up, bokeh, shallow depth of field
6. Structure: [subject with details] + [scene/environment] + [lighting] + [style] + [quality]

ENHANCEMENT EXAMPLES:
"a cat" → "fluffy orange tabby cat sitting on windowsill, soft natural lighting filtering through curtains, detailed fur texture, whiskers visible, photorealistic, shallow depth of field, DSLR-quality, 8K"

"sunset" → "vibrant sunset over calm ocean, golden hour lighting casting warm orange and purple hues across dramatic cumulus clouds, wide angle seascape composition, landscape photography, highly detailed, volumetric atmospheric lighting, 4K"

"robot" → "futuristic humanoid robot assistant with sleek metallic chrome finish and glowing blue LED accents, studio lighting setup with rim lights highlighting edges, sci-fi aesthetic, digital concept art, sharp focus, highly detailed mechanical parts, 8K render"
"""

        # Model-specific optimizations based on SD model capabilities
        model = self.config.sd_model

        if model == "SD-Turbo":
            model_specific = """
MODEL: SD-Turbo (very fast, 4 steps, 512x512)
OPTIMIZATION:
- Keep prompts focused (SD-Turbo responds better to concise descriptions)
- Emphasize main subject and 2-3 key visual elements
- Best for: quick iterations, testing, simple subjects
- Recommended: size=512x512, steps=4
- After enhancing, use: generate_image with model="SD-Turbo", size="512x512"
"""
        elif model == "SDXL-Turbo":
            model_specific = """
MODEL: SDXL-Turbo (fast, 4 steps, 512x512 optimal per HuggingFace)
OPTIMIZATION:
- More responsive to detailed prompts than SD-Turbo
- Add artistic style keywords (digital art, concept art, ArtStation aesthetic)
- Include specific lighting scenarios (volumetric, dramatic, soft diffused)
- Best for: stylized/artistic images with good quality-speed balance
- Note: 512x512 gives best quality (HuggingFace docs), 1024x1024 may degrade quality
- Recommended: size=512x512, steps=4
- After enhancing, use: generate_image with model="SDXL-Turbo", size="512x512"
"""
        elif model == "SDXL-Base-1.0":
            model_specific = """
MODEL: SDXL-Base-1.0 (photorealistic, 20 steps, 1024x1024)
OPTIMIZATION:
- Use natural language descriptions (SDXL understands full sentences)
- Add comprehensive environmental and material details
- Emphasize photorealistic keywords: DSLR-quality photograph, realistic, natural
- Include complete lighting scenarios: golden hour sunlight with soft shadows, professional studio lighting setup
- Can use keyword weights for emphasis: (keyword: 1.1) adds 10% emphasis, max 1.4
- Best for: professional quality, photorealistic renders, presentation images
- Recommended: size=1024x1024, steps=20, cfg_scale=7.5
- After enhancing, use: generate_image with model="SDXL-Base-1.0", size="1024x1024", steps=20, cfg_scale=7.5
"""
        else:  # SD-1.5
            model_specific = """
MODEL: SD-1.5 (general purpose, 20 steps, 512x512)
OPTIMIZATION:
- Traditional keyword-based prompts work well
- Balance between detail and conciseness
- Include quality modifiers and style references
- Best for: general purpose image generation
- Recommended: size=512x512, steps=20, cfg_scale=7.5
- After enhancing, use: generate_image with model="SD-1.5", size="512x512", steps=20, cfg_scale=7.5
"""

        return base_guidelines + model_specific + """

WORKFLOW:
1. Analyze user's request for subject, mood, desired style
2. Enhance prompt following guidelines above
3. Call generate_image with optimized parameters for this model
4. Report to user: enhanced prompt used + generation time + file path

CRITICAL:
- ALWAYS use generate_image tool
- Apply model-specific size/steps/cfg_scale recommendations
- Tell user the enhanced prompt you generated (so they learn)
- Include file path in response

Example interaction:
User: "a robot"
You: "I'll enhance that prompt for better quality. Generating: 'futuristic robot assistant with metallic chrome finish, studio lighting with rim lights, sci-fi aesthetic, highly detailed, 8K'..."
[calls generate_image]
You: "Image generated in 13.2s! Enhanced prompt: 'futuristic robot assistant with metallic chrome finish, studio lighting with rim lights, sci-fi aesthetic, highly detailed, 8K'. Saved to: [path]"
"""

    def _register_tools(self):
        """Register SD tools - already done in __init__ via register_sd_tools()."""
        pass
