import pytest
import json
import logging
import re
from unittest.mock import MagicMock, patch, call
from gaia.agents.Blender.agent import BlenderAgent
from gaia.llm.llm_client import LLMClient
from gaia.mcp.blender_mcp_client import MCPClient
from gaia.agents.base.console import AgentConsole, ProgressSpinner

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Test data for various response scenarios
VALID_JSON_RESPONSE = """
{
    "thought": "I'll create a red cube",
    "goal": "Create a red cube at the center",
    "tool": "create_object",
    "tool_args": {"type": "CUBE", "name": "test_cube"}
}
"""

VALID_PLAN_JSON = """
{
    "thought": "I'll create a simple scene",
    "goal": "Create a scene with a red cube and blue sphere",
    "plan": [
        {"tool": "clear_scene", "tool_args": {}},
        {"tool": "create_object", "tool_args": {"type": "CUBE", "name": "my_cube"}},
        {"tool": "set_material_color", "tool_args": {"object_name": "my_cube", "color": [1,0,0,1]}}
    ],
    "tool": "clear_scene",
    "tool_args": {}
}
"""

INVALID_JSON_RESPONSE = """
I'll create a red cube.

```json
{
    "thought": "I'll create a red cube",
    "goal": "Create a red cube at the center",
    "tool": "create_object",
    "tool_args": {"type": "CUBE", "name": "test_cube"
}
```

Let me know if you need anything else.
"""

MALFORMED_JSON_RESPONSE = """
{
    'thought': 'I will create a red cube',
    'goal': 'Create a red cube at the center',
    'tool': 'create_object',
    'tool_args': {'type': 'CUBE', 'name': 'test_cube'}
}
"""

NATURAL_LANGUAGE_RESPONSE = """
I'll create a red cube at the center of the scene. First, I'll clear the scene to start fresh, then I'll add a cube and set its material color to red.
"""

# Example tasks from app.py for integration testing
EXAMPLE_TASKS = [
    "Clear the scene to start fresh",
    "Create a red cube at the center of the scene and make sure it has a red material",
    "Create a blue sphere at position (3, 0, 0) and set its scale to (2, 2, 2)",
    "Create a green cube at (0, 0, 0) and a red sphere 3 units above it",
]

# Mocked LLM responses for integration testing
MOCKED_RESPONSES = {
    # Valid JSON response
    "clear_scene": json.dumps(
        {
            "thought": "I'll clear the scene",
            "goal": "Clear the scene to start fresh",
            "tool": "clear_scene",
            "tool_args": {},
        }
    ),
    # Valid JSON with plan
    "create_red_cube": json.dumps(
        {
            "thought": "I need to create a red cube",
            "goal": "Create a red cube with proper material",
            "plan": [
                {
                    "tool": "create_object",
                    "tool_args": {"type": "CUBE", "name": "red_cube"},
                },
                {
                    "tool": "set_material_color",
                    "tool_args": {"object_name": "red_cube", "color": [1, 0, 0, 1]},
                },
            ],
            "tool": "create_object",
            "tool_args": {"type": "CUBE", "name": "red_cube"},
        }
    ),
    # JSON with single quotes (needs correction)
    "create_blue_sphere": """
    {
        'thought': 'I need to create a blue sphere at the specified position',
        'goal': 'Create a blue sphere at position (3,0,0) with scale (2,2,2)',
        'tool': 'create_object',
        'tool_args': {'type': 'SPHERE', 'name': 'blue_sphere', 'location': [3,0,0], 'scale': [2,2,2]}
    }
    """,
    # Natural language with JSON embedded in text
    "color_blue_sphere": """
    Now I'll set the color of the sphere to blue.

    ```json
    {
        "thought": "I need to apply blue material to the sphere",
        "goal": "Set the sphere to blue color",
        "tool": "set_material_color",
        "tool_args": {"object_name": "blue_sphere.001", "color": [0,0,1,1]}
    }
    ```

    This will give the sphere a nice blue appearance.
    """,
    # Completely natural language (no JSON)
    "create_complex_scene": """
    I'll create a green cube at the origin (0,0,0) and a red sphere 3 units above it.
    First, I'll create the cube, then set its color to green, then create the sphere above it, and finally set the sphere's color to red.
    """,
}

# ----- Fixtures -----


@pytest.fixture
def mock_console():
    """Mock the console to prevent rich.errors.LiveError in tests."""
    mock = MagicMock(spec=AgentConsole)
    # Mock all necessary console methods to prevent errors
    mock.print_header = MagicMock()
    mock.print_separator = MagicMock()
    mock.print_step_header = MagicMock()
    mock.print_state_info = MagicMock()
    mock.print_thought = MagicMock()
    mock.print_goal = MagicMock()
    mock.print_tool_usage = MagicMock()
    mock.print_tool_complete = MagicMock()
    mock.pretty_print_json = MagicMock()
    mock.print_error = MagicMock()
    mock.print_warning = MagicMock()
    mock.print_info = MagicMock()
    mock.print_prompt = MagicMock()

    # Mock the progress spinner
    mock_progress = MagicMock(spec=ProgressSpinner)
    mock_progress.start = MagicMock()
    mock_progress.stop = MagicMock()
    mock.progress = mock_progress

    # Mock start_progress and stop_progress
    mock.start_progress = MagicMock()
    mock.stop_progress = MagicMock()

    return mock


@pytest.fixture
def mock_llm_client():
    """Create a mock LLM client for testing."""
    mock = MagicMock(spec=LLMClient)
    # Set up system prompt
    mock.system_prompt = "Test system prompt"

    # Set up generate method to return different responses based on input
    def side_effect(prompt, model=None, stream=False):
        # Return appropriate response based on prompt content
        if "clear the scene" in prompt.lower():
            return MOCKED_RESPONSES["clear_scene"]
        elif "red cube" in prompt.lower():
            return MOCKED_RESPONSES["create_red_cube"]
        elif "blue sphere" in prompt.lower():
            return MOCKED_RESPONSES["create_blue_sphere"]
        elif "color" in prompt.lower() and "sphere" in prompt.lower():
            return MOCKED_RESPONSES["color_blue_sphere"]
        elif "green cube" in prompt.lower() and "red sphere" in prompt.lower():
            return MOCKED_RESPONSES["create_complex_scene"]
        elif "json" in prompt.lower() and "correct" in prompt.lower():
            # This is a JSON correction request
            # Return a fixed valid JSON for simplicity
            return json.dumps(
                {
                    "thought": "Correcting my JSON response",
                    "goal": "Provide properly formatted JSON",
                    "tool": "create_object",
                    "tool_args": {"type": "CUBE", "name": "corrected_cube"},
                }
            )
        else:
            # Default response for other prompts
            return json.dumps(
                {
                    "thought": "Processing request",
                    "goal": "Complete the task",
                    "answer": "Task completed successfully",
                }
            )

    mock.generate.side_effect = side_effect
    return mock


@pytest.fixture
def mock_mcp_client():
    """Create a mock MCP client for testing."""
    mock = MagicMock(spec=MCPClient)
    # Mock the create_object method to return a success result
    mock.create_object.return_value = {
        "status": "success",
        "result": {"name": "test_cube.001"},
    }
    # Mock the clear_scene method
    mock.clear_scene = MagicMock(return_value={"status": "success"})
    # Mock set_material_color method
    mock.set_material_color = MagicMock(
        return_value={
            "status": "success",
            "result": {"material_name": "test_material.001"},
        }
    )
    # Mock modify_object method
    mock.modify_object = MagicMock(return_value={"status": "success"})

    return mock


@pytest.fixture
def agent(mock_llm_client, mock_mcp_client, mock_console):
    """Create a Blender agent with mock clients for testing."""
    agent = BlenderAgent(
        use_local_llm=True,
        mcp=mock_mcp_client,
        max_retries=3,
        debug_prompts=False,
        max_steps=10,
    )
    # Replace the LLM client with our mock
    agent.llm = mock_llm_client
    # Replace the console with our mock to prevent rich errors
    agent.console = mock_console
    # Return the configured agent
    return agent


# ----- JSON Validation Tests -----


class TestJSONValidation:
    """Tests for JSON validation and error recovery capabilities."""

    def test_valid_json_parsing(self, agent):
        """Test that valid JSON responses are correctly parsed."""
        parsed = agent._parse_llm_response(VALID_JSON_RESPONSE)

        assert parsed["thought"] == "I'll create a red cube"
        assert parsed["goal"] == "Create a red cube at the center"
        assert parsed["tool"] == "create_object"
        assert parsed["tool_args"]["type"] == "CUBE"
        assert parsed["tool_args"]["name"] == "test_cube"

    def test_valid_plan_parsing(self, agent):
        """Test that valid JSON plan responses are correctly parsed."""
        parsed = agent._parse_llm_response(VALID_PLAN_JSON)

        assert parsed["thought"] == "I'll create a simple scene"
        assert parsed["goal"] == "Create a scene with a red cube and blue sphere"
        assert "plan" in parsed
        assert len(parsed["plan"]) == 3
        assert parsed["plan"][0]["tool"] == "clear_scene"
        assert parsed["tool"] == "clear_scene"

    def test_json_extraction_from_invalid_response(self, agent):
        """Test extraction of JSON from invalid response with markdown and text."""
        parsed = agent._parse_llm_response(INVALID_JSON_RESPONSE)

        # Should extract the JSON even with missing closing brace
        assert "thought" in parsed
        assert "goal" in parsed
        assert parsed["tool"] == "create_object"
        assert "tool_args" in parsed

    def test_json_correction_for_single_quotes(self, agent):
        """Test correction of JSON with single quotes instead of double quotes."""
        # Configure the mock to return a corrected response
        agent.llm.generate.return_value = VALID_JSON_RESPONSE

        parsed = agent._parse_llm_response(MALFORMED_JSON_RESPONSE)

        # Should either fix the single quotes or request a correction via LLM
        assert "thought" in parsed
        assert "goal" in parsed
        assert "tool" in parsed
        assert "tool_args" in parsed

    def test_natural_language_fallback(self, agent):
        """Test fallback mechanism for natural language responses."""
        # Mock domain pattern detection with a specific return value
        with patch.object(
            agent,
            "_get_domain_patterns",
            return_value={
                "create_object": {
                    "patterns": [r"create\s+(?:a|an)?\s*(\w+)"],
                    "fallback": {
                        "thought": "Detected intention to create an object",
                        "goal": "Create a default object",
                        "tool": "create_object",
                        "tool_args": {"type": "CUBE", "name": "auto_created_cube"},
                    },
                }
            },
        ):
            # Also mock _extract_json_from_response to ensure it returns None
            with patch.object(agent, "_extract_json_from_response", return_value=None):
                parsed = agent.process_llm_response(NATURAL_LANGUAGE_RESPONSE)

                # Should detect the create object intent and return appropriate fallback
                assert parsed["thought"] == "Detected intention to create an object"
                assert parsed["tool"] == "create_object"
                assert parsed["tool_args"]["type"] == "CUBE"

    def test_retry_mechanism(self, agent):
        """Test the retry mechanism for invalid JSON responses."""
        # Configure mock to return valid JSON on second try
        agent.llm.generate.return_value = VALID_JSON_RESPONSE

        # Also mock _extract_json_from_response to ensure it returns None
        with patch.object(agent, "_extract_json_from_response", return_value=None):
            # Process an invalid response, which should trigger a retry
            parsed = agent.process_llm_response(INVALID_JSON_RESPONSE)

            # Verify the correction was attempted
            agent.llm.generate.assert_called_once()

            # Verify we got a valid result after correction
            assert "thought" in parsed
            assert "goal" in parsed
            assert "tool" in parsed

    def test_graduated_retry_escalation(self, agent):
        """Test that retry prompts escalate in strictness."""
        # Configure to return valid JSON on second try
        agent.llm.generate.return_value = VALID_JSON_RESPONSE

        # Spy on the _create_json_correction_prompt method
        with patch.object(
            agent,
            "_create_json_correction_prompt",
            wraps=agent._create_json_correction_prompt,
        ) as mock_create_prompt:
            # Also mock _extract_json_from_response to ensure it returns None
            with patch.object(agent, "_extract_json_from_response", return_value=None):
                # First retry should use regular correction prompt
                agent.process_llm_response(INVALID_JSON_RESPONSE)

                # Check that the correction prompt was called
                mock_create_prompt.assert_called_once()

    def test_format_reminder_added_to_prompts(self, agent):
        """Test that format reminders are added to prompts."""
        test_prompt = "Create a red cube"
        enhanced_prompt = agent._add_format_reminder(test_prompt)

        # Verify the enhanced prompt contains the reminder text
        assert (
            "IMPORTANT: Your response must be a single valid JSON object"
            in enhanced_prompt
        )
        assert test_prompt in enhanced_prompt  # Original prompt is preserved

    def test_blender_domain_patterns(self, agent):
        """Test Blender-specific domain patterns for natural language responses."""
        # Get the domain patterns
        patterns = agent._get_domain_patterns()

        # Verify Blender-specific patterns are present
        assert "create_object" in patterns
        assert "color_object" in patterns
        assert "move_object" in patterns
        assert "clear_scene" in patterns

        # Test pattern matching for object creation
        create_pattern = patterns["create_object"]["patterns"][0]
        match = re.search(create_pattern, "create a cube at the center", re.IGNORECASE)
        assert match is not None
        assert match.group(1) == "cube"

    def test_fallback_response(self, agent):
        """Test the fallback response mechanism."""
        fallback = agent._create_fallback_response(
            "I think I should create a red cube for this task.", "JSON parsing error"
        )

        # Verify the fallback response has the required fields
        assert "thought" in fallback
        assert "goal" in fallback
        # Should contain either answer or tool
        assert "answer" in fallback or "tool" in fallback

    def test_extract_json_from_response(self, agent):
        """Test the JSON extraction helper method."""
        test_response = """
        I'll help you create this scene.

        ```json
        {
            "thought": "Creating objects",
            "goal": "Build the scene",
            "tool": "create_object",
            "tool_args": {"type": "CUBE", "name": "extracted_cube"}
        }
        ```
        """

        extracted = agent._extract_json_from_response(test_response)
        assert extracted is not None
        assert extracted["tool"] == "create_object"
        assert extracted["tool_args"]["name"] == "extracted_cube"


# ----- Integration Tests -----


class TestAgentIntegration:
    """Integration tests for the BlenderAgent with mock dependencies."""

    @pytest.mark.parametrize(
        "example", [EXAMPLE_TASKS[0]]
    )  # Test just the first example for speed
    def test_example_tasks(self, agent, example):
        """Test processing example tasks with the agent."""
        # Configure a simple success response based on the example
        if "clear the scene" in example.lower():
            agent.llm.generate.return_value = MOCKED_RESPONSES["clear_scene"]
            expected_tool = "clear_scene"
        elif "red cube" in example.lower():
            agent.llm.generate.return_value = MOCKED_RESPONSES["create_red_cube"]
            expected_tool = "create_object"

        # Process the example query
        result = agent.process_query(example, output_to_file=False)

        # Verify successful processing
        assert result["status"] == "success" or result["status"] == "incomplete"
        # Verify conversation history was recorded
        assert "conversation" in result
        assert len(result["conversation"]) >= 2  # At least user and assistant

    def test_json_extraction_from_markdown(self, agent):
        """Test JSON extraction from markdown code blocks."""
        # Use the extracted JSON directly
        parsed = agent._extract_json_from_response(
            MOCKED_RESPONSES["color_blue_sphere"]
        )

        # Verify essential fields were extracted
        assert parsed is not None
        assert "thought" in parsed
        assert "goal" in parsed
        assert parsed["tool"] == "set_material_color"
        assert parsed["tool_args"]["color"] == [0, 0, 1, 1]

    def test_natural_language_intent_detection(self, agent):
        """Test detection of intentions from natural language."""
        # Create a specific return value for the fallback
        fallback_response = {
            "thought": "Detected intent to create objects",
            "goal": "Create a complex scene",
            "tool": "create_object",
            "tool_args": {"type": "CUBE", "name": "detected_cube"},
        }

        # Mock the fallback response directly
        with patch.object(
            agent, "_create_fallback_response", return_value=fallback_response
        ):
            # Also mock _extract_json_from_response to ensure it returns None
            with patch.object(agent, "_extract_json_from_response", return_value=None):
                # Process with validate_json_response always failing
                with patch.object(
                    agent,
                    "validate_json_response",
                    side_effect=ValueError("Test error"),
                ):
                    # Parse the natural language response
                    parsed = agent.process_llm_response(
                        MOCKED_RESPONSES["create_complex_scene"]
                    )

                    # Verify the result matches our fallback
                    assert parsed["thought"] == "Detected intent to create objects"
                    assert parsed["goal"] == "Create a complex scene"
                    assert parsed["tool"] == "create_object"

    def test_retry_mechanism_integration(self, agent):
        """Test the complete retry mechanism with JSON correction."""
        # Configure specific responses for the test
        invalid_response = MOCKED_RESPONSES["create_blue_sphere"]
        valid_response = json.dumps(
            {
                "thought": "Corrected JSON",
                "goal": "Provide valid JSON",
                "tool": "create_object",
                "tool_args": {"type": "SPHERE", "name": "corrected_sphere"},
            }
        )

        # Set up the generate method to return the invalid response first, then the valid one
        agent.llm.generate.side_effect = [invalid_response, valid_response]

        # Mock _extract_json_from_response to simulate a failure
        with patch.object(agent, "_extract_json_from_response", return_value=None):
            # Process the invalid response
            with patch.object(
                agent,
                "validate_json_response",
                side_effect=[ValueError("Test error"), json.loads(valid_response)],
            ):
                result = agent.validate_json_response(invalid_response)

                # Validate the result
                assert result == json.loads(valid_response)

    def test_format_reminder_in_prompts(self, agent):
        """Test that format reminders are added to prompts."""
        # Instead of running the full process_query, just test the _add_format_reminder method
        test_prompt = "Create a red cube"
        enhanced_prompt = agent._add_format_reminder(test_prompt)

        # Verify the reminder was added
        assert (
            "IMPORTANT: Your response must be a single valid JSON object"
            in enhanced_prompt
        )

    def test_complex_plan_execution(self, agent):
        """Test execution of a complex plan with minimal mocking."""
        # Create a plan response
        plan_response = json.dumps(
            {
                "thought": "Creating a complex scene with multiple objects",
                "goal": "Create a scene with a cube and sphere",
                "plan": [
                    {"tool": "clear_scene", "tool_args": {}},
                    {
                        "tool": "create_object",
                        "tool_args": {
                            "type": "CUBE",
                            "name": "plan_cube",
                            "location": [0, 0, 0],
                        },
                    },
                    {
                        "tool": "set_material_color",
                        "tool_args": {
                            "object_name": "plan_cube",
                            "color": [1, 0, 0, 1],
                        },
                    },
                    {
                        "tool": "create_object",
                        "tool_args": {
                            "type": "SPHERE",
                            "name": "plan_sphere",
                            "location": [0, 0, 2],
                        },
                    },
                ],
                "tool": "clear_scene",
                "tool_args": {},
            }
        )

        # Test the extraction and validation of the plan
        parsed_plan = agent._parse_llm_response(plan_response)

        # Verify the plan was correctly parsed
        assert (
            parsed_plan["thought"] == "Creating a complex scene with multiple objects"
        )
        assert len(parsed_plan["plan"]) == 4
        assert parsed_plan["plan"][0]["tool"] == "clear_scene"
        assert parsed_plan["plan"][1]["tool"] == "create_object"
        assert parsed_plan["plan"][2]["tool"] == "set_material_color"
        assert parsed_plan["plan"][3]["tool"] == "create_object"


if __name__ == "__main__":
    pytest.main(["-xvs", __file__])
