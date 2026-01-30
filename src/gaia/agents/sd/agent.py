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
    sd_model: str = "SDXL-Turbo"
    output_dir: str = ".gaia/cache/sd/images"
    prompt_to_open: bool = True

    # LLM settings (for prompt enhancement)
    use_claude: bool = False
    use_chatgpt: bool = False
    claude_model: str = "claude-sonnet-4-20250514"
    base_url: str = "http://localhost:8000/api/v1"
    model_id: str = "Qwen3-4B-GGUF"  # 4B model for prompt enhancement

    # Execution settings
    max_steps: int = 5
    streaming: bool = False
    ctx_size: int = 8192  # 8K context (sufficient for prompt enhancement)

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
        # Research sources (2026):
        # - SDXL Best Practices: https://neurocanvas.net/blog/sdxl-best-practices-guide/
        # - Photorealistic Guide: https://blog.segmind.com/generating-photographic-images-with-stable-diffusion/
        # - SDXL Prompts: https://stable-diffusion-art.com/sdxl-prompts/
        # - HuggingFace SDXL: https://huggingface.co/docs/diffusers/en/using-diffusers/sdxl_turbo
        base_guidelines = """You are an expert image generation assistant using Stable Diffusion with research-backed prompt engineering.

TASK: Enhance user prompts for optimal image quality using proven modifiers.

PROMPT ENHANCEMENT STRATEGY (2026 Research):
1. Identify subject, mood, and desired outcome
2. Add quality modifiers: highly detailed, sharp focus, 8K, Aqua Vista (depth enhancer), masterpiece
3. Add lighting: golden hour, volumetric lighting, studio setup, soft diffused, dramatic rim lights
4. Add style: digital art, concept art, photorealistic, Cinematic, Photographic, ArtStation
5. Add composition: rule of thirds, bokeh, shallow depth of field, wide angle, close-up
6. Use sentence structure (SDXL prefers descriptive sentences over comma tags)

PROVEN QUALITY BOOSTERS:
- "8K" - proven quality enhancer
- "Aqua Vista" - enhances depth and atmosphere
- "Photographic" style - best for faces and realism
- "Cinematic" style - good texture for skin/clothes
- "ArtStation" - pushes toward high-quality digital art aesthetic
- "masterpiece", "trending on ArtStation" - quality signals

ENHANCEMENT EXAMPLES:
"robot puppy" → "adorable robotic puppy with large expressive LED eyes and metallic silver body, sitting in playful pose with tilted head, soft studio lighting with rim lights highlighting metallic surfaces, digital art style, Cinematic aesthetic, highly detailed mechanical joints, sharp focus, 8K quality"

"sunset" → "vibrant sunset over calm ocean with golden hour lighting casting warm orange and purple hues across dramatic cumulus clouds, sun on horizon with volumetric god rays, wide angle seascape composition in Cinematic style, landscape photography, highly detailed atmospheric effects, 8K quality"

"robot owl" → "futuristic mechanical owl perched on branch with large glowing amber LED eyes, intricate bronze and copper metallic feather details showing individual gear mechanisms, soft dramatic lighting, steampunk Photographic aesthetic, highly detailed textures, sharp focus on mechanical elements, 8K render, trending on ArtStation"
"""

        # Model-specific optimizations based on SD model capabilities
        model = self.config.sd_model

        if model == "SD-Turbo":
            model_specific = """
MODEL: SD-Turbo (very fast, 4 steps, 512x512)
OPTIMIZATION:
- Keep prompts concise and focused (less sensitive to detailed prompts than SDXL)
- Emphasize main subject + 2-3 key visual elements only
- Simple quality modifiers: "detailed", "4K", "clean"
- Basic lighting: "soft light", "dramatic light"
- Best for: rapid iteration, quick testing, concept validation
- Recommended: size=512x512, steps=4, cfg_scale=1.0

SIMPLE ENHANCEMENT PATTERN:
[Subject] + [2-3 key attributes] + [basic lighting] + [quality: detailed, 4K]

After enhancing, use: generate_image with model="SD-Turbo", size="512x512", steps=4
"""
        elif model == "SDXL-Turbo":
            model_specific = """
MODEL: SDXL-Turbo (fast, 4 steps, 512x512 optimal)
RESEARCH-BASED OPTIMIZATION (neurocanvas.net, stable-diffusion-art.com):
- Use sentence-style prompts (SDXL prefers descriptive sentences over tag lists)
- Add proven modifiers: "8K", "Aqua Vista" (enhances depth), "masterpiece"
- Style keywords: "Photographic" (for faces), "Cinematic" (for texture/atmosphere), "ArtStation aesthetic"
- Lighting specifics: volumetric fog, dramatic rim lights, soft diffused studio light
- Can use keyword weights: (keyword: 1.1) = 10% emphasis, max 1.4
- Best quality at 512x512 (HuggingFace docs confirm), 1024x1024 may degrade
- Recommended: size=512x512, steps=4, cfg_scale=1.0

ENHANCEMENT PATTERN:
[Subject with materials/textures] + [descriptive action/pose] + [lighting scenario] + [style: Cinematic/Photographic] + [quality: 8K, Aqua Vista, sharp focus]

After enhancing, use: generate_image with model="SDXL-Turbo", size="512x512", steps=4
"""
        elif model == "SDXL-Base-1.0":
            model_specific = """
MODEL: SDXL-Base-1.0 (photorealistic, 20 steps, 1024x1024)
RESEARCH-BASED OPTIMIZATION (Civitai, Segmind photorealistic guides):
- Use full descriptive sentences (SDXL excels at natural language)
- Add camera settings for realism: "35mm lens", "f/2.8 aperture", "ISO 500", "shallow depth of field"
- Style: ALWAYS use "Photographic" or "Cinematic" for photorealistic results
- Lighting scenarios: "golden hour sunlight", "studio three-point lighting", "soft box diffusion"
- Material/texture details: "brushed metal", "soft fabric", "rough stone texture"
- Keyword weights for emphasis: (subject: 1.2), (quality: 1.1), max 1.4
- Quality modifiers: "8K", "DSLR photograph", "professional photography", "highly detailed"
- Avoid cartoon elements: Don't use "illustration", "anime", "CGI", "3D render" for photorealism
- Composition: "rule of thirds", "bokeh background", "shallow depth of field"
- Trained on 1024x1024 (optimal resolution)
- Recommended: size=1024x1024, steps=20, cfg_scale=7.5

PHOTOREALISTIC PATTERN:
[Subject with specific materials] + [natural language description] + [camera settings: lens, aperture, ISO] + [lighting scenario] + [style: Photographic] + [quality: 8K, DSLR photograph]

EXAMPLE:
"portrait" → "portrait of person with expressive eyes, natural skin texture and pores visible, captured with 50mm lens at f/2.8 aperture and ISO 320, soft diffused studio lighting from left, Photographic style, professional DSLR photograph, highly detailed, 8K quality"

After enhancing, use: generate_image with model="SDXL-Base-1.0", size="1024x1024", steps=20, cfg_scale=7.5
"""
        else:  # SD-1.5
            model_specific = """
MODEL: SD-1.5 (general purpose, 20 steps, 512x512)
OPTIMIZATION:
- Traditional comma-separated keyword approach
- Balance: descriptive but not excessive
- Quality modifiers: "highly detailed", "8K", "sharp focus"
- Style references: "digital art", "oil painting", "photorealistic"
- Lighting: "golden hour", "studio lighting", "dramatic"
- Best for: general purpose generation, legacy compatibility
- Recommended: size=512x512, steps=20, cfg_scale=7.5

BALANCED PATTERN:
[Subject], [key attributes], [lighting], [style], [quality modifiers]

After enhancing, use: generate_image with model="SD-1.5", size="512x512", steps=20, cfg_scale=7.5
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
