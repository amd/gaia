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
- Step 1 returns: {"image_path": "./generated_images/robot_kitten_SDXL_20260203.png", ...}
- Step 2 receives: {"image_path": "./generated_images/robot_kitten_SDXL_20260203.png", "story_style": "whimsical"}
- The system automatically substitutes $PREV.image_path with the actual path

OTHER RULES:
- Generate ONE image by default (multiple only if explicitly requested: "3 images", "variations")
- Match story_style to user's request: "whimsical" (cute/playful), "adventure" (action), "dramatic" (intense), "any" (default)
- Include full story text in answer - users want to read it immediately
- After both tools complete (image + story), provide final answer immediately - DO NOT call tools again"""


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
