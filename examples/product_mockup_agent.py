#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Product Mockup Agent Example

A product manager's AI assistant that generates HTML landing page mockups
with Tailwind CSS based on product descriptions. Perfect for rapid prototyping
and visualizing product ideas.

Requirements:
- Python 3.12+
- Lemonade server running for LLM reasoning

Run:
    uv run examples/product_mockup_agent.py

Examples:
    You: Create a landing page for 'FastTrack CRM' - a sales automation tool
         with features: Lead Scoring, Email Campaigns, Analytics Dashboard
    You: Generate a mockup for a fitness app called 'FitPro' with workout tracking,
         nutrition planning, and progress charts
"""

from pathlib import Path

from gaia import Agent, tool


class ProductMockupAgent(Agent):
    """Agent that generates product landing page mockups."""

    def __init__(self, output_dir: str = "./mockups", **kwargs):
        """Initialize the Product Mockup Agent.

        Args:
            output_dir: Directory to save generated HTML files
            **kwargs: Additional arguments passed to Agent
        """
        super().__init__(**kwargs)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _get_system_prompt(self) -> str:
        """Generate the system prompt for the agent."""
        return f"""You are a product mockup generator for product managers.

Create professional HTML landing pages with Tailwind CSS based on product descriptions.

Your mockups should be:
1. Clean and modern with dark theme
2. Include hero section with product name and description
3. Feature cards showcasing key capabilities
4. Responsive and mobile-friendly
5. Visually appealing with good use of spacing and typography

All HTML files are saved to: {self.output_dir}

Use the generate_landing_page tool to create mockups."""

    def _register_tools(self):
        """Register mockup generation tools."""
        agent = self

        @tool
        def generate_landing_page(
            product_name: str, description: str, features: list
        ) -> dict:
            """Generate an HTML landing page mockup for a product.

            Args:
                product_name: Name of the product
                description: Short description/tagline for the product
                features: List of key features (3-6 recommended)

            Returns:
                dict with success status and file path
            """
            # Create HTML with Tailwind CSS
            html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{product_name}</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-900 text-white min-h-screen">
    <!-- Hero Section -->
    <div class="container mx-auto px-4 py-16">
        <div class="text-center mb-16">
            <h1 class="text-6xl font-bold mb-6 bg-gradient-to-r from-blue-400 to-purple-500 bg-clip-text text-transparent">
                {product_name}
            </h1>
            <p class="text-2xl text-gray-300 max-w-3xl mx-auto">
                {description}
            </p>
        </div>

        <!-- Features Grid -->
        <div class="grid md:grid-cols-3 gap-8 max-w-6xl mx-auto">
"""
            # Add feature cards
            for feature in features:
                html += f"""            <div class="bg-gray-800 p-8 rounded-lg border border-gray-700 hover:border-blue-500 transition-colors">
                <h3 class="text-xl font-semibold mb-3 text-blue-400">{feature}</h3>
                <p class="text-gray-400">Enhanced capabilities for {feature.lower()}</p>
            </div>
"""

            html += """        </div>

        <!-- CTA Section -->
        <div class="text-center mt-16">
            <button class="bg-blue-500 hover:bg-blue-600 text-white font-bold py-4 px-8 rounded-lg text-lg transition-colors">
                Get Started
            </button>
        </div>
    </div>

    <!-- Footer -->
    <footer class="text-center text-gray-500 py-8 mt-16 border-t border-gray-800">
        <p>Generated with GAIA Product Mockup Agent</p>
    </footer>
</body>
</html>"""

            # Save to file
            filename = product_name.lower().replace(" ", "_").replace("'", "") + ".html"
            filepath = agent.output_dir / filename

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(html)

            return {
                "success": True,
                "file": str(filepath.absolute()),
                "message": f"Generated landing page: {filepath.absolute()}",
            }


def main():
    """Run the Product Mockup Agent interactively."""
    print("=" * 60)
    print("Product Mockup Agent - Landing Page Generator")
    print("=" * 60)
    print("\nGenerate HTML mockups for product ideas in seconds!")
    print("\nExamples:")
    print("  - 'Create a landing page for FastTrack CRM with Lead Scoring,")
    print("     Email Campaigns, and Analytics Dashboard'")
    print("  - 'Generate a mockup for FitPro fitness app with Workout Tracking,")
    print("     Nutrition Planning, and Progress Charts'")
    print("\nType 'quit' or 'exit' to stop.\n")

    # Create agent (uses local Lemonade server by default)
    try:
        agent = ProductMockupAgent()
        print(f"Product Mockup Agent ready! Files saved to: {agent.output_dir.absolute()}\n")
    except Exception as e:
        print(f"Error initializing agent: {e}")
        print("\nMake sure Lemonade server is running:")
        print("  lemonade-server serve")
        return

    # Interactive loop
    while True:
        try:
            user_input = input("You: ").strip()

            if not user_input:
                continue

            if user_input.lower() in ("quit", "exit", "q"):
                print("Goodbye!")
                break

            # Process the query
            result = agent.process_query(user_input)
            if result.get("result"):
                print(f"\nAgent: {result['result']}\n")

        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as e:
            print(f"\nError: {e}\n")


if __name__ == "__main__":
    main()
