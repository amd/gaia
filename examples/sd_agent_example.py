"""
SD Agent Example - Multi-modal agent that generates images and creates stories.

This example matches the SD Agent Playbook (docs/playbooks/sd-agent/part-1-building-agent.mdx).
Demonstrates how to create a custom tool (create_story_from_image) that composes
SD and VLM capabilities.

Prerequisites:
    1. Install GAIA and initialize SD profile:
       uv pip install amd-gaia
       gaia init --profile sd

    2. This downloads (~15GB):
       - SDXL-Turbo (6.5GB) - Image generation
       - Qwen3-8B-GGUF (5GB) - Reasoning LLM
       - Qwen3-VL-4B-Instruct-GGUF (3.2GB) - Vision LLM

Usage:
    python examples/sd_agent_example.py

    Try:
    - "create a robot exploring ancient ruins"
    - "generate a sunset over mountains and describe the colors"
    - "make a cyberpunk street scene and tell me a story"
"""

from gaia.agents.base import Agent
from gaia.sd import SDToolsMixin
from gaia.vlm import VLMToolsMixin


class ImageStoryAgent(Agent, SDToolsMixin, VLMToolsMixin):
    """Agent that generates images and creates stories."""

    def __init__(self, output_dir="./generated_images"):
        super().__init__(model_id="Qwen3-8B-GGUF")
        self.init_sd(output_dir=output_dir, default_model="SDXL-Turbo")
        self.init_vlm(model="Qwen3-VL-4B-Instruct-GGUF")

    def _register_tools(self):
        from gaia.agents.base.tools import tool
        from pathlib import Path

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
                "any": "engaging and imaginative"
            }
            style_desc = style_map.get(story_style, "engaging and imaginative")

            # Call VLM to generate story
            prompt = f"Create a short creative story (2-3 paragraphs) that is {style_desc}. Bring the image to life with narrative."
            story = self.vlm_client.extract_from_image(image_bytes, prompt=prompt)

            return {
                "status": "success",
                "story": story,
                "story_style": story_style,
                "image_path": str(path)
            }

    def _get_system_prompt(self) -> str:
        return """You are an image generation and storytelling agent.

WORKFLOW for image + story requests:
1. Call generate_image() - THIS RETURNS: {"status": "success", "image_path": ".gaia/cache/sd/images/..."}
2. Extract the ACTUAL image_path value from step 1's result
3. Call create_story_from_image(image_path=<ACTUAL PATH FROM STEP 1>, story_style=<user's tone>)
4. Include the COMPLETE story text in your final answer

CRITICAL: Extract actual values from tool results, NEVER use placeholders like "$IMAGE_PATH$" or "generated_image_path"

CORRECT parameter passing:
Step 1 result: {"image_path": ".gaia/cache/sd/images/robot_123.png"}
Step 2 args: {"image_path": ".gaia/cache/sd/images/robot_123.png", "story_style": "adventure"} ✓

INCORRECT (DO NOT DO THIS):
Step 2 args: {"image_path": "$IMAGE_PATH$"} ✗
Step 2 args: {"image_path": "generated_image_path"} ✗

OTHER RULES:
- Generate ONE image by default (multiple only if explicitly requested: "3 images", "variations")
- Match story_style to user's request: "whimsical" (cute/playful), "adventure" (action), "dramatic" (intense), "any" (default)
- Include full story text in answer - users want to read it immediately"""


if __name__ == "__main__":
    import os

    os.makedirs("./generated_images", exist_ok=True)
    agent = ImageStoryAgent()

    print("Image Story Agent ready! Type 'quit' to exit.\n")

    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in ("quit", "exit", "q"):
            break
        if user_input:
            result = agent.process_query(user_input)
            if result.get("result"):
                print(f"\nAgent: {result['result']}\n")
