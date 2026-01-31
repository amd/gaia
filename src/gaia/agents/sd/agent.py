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
    model_id: str = "Qwen3-4B-Instruct-2507-GGUF"  # 4B model for prompt enhancement

    # Execution settings
    max_steps: int = 5
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

        # Initialize SD tools
        self.sd_prompt_to_open = config.prompt_to_open
        self.init_sd(
            output_dir=config.output_dir,
            default_model=config.sd_model,
        )
        self.register_sd_tools()

        # Initialize VLM tools for image analysis
        self.init_vlm(model="Qwen3-VL-4B-Instruct-GGUF")
        self.register_vlm_tools()

        logger.debug(
            f"SD Agent initialized with SD model: {config.sd_model}, VLM: Qwen3-VL-4B"
        )

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

After enhancing, use: generate_image with model="SD-Turbo", size="512x512", steps=4, cfg_scale=1.0
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

After enhancing, use: generate_image with model="SDXL-Turbo", size="512x512", steps=4, cfg_scale=1.0
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

AVAILABLE TOOLS:
- generate_image: Create images with enhanced prompts
- create_story_from_last_image: Analyze + create story from last generated image (SD-specific)
- analyze_image: Get detailed VLM description of any image (generic VLM tool)
- create_story_from_image: Create story from any image (generic VLM tool)
- answer_question_about_image: Answer questions about images (generic VLM tool)
- list_sd_models: List available models
- get_generation_history: See generated images in this session

USE TOOLS FLEXIBLY BASED ON USER REQUEST:

Example scenarios:
User: "create a robot kitten" → generate_image only
User: "create 3 robot kittens" → generate_image 3 times (different seeds)
User: "create a robot kitten with a story" → generate_image, then create_story_from_last_image
User: "tell me about that last image" → create_story_from_last_image (or analyze_image)
User: "what color are its eyes?" → answer_question_about_image(last generated image)
User: "create another one" → generate_image with similar prompt
User: "analyze the image at /path/to/file.png" → analyze_image with specific path

KEY POINTS:
- Enhance prompts following model-specific guidelines
- Use generate_image with explicit size, steps, cfg_scale for quality
- Story/analysis tools are OPTIONAL - only use if user requests
- create_story_from_last_image is a convenience (auto-finds last SD image)
- Generic VLM tools (analyze_image, create_story_from_image) work with any image path
- Be flexible - user might want multiple images, variations, or just one image without story

Example interaction with story:
User: "create a cute robot kitten and tell me a story about it"
You: [generate_image with enhanced prompt]
You: [create_story_from_last_image - SD-specific tool that wraps VLM tools]
You: "Generated a robot kitten with a story! Enhanced prompt: '...' Description: '...' Story: '...' Saved: [path]"

Example interaction without story:
User: "create a robot kitten"
You: [generate_image only]
You: "Generated! Enhanced prompt: '...' Saved: [path]"

Example with multiple images:
User: "create 3 different robot kittens"
You: [generate_image with seed=1]
You: [generate_image with seed=2]
You: [generate_image with seed=3]
You: "Generated 3 robot kitten variations! Saved to: [paths]"
"""

    def _register_tools(self):
        """Register SD tools and custom SD-specific tools."""
        # SD tools and VLM tools are already registered in __init__
        # via register_sd_tools() and register_vlm_tools()

        # Register SD-specific custom tool that wraps VLM functionality
        from gaia.agents.base.tools import tool

        @tool(
            atomic=True,
            name="create_story_from_last_image",
            description="SD-specific convenience: Analyze the last generated SD image and create a whimsical story. Automatically finds the most recent image from this session.",
            parameters={},
        )
        def create_story_from_last_image() -> dict:
            """
            Custom SD-Agent tool that wraps generic VLM tools for convenience.

            Demonstrates tool composition: an SD-specific wrapper that calls
            generic VLM tools under the hood.
            """
            if not self.sd_generations:
                return {
                    "status": "error",
                    "error": "No images generated yet. Generate an image first.",
                }

            # Get last generated image path
            last_gen = self.sd_generations[-1]
            image_path = last_gen["image_path"]

            # Call the generic VLM tool (if available)
            if hasattr(self, "_create_story_from_image"):
                result = self._create_story_from_image(
                    image_path, story_style="whimsical"
                )
                if result.get("status") == "success":
                    # Add SD-specific metadata
                    result["original_prompt"] = last_gen["prompt"]
                    result["sd_model"] = last_gen["model"]
                return result
            else:
                return {
                    "status": "error",
                    "error": "VLM tools not initialized. Agent needs VLMToolsMixin.",
                }

        # Register the custom tool
        self.register_tool(create_story_from_last_image)
