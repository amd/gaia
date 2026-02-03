"""
SD Agent Example - Image generation using the SDToolsMixin.

This minimal example demonstrates how to create an agent that can generate
images using Stable Diffusion via the Lemonade Server endpoint.

Prerequisites:
    1. Lemonade Server running with SD model:
       lemonade-server serve
       lemonade-server pull SD-Turbo

    2. GAIA SDK installed:
       pip install amd-gaia

Usage:
    python examples/sd_agent_example.py

    # Or interactively:
    python -c "
    from examples.sd_agent_example import ImageAgent
    agent = ImageAgent()
    agent.run('Create an image of a dragon')
    "
"""

from gaia.agents.base import Agent
from gaia.sd import SDToolsMixin


class ImageAgent(Agent, SDToolsMixin):
    """
    A simple agent that generates images from text descriptions.

    Combines the base Agent class with SDToolsMixin to provide
    image generation capabilities via Stable Diffusion.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        output_dir: str = "./generated_images",
        **kwargs,
    ):
        """
        Initialize the Image Agent.

        Args:
            base_url: Lemonade Server URL
            output_dir: Directory to save generated images
            **kwargs: Additional args passed to Agent base class
        """
        super().__init__(**kwargs)

        # Initialize SD tools with configuration
        # Initialize SD tools (auto-registers tools)
        self.init_sd(
            base_url=base_url,
            output_dir=output_dir,
            default_model="SD-Turbo",
            default_size="512x512",
        )

    def _register_tools(self):
        """SD tools registered by init_sd()."""
        pass

    def _get_system_prompt(self) -> str:
        """System prompt that guides the agent's behavior."""
        return """You are an image generation assistant powered by Stable Diffusion.

When the user asks you to create, generate, or make an image, use the generate_image tool.

PROMPT ENHANCEMENT TIPS:
Before generating, enhance simple prompts for better results:
- Add artistic style (photorealistic, anime, oil painting, digital art)
- Add lighting (golden hour, dramatic lighting, soft light, studio lighting)
- Add quality keywords (high quality, detailed, 4k, masterpiece)
- Add composition details (rule of thirds, centered, wide angle)

EXAMPLES:
- "a cat" → "a fluffy orange cat, soft natural lighting, detailed fur, photorealistic, 4k"
- "sunset" → "vibrant sunset over calm ocean, golden hour, dramatic clouds, wide angle, 4k"
- "robot" → "futuristic robot, metallic chrome finish, studio lighting, sci-fi, detailed, 4k"

Always enhance the user's prompt before generating for better results.
After generating, tell the user where the image was saved.
"""


def main():
    """Run the image agent interactively."""
    print("=" * 60)
    print("SD Agent Example")
    print("=" * 60)
    print()
    print("This agent generates images using Stable Diffusion.")
    print("Images are saved to: ./generated_images/")
    print()
    print("Example prompts:")
    print("  - Create an image of a mountain at sunset")
    print("  - Generate a cyberpunk city at night")
    print("  - Make an image of a dragon on a cliff")
    print()
    print("Type 'quit' to exit.")
    print("=" * 60)
    print()

    # Create the agent
    agent = ImageAgent()

    # Check SD endpoint health
    health = agent.sd_health_check()
    if health["status"] != "healthy":
        print(f"Warning: {health.get('error', 'SD endpoint unavailable')}")
        print("Make sure Lemonade Server is running:")
        print("  lemonade-server serve")
        print("  lemonade-server pull SD-Turbo")
        print()

    # Interactive loop
    while True:
        try:
            user_input = input("You: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit", "q"):
                print("Goodbye!")
                break

            # Process query with agent
            result = agent.process_query(user_input)
            if result.get("final_answer"):
                print(f"\nAgent: {result['final_answer']}\n")
            else:
                print("\nGeneration complete\n")

        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as e:
            print(f"\nError: {e}\n")


if __name__ == "__main__":
    main()
