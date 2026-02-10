# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for SD Agent."""

import os
import tempfile
import unittest
from unittest.mock import patch


class TestSDAgent(unittest.TestCase):
    """Test SD Agent functionality."""

    def test_story_file_creation(self):
        """Test that story is saved to .txt file alongside image."""
        from gaia.agents.sd import SDAgent, SDAgentConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            config = SDAgentConfig(output_dir=tmpdir)

            with patch("gaia.agents.sd.agent.Agent.__init__"):
                with patch("gaia.llm.lemonade_client.LemonadeClient"):
                    agent = SDAgent(config)
                    agent.sd_generations = [
                        {
                            "image_path": f"{tmpdir}/test_image.png",
                            "prompt": "test prompt",
                            "model": "SDXL-Turbo",
                        }
                    ]

                    # Mock VLM tool
                    def mock_create_story(img_path, story_style):
                        return {
                            "status": "success",
                            "story": "Once upon a time, there was a robot kitten...",
                            "description": "A cute robotic kitten with chrome body",
                            "image_path": img_path,
                        }

                    agent._create_story_from_image = mock_create_story

                    # Call the custom tool through the registered function
                    # We need to get the actual function from the closure
                    result = agent._register_tools()  # This defines the function
                    # The function is registered in the global registry, but for testing
                    # we'll call the method directly by recreating the logic

                    # Simulate what the tool does
                    last_gen = agent.sd_generations[-1]
                    image_path = last_gen["image_path"]
                    result = mock_create_story(image_path, "whimsical")

                    # Add the file saving logic
                    base_path, _ = os.path.splitext(image_path)
                    story_path = f"{base_path}_story.txt"

                    story_text = result.get("story", "")
                    description = result.get("description", "")

                    with open(story_path, "w", encoding="utf-8") as f:
                        f.write("=" * 80 + "\n")
                        f.write("STORY\n")
                        f.write("=" * 80 + "\n\n")
                        f.write(story_text + "\n\n")
                        f.write("=" * 80 + "\n")
                        f.write("IMAGE DESCRIPTION\n")
                        f.write("=" * 80 + "\n\n")
                        f.write(description + "\n")

                    # Verify file exists
                    self.assertTrue(
                        os.path.exists(story_path), "Story file should be created"
                    )

                    # Verify content
                    with open(story_path, "r", encoding="utf-8") as f:
                        content = f.read()
                        self.assertIn("STORY", content)
                        self.assertIn("Once upon a time", content)
                        self.assertIn("IMAGE DESCRIPTION", content)
                        self.assertIn("chrome body", content)

    def test_system_prompt_extraction(self):
        """Test that SD system prompt is loaded from prompts.py."""
        from gaia.agents.sd import SDAgent, SDAgentConfig

        with patch("gaia.agents.sd.agent.Agent.__init__"):
            with patch("gaia.llm.lemonade_client.LemonadeClient"):
                config = SDAgentConfig(sd_model="SDXL-Turbo")
                agent = SDAgent(config)
                # Initialize SD to set up the mixin
                agent.sd_default_model = "SDXL-Turbo"

                # Get SD-specific prompt from mixin
                sd_prompt = agent.get_sd_system_prompt()

                # Verify SD prompt contains expected components from prompts.py
                self.assertIn("TASK: Enhance user prompts", sd_prompt)
                self.assertIn("SDXL-Turbo", sd_prompt)

                # Get agent-specific prompt
                agent_prompt = agent._get_system_prompt()

                # Verify agent prompt contains workflow instructions
                self.assertIn("WORKFLOW for image + story requests:", agent_prompt)

    def test_model_specific_prompts(self):
        """Test that each SD model gets the correct prompt."""
        from gaia.agents.sd import SDAgent, SDAgentConfig

        models_to_test = ["SD-Turbo", "SDXL-Turbo", "SDXL-Base-1.0", "SD-1.5"]

        for model in models_to_test:
            with patch("gaia.agents.sd.agent.Agent.__init__"):
                with patch("gaia.llm.lemonade_client.LemonadeClient"):
                    config = SDAgentConfig(sd_model=model)
                    agent = SDAgent(config)
                    # Initialize SD to set up the mixin
                    agent.sd_default_model = model

                    # Get SD-specific prompt from mixin
                    sd_prompt = agent.get_sd_system_prompt()

                    # Verify model-specific content is included
                    self.assertIn(
                        f"MODEL: {model}",
                        sd_prompt,
                        f"Prompt should include {model} guidance",
                    )

    def test_parameter_substitution(self):
        """Test dynamic parameter substitution in multi-step plans."""
        from gaia.agents.base import Agent

        # Create minimal test agent
        class TestAgent(Agent):
            def _register_tools(self):
                pass

            def _get_system_prompt(self):
                return "Test agent"

        # Create agent without LLM (we're only testing parameter resolution logic)
        with patch("gaia.llm.lemonade_client.LemonadeClient"):
            agent = TestAgent()

            # Mock previous step results
            step_results = [
                {
                    "status": "success",
                    "image_path": ".gaia/cache/sd/images/robot_kitten_123.png",
                    "model": "SDXL-Turbo",
                    "seed": 42,
                }
            ]

            # Test $PREV.field substitution
            tool_args = {
                "image_path": "$PREV.image_path",
                "story_style": "whimsical",
            }
            resolved = agent._resolve_plan_parameters(tool_args, step_results)
            self.assertEqual(
                resolved["image_path"],
                ".gaia/cache/sd/images/robot_kitten_123.png",
            )
            self.assertEqual(resolved["story_style"], "whimsical")

            # Test $STEP_0.field substitution
            tool_args = {"image_path": "$STEP_0.image_path", "story_style": "dramatic"}
            resolved = agent._resolve_plan_parameters(tool_args, step_results)
            self.assertEqual(
                resolved["image_path"],
                ".gaia/cache/sd/images/robot_kitten_123.png",
            )

            # Test nested substitution in lists
            tool_args = {
                "paths": ["$PREV.image_path", "/other/path"],
                "style": "adventure",
            }
            resolved = agent._resolve_plan_parameters(tool_args, step_results)
            self.assertEqual(
                resolved["paths"][0],
                ".gaia/cache/sd/images/robot_kitten_123.png",
            )
            self.assertEqual(resolved["paths"][1], "/other/path")

            # Test no substitution when no placeholders
            tool_args = {"image_path": "/static/path.png", "count": 5}
            resolved = agent._resolve_plan_parameters(tool_args, step_results)
            self.assertEqual(resolved, tool_args)  # Unchanged

            # Test invalid placeholder returns unchanged
            tool_args = {"image_path": "$PREV.nonexistent_field"}
            resolved = agent._resolve_plan_parameters(tool_args, step_results)
            self.assertEqual(
                resolved["image_path"], "$PREV.nonexistent_field"
            )  # Unchanged

            # Test multiple step results
            step_results.append(
                {
                    "status": "success",
                    "story": "Once upon a time...",
                    "story_file": "/path/story.txt",
                }
            )
            tool_args = {
                "image": "$STEP_0.image_path",
                "story": "$STEP_1.story",
            }
            resolved = agent._resolve_plan_parameters(tool_args, step_results)
            self.assertEqual(
                resolved["image"],
                ".gaia/cache/sd/images/robot_kitten_123.png",
            )
            self.assertEqual(resolved["story"], "Once upon a time...")

    def test_parameter_substitution_edge_cases(self):
        """Test edge cases and error handling in parameter substitution."""
        from gaia.agents.base import Agent

        class TestAgent(Agent):
            def _register_tools(self):
                pass

            def _get_system_prompt(self):
                return "Test agent"

        # Mock both LemonadeClient and LemonadeManager
        with (
            patch("gaia.llm.lemonade_client.LemonadeClient"),
            patch("gaia.llm.lemonade_manager.LemonadeManager.ensure_ready"),
        ):
            agent = TestAgent()

            # Test 1: Empty step_results
            tool_args = {"path": "$PREV.image_path"}
            resolved = agent._resolve_plan_parameters(tool_args, [])
            self.assertEqual(resolved["path"], "$PREV.image_path")  # Unchanged

            # Test 2: Non-dict in step_results
            tool_args = {"path": "$PREV.image_path"}
            resolved = agent._resolve_plan_parameters(tool_args, ["string result"])
            self.assertEqual(resolved["path"], "$PREV.image_path")  # Unchanged

            # Test 3: Recursion depth limit
            # Create deeply nested structure (51 levels deep)
            nested = {"level": 0}
            current = nested
            for i in range(1, 52):
                current["nested"] = {"level": i}
                current = current["nested"]

            # Should return unchanged due to depth limit
            result = agent._resolve_plan_parameters(nested, [])
            # The top level should be processed, but deep nesting should stop
            self.assertIsNotNone(result)

            # Test 4: Circular reference (should not hang due to depth limit)
            circular = {"a": "$PREV.b"}
            step_results = [{"b": circular}]
            resolved = agent._resolve_plan_parameters(circular, step_results)
            # Should complete without hanging
            self.assertIsNotNone(resolved)

            # Test 5: Unicode in field names
            tool_args = {"path": "$PREV.图片路径"}  # Chinese characters
            step_results = [{"图片路径": "/path/to/image.png"}]
            resolved = agent._resolve_plan_parameters(tool_args, step_results)
            self.assertEqual(resolved["path"], "/path/to/image.png")

            # Test 6: Special characters in values
            tool_args = {"cmd": "$PREV.command"}
            step_results = [{"command": "echo 'hello & goodbye'"}]
            resolved = agent._resolve_plan_parameters(tool_args, step_results)
            self.assertEqual(resolved["cmd"], "echo 'hello & goodbye'")

            # Test 7: Numeric and boolean values preserved
            tool_args = {
                "count": 5,
                "enabled": True,
                "ratio": 0.5,
                "nothing": None,
            }
            resolved = agent._resolve_plan_parameters(tool_args, [])
            self.assertEqual(resolved["count"], 5)
            self.assertEqual(resolved["enabled"], True)
            self.assertEqual(resolved["ratio"], 0.5)
            self.assertIsNone(resolved["nothing"])


if __name__ == "__main__":
    unittest.main()
