# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
VLMToolsMixin - Vision Language Model tools for image analysis.

Provides generic VLM capabilities that can be used across any GAIA agent:
- Image description and analysis
- Story generation from images
- Question answering about images

Example:
    from gaia.agents.base import Agent
    from gaia.vlm import VLMToolsMixin

    class MyVisionAgent(Agent, VLMToolsMixin):
        def __init__(self):
            super().__init__()
            self.init_vlm()  # Initialize and auto-register tools

        def _register_tools(self):
            '''Required abstract method - VLM tools already registered in __init__.'''
            pass

        def _get_system_prompt(self):
            return "You analyze images. Use analyze_image, create_story_from_image, or answer_question_about_image tools."
"""

from pathlib import Path
from typing import Any, Dict

from gaia.logger import get_logger

logger = get_logger(__name__)


class VLMToolsMixin:
    """
    Mixin providing Vision Language Model (VLM) tools for image analysis.

    Tools provided:
    - analyze_image: Get detailed description of an image
    - create_story_from_image: Generate creative narrative about an image
    - answer_question_about_image: Answer specific questions about an image

    Attributes:
        vlm_model: VLM model to use (default: Qwen3-VL-4B-Instruct-GGUF)
        vlm_client: VLMClient instance for API calls
    """

    # Instance state (initialized by init_vlm)
    vlm_model: str
    vlm_client: Any  # Type: VLMClient (avoiding circular import)

    def init_vlm(
        self,
        model: str = "Qwen3-VL-4B-Instruct-GGUF",
        base_url: str = "http://localhost:8000",
    ) -> None:
        """
        Initialize VLM tools and register them with the agent.

        This method both initializes VLM state AND registers the tools automatically.
        No need to call register_vlm_tools() separately.

        Args:
            model: VLM model to use for image analysis
            base_url: Lemonade Server base URL

        Example:
            self.init_vlm()  # Use default Qwen3-VL-4B
            self.init_vlm(model="Qwen3-VL-8B-Instruct-GGUF")  # Use larger model
        """
        from gaia.llm.vlm_client import VLMClient

        self.vlm_model = model
        self.vlm_client = VLMClient(vlm_model=model, base_url=base_url)

        logger.debug(f"VLM tools initialized: model={model}")

        # Register VLM tools automatically during init
        from gaia.agents.base.tools import tool

        @tool(
            atomic=True,
            name="analyze_image",
            description="Analyze an image and provide a detailed description including colors, composition, mood, style, and interesting elements.",
            parameters={
                "image_path": {
                    "type": "str",
                    "description": "Path to the image file to analyze",
                    "required": True,
                },
                "focus": {
                    "type": "str",
                    "description": "Optional focus area: 'composition', 'colors', 'mood', 'details', or 'all' (default)",
                    "required": False,
                },
            },
        )
        def analyze_image(image_path: str, focus: str = "all") -> Dict[str, Any]:
            """Analyze an image with VLM."""
            return self._analyze_image(image_path, focus)

        @tool(
            atomic=True,
            name="create_story_from_image",
            description="Generate a creative short story (2-3 paragraphs) based on an image. Brings the image to life with narrative.",
            parameters={
                "image_path": {
                    "type": "str",
                    "description": "Path to the image file",
                    "required": True,
                },
                "story_style": {
                    "type": "str",
                    "description": "Story style: 'whimsical', 'dramatic', 'educational', 'adventure', or 'any' (default)",
                    "required": False,
                },
            },
        )
        def create_story_from_image(
            image_path: str, story_style: str = "any"
        ) -> Dict[str, Any]:
            """Create a creative story from an image."""
            return self._create_story_from_image(image_path, story_style)

        @tool(
            atomic=True,
            name="answer_question_about_image",
            description="Answer a specific question about an image using visual analysis.",
            parameters={
                "image_path": {
                    "type": "str",
                    "description": "Path to the image file",
                    "required": True,
                },
                "question": {
                    "type": "str",
                    "description": "Specific question to answer about the image",
                    "required": True,
                },
            },
        )
        def answer_question_about_image(
            image_path: str, question: str
        ) -> Dict[str, Any]:
            """Answer a question about an image."""
            return self._answer_question_about_image(image_path, question)

        # Tools are automatically registered by the @tool decorator above
        # No need to call register_tool() - it doesn't exist anyway

    def _analyze_image(self, image_path: str, focus: str = "all") -> Dict[str, Any]:
        """
        Analyze an image with VLM and provide detailed description.

        Args:
            image_path: Path to image file
            focus: What to focus on in the analysis

        Returns:
            Dict with status, description, and metadata
        """
        path = Path(image_path)
        if not path.exists():
            return {
                "status": "error",
                "error": f"Image not found: {image_path}",
            }

        # Build analysis prompt based on focus
        focus_prompts = {
            "composition": "Analyze the composition, framing, and arrangement of elements in this image.",
            "colors": "Describe the color palette, color relationships, and color mood in this image.",
            "mood": "Describe the mood, atmosphere, and emotional tone conveyed by this image.",
            "details": "Provide detailed observations about textures, materials, and fine details in this image.",
            "all": "Provide a detailed description of this image including composition, colors, mood, style, and interesting elements.",
        }

        prompt = focus_prompts.get(focus, focus_prompts["all"])

        try:
            # Read image file as bytes (VLMClient expects bytes, not path)
            image_bytes = path.read_bytes()

            # VLMClient.extract_from_image expects bytes and returns markdown string
            description = self.vlm_client.extract_from_image(image_bytes, prompt=prompt)

            return {
                "status": "success",
                "image_path": str(path),
                "focus": focus,
                "description": description,
                "model": self.vlm_model,
            }

        except Exception as e:
            logger.error(f"Image analysis failed: {e}", exc_info=True)
            return {
                "status": "error",
                "error": f"Image analysis failed: {str(e)}",
                "image_path": str(path),
            }

    def _create_story_from_image(
        self, image_path: str, story_style: str = "any"
    ) -> Dict[str, Any]:
        """
        Create a creative story based on an image.

        Args:
            image_path: Path to image file
            story_style: Style of story to create

        Returns:
            Dict with status, description, story, and metadata
        """
        path = Path(image_path)
        if not path.exists():
            return {
                "status": "error",
                "error": f"Image not found: {image_path}",
            }

        try:
            # Read image as bytes
            image_bytes = path.read_bytes()

            # First analyze the image
            description_prompt = "Describe this image in vivid detail. Include colors, composition, mood, style, and any interesting elements you notice."
            description = self.vlm_client.extract_from_image(
                image_bytes, prompt=description_prompt
            )

            # Build story prompt based on style
            style_instructions = {
                "whimsical": "playful and lighthearted with imagination",
                "dramatic": "intense and emotionally charged",
                "educational": "informative and teaches something",
                "adventure": "exciting with action and discovery",
                "any": "engaging and imaginative",
            }

            style_desc = style_instructions.get(story_style, "engaging and imaginative")

            story_prompt = f"""Based on this image, create a short creative story (2-3 paragraphs) that is {style_desc}. Make it engaging and bring the image to life with narrative. Include sensory details and character."""

            story = self.vlm_client.extract_from_image(image_bytes, prompt=story_prompt)

            return {
                "status": "success",
                "image_path": str(path),
                "description": description,
                "story": story,
                "story_style": story_style,
                "model": self.vlm_model,
            }

        except Exception as e:
            logger.error(f"Story creation failed: {e}", exc_info=True)
            return {
                "status": "error",
                "error": f"Story creation failed: {str(e)}",
                "image_path": str(path),
            }

    def _answer_question_about_image(
        self, image_path: str, question: str
    ) -> Dict[str, Any]:
        """
        Answer a specific question about an image.

        Args:
            image_path: Path to image file
            question: Question to answer

        Returns:
            Dict with status, question, answer, and metadata
        """
        path = Path(image_path)
        if not path.exists():
            return {
                "status": "error",
                "error": f"Image not found: {image_path}",
            }

        try:
            # Read image as bytes
            image_bytes = path.read_bytes()

            prompt = f"Answer this question about the image: {question}"
            answer = self.vlm_client.extract_from_image(image_bytes, prompt=prompt)

            return {
                "status": "success",
                "image_path": str(path),
                "question": question,
                "answer": answer,
                "model": self.vlm_model,
            }

        except Exception as e:
            logger.error(f"Question answering failed: {e}", exc_info=True)
            return {
                "status": "error",
                "error": f"Question answering failed: {str(e)}",
                "image_path": str(path),
                "question": question,
            }

    def get_vlm_system_prompt(self) -> str:
        """
        Get VLM-specific system prompt fragment for composition.

        Returns basic VLM usage guidelines that agents can compose into
        their full system prompt. VLM tools are generic and self-documenting,
        so this prompt is minimal.

        Returns:
            VLM usage guidelines (optional - tools are self-documenting)

        Example:
            def _get_system_prompt(self) -> str:
                vlm_prompt = self.get_vlm_system_prompt()
                return vlm_prompt  # Or compose with other prompts
        """
        return """Vision tools are available for image analysis. Use them when users ask about images:

- analyze_image(): For detailed descriptions (composition, colors, mood, style)
- create_story_from_image(): For creative narratives (2-3 paragraphs)
- answer_question_about_image(): For specific questions about image content

Tool schemas provide full parameter details. Be flexible based on user needs."""

    def cleanup_vlm(self) -> None:
        """
        Cleanup VLM resources.

        Call this when done with VLM operations to free resources.
        """
        if hasattr(self, "vlm_client") and self.vlm_client:
            try:
                if hasattr(self.vlm_client, "cleanup"):
                    self.vlm_client.cleanup()
                logger.debug("VLM client cleaned up")
            except Exception as e:
                logger.warning(f"VLM cleanup warning: {e}")
