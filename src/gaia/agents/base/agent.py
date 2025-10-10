# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Generic Agent class for building domain-specific agents.
"""

# Standard library imports
import abc
import datetime
import inspect
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from gaia.agents.base.console import AgentConsole, SilentConsole
from gaia.agents.base.tools import _TOOL_REGISTRY

# First-party imports
from gaia.chat.sdk import ChatConfig, ChatSDK

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Agent(abc.ABC):
    """
    Base Agent class that provides core functionality for domain-specific agents.

    The Agent class handles the core conversation loop, tool execution, and LLM
    interaction patterns. It provides:
    - Conversation management with an LLM
    - Tool registration and execution framework
    - JSON response parsing and validation
    - Error handling and recovery
    - State management for multi-step plans
    - Output formatting and file writing
    - Configurable prompt display for debugging

    Key Parameters:
        debug: Enable general debug output and logging
        show_prompts: Display prompts sent to LLM (useful for debugging prompts)
        debug_prompts: Include prompts in conversation history for analysis
        streaming: Enable real-time streaming of LLM responses
        silent_mode: Suppress all console output for JSON-only usage
    """

    # Define state constants
    STATE_PLANNING = "PLANNING"
    STATE_EXECUTING_PLAN = "EXECUTING_PLAN"
    STATE_DIRECT_EXECUTION = "DIRECT_EXECUTION"
    STATE_ERROR_RECOVERY = "ERROR_RECOVERY"
    STATE_COMPLETION = "COMPLETION"

    # Define tools that can execute directly without requiring a plan
    # Subclasses can override this to specify domain-specific simple tools
    SIMPLE_TOOLS = []

    def __init__(
        self,
        use_claude: bool = False,
        use_chatgpt: bool = False,
        claude_model: str = "claude-sonnet-4-20250514",
        model_id: str = None,
        max_steps: int = 5,
        debug_prompts: bool = False,
        show_prompts: bool = False,
        output_dir: str = None,
        streaming: bool = False,
        show_stats: bool = True,
        silent_mode: bool = False,
        debug: bool = False,
    ):
        """
        Initialize the Agent with LLM client.

        Args:
            use_claude: If True, uses Claude API (default: False)
            use_chatgpt: If True, uses ChatGPT/OpenAI API (default: False)
            claude_model: Claude model to use when use_claude=True (default: "claude-sonnet-4-20250514")
            model_id: The ID of the model to use with LLM server (default for local)
            max_steps: Maximum number of steps the agent can take before terminating
            debug_prompts: If True, includes prompts in the conversation history
            show_prompts: If True, displays prompts sent to LLM in console (default: False)
            output_dir: Directory for storing JSON output files (default: current directory)
            streaming: If True, enables real-time streaming of LLM responses (default: False)
            show_stats: If True, displays LLM performance stats after each response (default: True)
            silent_mode: If True, suppresses all console output for JSON-only usage (default: False)
            debug: If True, enables debug output for troubleshooting (default: False)

        Note: Uses local LLM server by default unless use_claude or use_chatgpt is True.
        """
        self.error_history = []  # Store error history for learning
        self.max_steps = max_steps
        self.debug_prompts = debug_prompts
        self.show_prompts = show_prompts  # Separate flag for displaying prompts
        self.output_dir = output_dir if output_dir else os.getcwd()
        self.streaming = streaming
        self.show_stats = show_stats
        self.silent_mode = silent_mode
        self.debug = debug
        self.last_result = None  # Store the most recent result

        # Initialize state management
        self.execution_state = self.STATE_PLANNING
        self.current_plan = None
        self.current_step = 0
        self.total_plan_steps = 0

        # Initialize the console for display
        self.console = self._create_console()

        # Initialize LLM client for local model
        self.system_prompt = self._get_system_prompt()

        # Register tools for this agent
        self._register_tools()

        # Update system prompt with available tools
        tools_description = self._format_tools_for_prompt()
        self.system_prompt += f"\n\n==== AVAILABLE TOOLS ====\n{tools_description}\n\n"

        # Initialize ChatSDK with proper configuration
        # Note: We don't set system_prompt in config, we pass it per request
        chat_config = ChatConfig(
            model=model_id or "Qwen2.5-0.5B-Instruct-CPU",
            use_claude=use_claude,
            use_chatgpt=use_chatgpt,
            claude_model=claude_model,
            show_stats=show_stats,
            max_history_length=20,  # Keep more history for agent conversations
        )
        self.chat = ChatSDK(chat_config)
        self.model_id = model_id

        # Print system prompt if show_prompts is enabled
        # Debug: Check the actual value of show_prompts
        if self.debug:
            logger.debug(
                f"show_prompts={self.show_prompts}, debug={self.debug}, will show prompt: {self.show_prompts}"
            )

        if self.show_prompts:
            self.console.print_prompt(self.system_prompt, "Initial System Prompt")

    @abc.abstractmethod
    def _get_system_prompt(self) -> str:
        """
        Generate the system prompt for the agent.
        Subclasses must implement this to provide domain-specific prompts.
        """
        raise NotImplementedError("Subclasses must implement _get_system_prompt")

    @abc.abstractmethod
    def _create_console(self):
        """
        Create and return a console output handler.
        Returns SilentConsole if in silent_mode, otherwise AgentConsole.
        Subclasses should override this to provide domain-specific console output.
        """
        if self.silent_mode:
            return SilentConsole()
        return AgentConsole()

    @abc.abstractmethod
    def _register_tools(self):
        """
        Register all domain-specific tools for the agent.
        Subclasses must implement this method.
        """
        raise NotImplementedError("Subclasses must implement _register_tools")

    def _format_tools_for_prompt(self) -> str:
        """Format the registered tools into a string for the prompt."""
        tool_descriptions = []

        for name, tool_info in _TOOL_REGISTRY.items():
            params_str = ", ".join(
                [
                    f"{param_name}{'' if param_info['required'] else '?'}: {param_info['type']}"
                    for param_name, param_info in tool_info["parameters"].items()
                ]
            )

            description = tool_info["description"].strip()
            tool_descriptions.append(f"- {name}({params_str}): {description}")

        return "\n".join(tool_descriptions)

    def list_tools(self, verbose: bool = True) -> None:
        """
        Display all tools registered for this agent with their parameters and descriptions.

        Args:
            verbose: If True, displays full descriptions and parameter details. If False, shows a compact list.
        """
        self.console.print_header(f"🛠️ Registered Tools for {self.__class__.__name__}")
        self.console.print_separator()

        for name, tool_info in _TOOL_REGISTRY.items():
            # Format parameters
            params = []
            for param_name, param_info in tool_info["parameters"].items():
                required = param_info.get("required", False)
                param_type = param_info.get("type", "Any")
                default = param_info.get("default", None)

                if required:
                    params.append(f"{param_name}: {param_type}")
                else:
                    default_str = f"={default}" if default is not None else "=None"
                    params.append(f"{param_name}: {param_type}{default_str}")

            params_str = ", ".join(params)

            # Get description
            if verbose:
                description = tool_info["description"]
            else:
                description = (
                    tool_info["description"].split("\n")[0]
                    if tool_info["description"]
                    else "No description"
                )

            # Print tool information
            self.console.print_tool_info(name, params_str, description)

        self.console.print_separator()

        return None

    def _extract_json_from_response(self, response: str) -> Optional[Dict[str, Any]]:
        """
        Apply multiple extraction strategies to find valid JSON in the response.

        Args:
            response: The raw response from the LLM

        Returns:
            Extracted JSON dictionary or None if extraction failed
        """
        # Strategy 1: Extract JSON from code blocks with various patterns
        json_patterns = [
            r"```(?:json)?\s*(.*?)\s*```",  # Standard code block
            r"`json\s*(.*?)\s*`",  # Single backtick with json tag
            r"<json>\s*(.*?)\s*</json>",  # XML-style tags
            r'\{\s*"thought".*\}',  # Direct JSON object starting with thought
            r'\{\s*"tool".*\}',  # Direct JSON object starting with tool
            r'\{\s*"answer".*\}',  # Direct JSON object starting with answer
        ]

        for pattern in json_patterns:
            matches = re.findall(pattern, response, re.DOTALL)
            for match in matches:
                try:
                    result = json.loads(match)
                    # Ensure tool_args exists if tool is present
                    if "tool" in result and "tool_args" not in result:
                        result["tool_args"] = {}
                    logger.debug(f"Successfully extracted JSON with pattern {pattern}")
                    return result
                except json.JSONDecodeError:
                    continue

        # Strategy 2: Try to fix common JSON format issues and parse again
        fixed_response = response
        # Replace single quotes with double quotes
        fixed_response = re.sub(r"'([^']*)':", r'"\1":', fixed_response)
        # Fix trailing commas in objects and arrays
        fixed_response = re.sub(r",\s*}", "}", fixed_response)
        fixed_response = re.sub(r",\s*]", "]", fixed_response)

        try:
            # Try to find JSON object in the fixed text
            match = re.search(r"(\{.*\})", fixed_response, re.DOTALL)
            if match:
                result = json.loads(match.group(1))
                logger.debug("Successfully extracted JSON after fixing format issues")
                return result
        except json.JSONDecodeError as e:
            logger.debug(f"Regex extraction failed to parse JSON: {e}")

        return None

    def validate_json_response(self, response_text: str) -> Dict[str, Any]:
        """
        Validates and attempts to fix JSON responses from the LLM.

        Attempts the following fixes in order:
        1. Parse as-is if valid JSON
        2. Extract JSON from code blocks
        3. Truncate after first complete JSON object
        4. Fix common JSON syntax errors
        5. Extract JSON-like content using regex

        Args:
            response_text: The response string from the LLM

        Returns:
            A dictionary containing the parsed JSON if valid

        Raises:
            ValueError: If the response cannot be parsed as JSON or is missing required fields
        """
        original_response = response_text
        json_was_modified = False

        # Step 1: Try to parse as-is
        try:
            json_response = json.loads(response_text)
            logger.debug("[JSON] Successfully parsed response without modifications")
        except json.JSONDecodeError as initial_error:
            # Step 2: Try to extract from code blocks
            json_match = re.search(
                r"```(?:json)?\s*({.*?})\s*```", response_text, re.DOTALL
            )
            if json_match:
                try:
                    response_text = json_match.group(1)
                    json_response = json.loads(response_text)
                    json_was_modified = True
                    logger.warning("[JSON] Extracted JSON from code block")
                except json.JSONDecodeError as e:
                    logger.debug(f"[JSON] Code block extraction failed: {e}")

            # Step 3: Try to find and extract first complete JSON object
            if not json_was_modified:
                # Find the first '{' and try to match brackets
                start_idx = response_text.find("{")
                if start_idx >= 0:
                    bracket_count = 0
                    in_string = False
                    escape_next = False

                    for i, char in enumerate(response_text[start_idx:], start_idx):
                        if escape_next:
                            escape_next = False
                            continue
                        if char == "\\":
                            escape_next = True
                            continue
                        if char == '"' and not escape_next:
                            in_string = not in_string
                        if not in_string:
                            if char == "{":
                                bracket_count += 1
                            elif char == "}":
                                bracket_count -= 1
                                if bracket_count == 0:
                                    # Found complete JSON object
                                    try:
                                        truncated = response_text[start_idx : i + 1]
                                        json_response = json.loads(truncated)
                                        json_was_modified = True
                                        logger.warning(
                                            f"[JSON] Truncated response after first complete JSON object (removed {len(response_text) - i - 1} chars)"
                                        )
                                        response_text = truncated
                                        break
                                    except json.JSONDecodeError:
                                        logger.debug(
                                            "[JSON] Truncated text is not valid JSON, trying next bracket pair"
                                        )
                                        continue

            # Step 4: Try to fix common JSON errors
            if not json_was_modified:
                fixed_text = response_text

                # Remove trailing commas
                fixed_text = re.sub(r",\s*}", "}", fixed_text)
                fixed_text = re.sub(r",\s*]", "]", fixed_text)

                # Fix single quotes to double quotes (carefully)
                if "'" in fixed_text and '"' not in fixed_text:
                    fixed_text = fixed_text.replace("'", '"')

                # Remove any text before first '{' or '['
                json_start = min(
                    fixed_text.find("{") if "{" in fixed_text else len(fixed_text),
                    fixed_text.find("[") if "[" in fixed_text else len(fixed_text),
                )
                if json_start > 0 and json_start < len(fixed_text):
                    fixed_text = fixed_text[json_start:]

                # Try to parse the fixed text
                if fixed_text != response_text:
                    try:
                        json_response = json.loads(fixed_text)
                        json_was_modified = True
                        logger.warning("[JSON] Applied automatic JSON fixes")
                        response_text = fixed_text
                    except json.JSONDecodeError as e:
                        logger.debug(f"[JSON] Auto-fix failed: {e}")

            # If still no valid JSON, raise the original error
            if not json_was_modified:
                raise ValueError(
                    f"Failed to parse response as JSON: {str(initial_error)}"
                )

        # Log warning if JSON was modified
        if json_was_modified:
            logger.warning(
                f"[JSON] Response was modified to extract valid JSON. Original length: {len(original_response)}, Fixed length: {len(response_text)}"
            )

        # Validate required fields
        if "answer" in json_response:
            required_fields = ["thought", "goal", "answer"]
        elif "tool" in json_response:
            required_fields = ["thought", "goal", "tool", "tool_args"]
        else:
            required_fields = ["thought", "goal", "plan"]

        missing_fields = [
            field for field in required_fields if field not in json_response
        ]
        if missing_fields:
            raise ValueError(
                f"Response is missing required fields: {', '.join(missing_fields)}"
            )

        return json_response

    def _parse_llm_response(self, response: str) -> Dict[str, Any]:
        """
        Parse the LLM response to extract tool calls or final answers.

        Args:
            response: The raw response from the LLM

        Returns:
            Parsed response as a dictionary
        """
        # Check for empty or whitespace-only responses
        if not response or not response.strip():
            logger.warning("Empty LLM response received")
            self.error_history.append("Empty LLM response")
            return {
                "thought": "LLM returned empty response",
                "goal": "Handle empty response error",
                "answer": "I apologize, but I received an empty response from the language model. Please try again.",
            }

        # First try to validate and process the response using our robust JSON validation
        try:
            validated_response = self.validate_json_response(response)
            logger.debug(
                f"[PARSE] Successfully validated response: {str(validated_response)[:200]}..."
            )
            return validated_response
        except Exception as e:
            # If validation fails, fall back to the original parsing logic
            logger.warning(
                f"[PARSE] JSON validation failed, falling back to original parsing: {str(e)}"
            )
            # Continue with existing parsing logic

        # Clean up the response
        response = response.strip()
        logger.debug(f"[PARSE] Cleaned response length: {len(response)}")

        # Try more aggressive JSON extraction methods
        extracted_json = self._extract_json_from_response(response)
        if extracted_json:
            logger.debug(
                f"Successfully extracted JSON with advanced methods: {extracted_json}"
            )
            return extracted_json

        # If no code blocks or JSON parsing failed, try to parse the raw response
        try:
            logger.debug("Attempting to parse raw response as JSON")
            result = json.loads(response)
            # Ensure tool_args exists if tool is present
            if "tool" in result and "tool_args" not in result:
                result["tool_args"] = {}
            # Ensure plan is included in conversation if present
            if "plan" in result:
                logger.debug(f"Found plan in response: {result['plan']}")
            logger.debug(f"Successfully parsed raw response as JSON: {result}")
            return result
        except json.JSONDecodeError as e:
            error_msg = f"[PARSE] Failed to parse raw response as JSON: {str(e)}, response length: {len(response)}, preview: {response[:100]}..."
            logger.error(error_msg)
            self.error_history.append(error_msg)

        # If JSON parsing fails, try to extract thought/tool/tool_args using regex
        logger.debug("Attempting to extract fields using regex")
        thought_match = re.search(r'"thought":\s*"([^"]*)"', response)
        tool_match = re.search(r'"tool":\s*"([^"]*)"', response)
        answer_match = re.search(r'"answer":\s*"([^"]*)"', response)
        plan_match = re.search(r'"plan":\s*(\[.*?\])', response, re.DOTALL)

        if answer_match:
            result = {
                "thought": thought_match.group(1) if thought_match else "",
                "goal": "what was achieved",
                "answer": answer_match.group(1),
            }
            logger.debug(f"Extracted answer using regex: {result}")
            return result

        if tool_match:
            tool_args_match = re.search(r'"tool_args":\s*(\{.*\})', response, re.DOTALL)
            tool_args = {}

            if tool_args_match:
                try:
                    tool_args = json.loads(tool_args_match.group(1))
                except json.JSONDecodeError as e:
                    error_msg = f"Failed to parse tool_args JSON: {str(e)}, content: {tool_args_match.group(1)[:100]}..."
                    logger.error(error_msg)
                    self.error_history.append(error_msg)

            result = {
                "thought": thought_match.group(1) if thought_match else "",
                "goal": "clear statement of what you're trying to achieve",
                "tool": tool_match.group(1),
                "tool_args": tool_args,
            }

            # Add plan if found
            if plan_match:
                try:
                    result["plan"] = json.loads(plan_match.group(1))
                    logger.debug(f"Extracted plan using regex: {result['plan']}")
                except json.JSONDecodeError as e:
                    error_msg = f"Failed to parse plan JSON: {str(e)}, content: {plan_match.group(1)[:100]}..."
                    logger.error(error_msg)
                    self.error_history.append(error_msg)

            logger.debug(f"Extracted tool call using regex: {result}")
            return result

        # Try to match simple key-value patterns for object names (like ': "my_cube"')
        obj_name_match = re.search(
            r'["\':]?\s*["\'"]?([a-zA-Z0-9_\.]+)["\'"]?', response
        )
        if obj_name_match:
            object_name = obj_name_match.group(1)
            # If it looks like an object name and not just a random word
            if "." in object_name or "_" in object_name:
                logger.debug(f"Found potential object name: {object_name}")
                return {
                    "thought": "Extracted object name",
                    "goal": "Use the object name",
                    "answer": object_name,
                }

        # If all else fails, treat the entire response as the answer
        error_msg = f"[PARSE] All parsing attempts failed, treating entire response as answer. Length: {len(response)}, preview: {response[:100]}..."
        logger.error(error_msg)
        self.error_history.append(error_msg)

        # If response is still empty after all processing, provide a meaningful fallback
        if not response.strip():
            logger.error(
                "[PARSE] Final fallback: response is empty, returning error message"
            )
            return {
                "thought": "All parsing failed and response is empty",
                "goal": "Handle complete parsing failure",
                "answer": "I encountered an issue processing the response. The language model returned an empty or invalid response.",
            }

        return {"thought": "", "goal": "what was achieved", "answer": response}

    def _execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> Any:
        """
        Execute a tool by name with the provided arguments.

        Args:
            tool_name: Name of the tool to execute
            tool_args: Arguments to pass to the tool

        Returns:
            Result of the tool execution
        """
        logger.debug(f"Executing tool {tool_name} with args: {tool_args}")

        if tool_name not in _TOOL_REGISTRY:
            logger.error(f"Tool '{tool_name}' not found in registry")
            return {"status": "error", "error": f"Tool '{tool_name}' not found"}

        tool = _TOOL_REGISTRY[tool_name]["function"]
        sig = inspect.signature(tool)

        # Get required parameters (those without defaults)
        required_args = {
            name: param
            for name, param in sig.parameters.items()
            if param.default == inspect.Parameter.empty and name != "return"
        }

        # Check for missing required arguments
        missing_args = [arg for arg in required_args if arg not in tool_args]
        if missing_args:
            error_msg = (
                f"Missing required arguments for {tool_name}: {', '.join(missing_args)}"
            )
            logger.error(error_msg)
            return {"status": "error", "error": error_msg}

        try:
            result = tool(**tool_args)
            logger.debug(f"Tool execution result: {result}")
            return result
        except Exception as e:
            logger.error(f"Error executing tool {tool_name}: {str(e)}")
            self.error_history.append(str(e))
            return {"status": "error", "error": str(e)}

    def _write_json_to_file(self, data: Dict[str, Any], filename: str = None) -> str:
        """
        Write JSON data to a file and return the absolute path.

        Args:
            data: Dictionary data to write as JSON
            filename: Optional filename, if None a timestamped name will be generated

        Returns:
            Absolute path to the saved file
        """
        # Ensure output directory exists
        os.makedirs(self.output_dir, exist_ok=True)

        # Generate filename if not provided
        if not filename:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"agent_output_{timestamp}.json"

        # Ensure filename has .json extension
        if not filename.endswith(".json"):
            filename += ".json"

        # Create absolute path
        file_path = os.path.join(self.output_dir, filename)

        # Write JSON data to file
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        return os.path.abspath(file_path)

    def _handle_large_tool_result(
        self, tool_result: Any, conversation: List[Dict[str, Any]]
    ) -> Any:
        """
        Handle large tool results by truncating them if necessary.

        Args:
            tool_result: The result from tool execution
            conversation: The conversation list to append to

        Returns:
            The truncated result or original if within limits
        """

        truncated_result = tool_result
        if isinstance(tool_result, (dict, list)):
            result_str = json.dumps(tool_result)
            if len(result_str) > 3000:  # Threshold for truncation
                truncated_str = self._truncate_large_content(
                    tool_result, max_chars=2000
                )
                try:
                    truncated_result = json.loads(truncated_str)
                except json.JSONDecodeError:
                    # If truncated string isn't valid JSON, use it as-is
                    truncated_result = truncated_str
                # Notify user about truncation
                self.console.print_info(
                    f"Note: Large result ({len(result_str)} chars) truncated for LLM context"
                )
                if self.debug:
                    print(f"[DEBUG] Tool result truncated from {len(result_str)} chars")

        # Add to conversation
        conversation.append({"role": "system", "content": truncated_result})
        return truncated_result

    def _truncate_large_content(self, content: Any, max_chars: int = 2000) -> str:
        """
        Truncate large content to prevent overwhelming the LLM.
        """

        # Convert to string (use compact JSON first to check size)
        if isinstance(content, (dict, list)):
            compact_str = json.dumps(content)
            # Only use indented format if we need to truncate anyway
            content_str = (
                json.dumps(content, indent=2)
                if len(compact_str) > max_chars
                else compact_str
            )
        else:
            content_str = str(content)

        # Return as-is if within limits
        if len(content_str) <= max_chars:
            return content_str

        # For Jira responses, keep first 3 issues
        if (
            isinstance(content, dict)
            and "issues" in content
            and isinstance(content["issues"], list)
        ):
            truncated = {
                **content,
                "issues": content["issues"][:3],
                "truncated": True,
                "total": len(content["issues"]),
            }
            return json.dumps(truncated, indent=2)[:max_chars]

        # For lists, keep first 3 items
        if isinstance(content, list):
            truncated = (
                content[:3] + [{"truncated": f"{len(content) - 3} more"}]
                if len(content) > 3
                else content
            )
            return json.dumps(truncated, indent=2)[:max_chars]

        # Simple truncation
        half = max_chars // 2 - 20
        return f"{content_str[:half]}\n...[truncated]...\n{content_str[-half:]}"

    def _add_format_reminder(self) -> str:
        """
        Add JSON format reminders to reinforce proper response structure.

        Returns:
            Format reminder string for prompts
        """
        # Short reminder for standard prompts
        reminder = """
IMPORTANT: Your response must be a single valid JSON object.
Use double quotes for keys and string values. Ensure all fields are present.
DO NOT include text, markdown, or explanations outside the JSON structure.
NEVER wrap your response in code blocks or backticks.
        """

        return reminder

    def process_query(
        self,
        user_input: str,
        max_steps: int = None,
        output_to_file: bool = True,
        filename: str = None,
    ) -> Dict[str, Any]:
        """
        Process a user query and execute the necessary tools.
        Displays each step as it's being generated in real-time.

        Args:
            user_input: User's query or request
            max_steps: Maximum number of steps to take in the conversation (overrides class default if provided)
            output_to_file: If True, write results to a JSON file
            filename: Optional filename for output, if None a timestamped name will be generated

        Returns:
            Dict containing the final result and operation details
        """
        import time

        start_time = time.time()  # Track query processing start time

        logger.debug(f"Processing query: {user_input}")
        conversation = []
        # Build messages array for chat completions (user/assistant only, system passed separately)
        messages = []
        steps_taken = 0
        final_answer = None
        error_count = 0
        last_tool_call = None  # Track the last tool call to prevent loops
        last_error = None  # Track the last error to handle it properly
        previous_outputs = []  # Track previous tool outputs

        # Reset state management
        self.execution_state = self.STATE_PLANNING
        self.current_plan = None
        self.current_step = 0
        self.total_plan_steps = 0

        # Add user query to the conversation history
        conversation.append({"role": "user", "content": user_input})
        messages.append({"role": "user", "content": user_input})

        # Use provided max_steps or fall back to class default
        steps_limit = max_steps if max_steps is not None else self.max_steps

        # Print initial message with max steps info
        self.console.print_processing_start(user_input, steps_limit)
        logger.debug(f"Using max_steps: {steps_limit}")

        prompt = f"User request: {user_input}\n\n"

        # Only add planning reminder in PLANNING state
        if self.execution_state == self.STATE_PLANNING:
            prompt += (
                "IMPORTANT: ALWAYS BEGIN WITH A PLAN before executing any tools.\n"
                "First create a detailed plan with all necessary steps, then execute the first step.\n"
                "When creating a plan with multiple steps:\n"
                "   1. ALWAYS follow the plan in the correct order, starting with the FIRST step.\n"
                "   2. Include both a plan and a 'tool' field, the 'tool' field MUST match the tool in the first step of the plan.\n"
                "   3. Create plans with clear, executable steps that include both the tool name and the exact arguments for each step.\n"
            )

        # Apply format reminders to the prompt
        prompt += self._add_format_reminder()

        logger.debug(f"Input prompt: {prompt[:200]}...")

        # Process the query in steps, allowing for multiple tool usages
        while steps_taken < steps_limit and final_answer is None:
            # Build the next prompt based on current state (this is for fallback mode only)
            # In chat mode, we'll just add to messages array
            steps_taken += 1
            logger.debug(f"Step {steps_taken}/{steps_limit}")

            # Display current step
            self.console.print_step_header(steps_taken, steps_limit)

            # Skip automatic finalization for single-step plans - always request proper final answer

            # If we're executing a plan, we might not need to query the LLM again
            if (
                self.execution_state == self.STATE_EXECUTING_PLAN
                and self.current_step < self.total_plan_steps
            ):
                logger.debug(
                    f"Executing plan step {self.current_step + 1}/{self.total_plan_steps}"
                )
                self.console.print_state_info(
                    f"EXECUTING PLAN: Step {self.current_step + 1}/{self.total_plan_steps}"
                )

                # Display the current plan with the current step highlighted
                if self.current_plan:
                    self.console.print_plan(self.current_plan, self.current_step)

                # Extract next step from plan
                next_step = self.current_plan[self.current_step]

                if (
                    isinstance(next_step, dict)
                    and "tool" in next_step
                    and "tool_args" in next_step
                ):
                    # We have a properly formatted step with tool and args
                    tool_name = next_step["tool"]
                    tool_args = next_step["tool_args"]

                    # Create a parsed response structure as if it came from the LLM
                    parsed = {
                        "thought": f"Executing step {self.current_step + 1} of the plan",
                        "goal": f"Following the plan to {user_input}",
                        "tool": tool_name,
                        "tool_args": tool_args,
                    }

                    # Add to conversation
                    conversation.append({"role": "assistant", "content": parsed})

                    # Display the agent's reasoning for the step
                    self.console.print_thought(
                        parsed.get("thought", "Executing plan step")
                    )
                    self.console.print_goal(parsed.get("goal", "Following the plan"))

                    # Display the tool call in real-time
                    self.console.print_tool_usage(tool_name)

                    # Start progress indicator for tool execution
                    self.console.start_progress("Executing tool")

                    # Execute the tool
                    tool_result = self._execute_tool(tool_name, tool_args)

                    # Stop progress indicator
                    self.console.stop_progress()

                    # Handle domain-specific post-processing
                    self._post_process_tool_result(tool_name, tool_args, tool_result)

                    # Handle large tool results
                    truncated_result = self._handle_large_tool_result(
                        tool_result, conversation
                    )

                    # Note: Tool results are not added to messages array
                    # The assistant's next response will incorporate the tool result

                    # Display the tool result in real-time (show full result to user)
                    self.console.print_tool_complete()

                    self.console.pretty_print_json(tool_result, "Tool Result")

                    # Store the truncated output for future context
                    previous_outputs.append(
                        {
                            "tool": tool_name,
                            "args": tool_args,
                            "result": truncated_result,
                        }
                    )

                    # Check for error
                    if (
                        isinstance(tool_result, dict)
                        and tool_result.get("status") == "error"
                    ):
                        error_count += 1
                        last_error = tool_result.get("error")
                        logger.warning(
                            f"Tool execution error in plan (count: {error_count}): {last_error}"
                        )
                        self.console.print_error(last_error)

                        # Switch to error recovery state
                        self.execution_state = self.STATE_ERROR_RECOVERY
                        self.console.print_state_info(
                            "ERROR RECOVERY: Handling tool execution failure"
                        )

                        # Break out of plan execution to trigger error recovery prompt
                        continue
                    else:
                        # Success - move to next step in plan
                        self.current_step += 1

                        # Check if we've completed the plan
                        if self.current_step >= self.total_plan_steps:
                            logger.debug("Plan execution completed")
                            self.execution_state = self.STATE_COMPLETION
                            self.console.print_state_info(
                                "COMPLETION: Plan fully executed"
                            )

                            # Prepare message for final answer - truncate if too large
                            # Truncate previous outputs if they're too large

                            original_str = json.dumps(previous_outputs)
                            if len(original_str) > 2000:
                                truncated_outputs = self._truncate_large_content(
                                    previous_outputs, max_chars=2000
                                )
                            else:
                                # Use compact JSON for small outputs to avoid unnecessary expansion
                                truncated_outputs = original_str

                            completion_message = (
                                f"You have successfully completed all steps in the plan for: {user_input}\n"
                                f"Previous outputs:\n{truncated_outputs}\n\n"
                                f"Please provide a final answer summarizing what you've accomplished.\n\n"
                                f"{self._add_format_reminder()}"
                            )

                            # Debug logging - only show if truncation happened
                            if self.debug and len(original_str) > 2000:
                                print(
                                    f"\n[DEBUG] Large completion context truncated: {len(original_str)} → {len(truncated_outputs)} chars"
                                )

                            # Add completion request to messages
                            messages.append(
                                {"role": "user", "content": completion_message}
                            )

                            # Send the completion prompt to get final answer
                            self.console.print_step_header(steps_taken, steps_limit)
                            self.console.print_state_info(
                                "COMPLETION: Requesting final answer"
                            )

                            # Continue to next iteration to get final answer
                            continue
                        else:
                            # Continue with next step - no need to query LLM again
                            continue
                else:
                    # Plan step doesn't have proper format, fall back to LLM
                    logger.warning(
                        f"Plan step {self.current_step + 1} doesn't have proper format: {next_step}"
                    )
                    self.console.print_warning(
                        f"Plan step {self.current_step + 1} format incorrect, asking LLM for guidance"
                    )
                    prompt = (
                        f"You are following a plan but step {self.current_step + 1} doesn't have proper format: {next_step}\n"
                        f"Please interpret this step and decide what tool to use next.\n\n"
                        f"Task: {user_input}\n\n"
                    )

                    # Apply format reminders to the prompt
                    prompt += self._add_format_reminder()
            else:
                # Normal execution flow - query the LLM
                if self.execution_state == self.STATE_DIRECT_EXECUTION:
                    self.console.print_state_info("DIRECT EXECUTION: Analyzing task")
                elif self.execution_state == self.STATE_PLANNING:
                    self.console.print_state_info("PLANNING: Creating or refining plan")
                elif self.execution_state == self.STATE_ERROR_RECOVERY:
                    self.console.print_state_info(
                        "ERROR RECOVERY: Handling previous error"
                    )

                    # Truncate previous outputs if too large to avoid overwhelming the LLM
                    truncated_outputs = (
                        self._truncate_large_content(previous_outputs, max_chars=500)
                        if previous_outputs
                        else "None"
                    )

                    # Create a specific error recovery prompt
                    prompt = (
                        f"TOOL EXECUTION FAILED!\n\n"
                        f"You were trying to execute: {last_tool_call[0] if last_tool_call else 'unknown tool'}\n"
                        f"Error: {last_error}\n\n"
                        f"Original task: {user_input}\n\n"
                        f"Current plan step {self.current_step + 1}/{self.total_plan_steps} failed.\n"
                        f"Current plan: {self.current_plan}\n\n"
                        f"Previous successful outputs: {truncated_outputs}\n\n"
                        f"INSTRUCTIONS:\n"
                        f"1. Analyze the error and understand what went wrong\n"
                        f"2. Create a NEW corrected plan that fixes the error\n"
                        f"3. Make sure to use correct tool parameters (check the available tools)\n"
                        f"4. Start executing the corrected plan\n\n"
                        f"Respond with your analysis, a corrected plan, and the first tool to execute."
                    )

                    # Add the error recovery prompt to the messages array so it gets sent to LLM
                    messages.append({"role": "user", "content": prompt})

                    # Reset state to planning after creating recovery prompt
                    self.execution_state = self.STATE_PLANNING
                    self.current_plan = None
                    self.current_step = 0
                    self.total_plan_steps = 0

                elif self.execution_state == self.STATE_COMPLETION:
                    self.console.print_state_info("COMPLETION: Finalizing response")

            # Print the prompt if show_prompts is enabled (separate from debug_prompts)
            if self.show_prompts:
                # Build context from system prompt and messages
                context_parts = [
                    (
                        f"SYSTEM: {self.system_prompt[:200]}..."
                        if len(self.system_prompt) > 200
                        else f"SYSTEM: {self.system_prompt}"
                    )
                ]

                for msg in messages:
                    role = msg.get("role", "user").upper()
                    content = str(msg.get("content", ""))[:150]
                    context_parts.append(
                        f"{role}: {content}{'...' if len(str(msg.get('content', ''))) > 150 else ''}"
                    )

                if not messages and prompt:
                    context_parts.append(
                        f"USER: {prompt[:150]}{'...' if len(prompt) > 150 else ''}"
                    )

                self.console.print_prompt("\n".join(context_parts), "LLM Context")

            # Handle streaming or non-streaming LLM response
            if self.streaming:
                # Print LLM thinking header for streaming mode
                if hasattr(self.console, "console"):
                    self.console.console.print(
                        "\n[bold blue]🧠 LLM Response:[/bold blue]"
                    )
                else:
                    print("\n🧠 LLM Response:")

                # Add prompt to conversation if debug is enabled
                if self.debug_prompts:
                    conversation.append(
                        {"role": "system", "content": {"prompt": prompt}}
                    )
                    # Print the prompt if show_prompts is enabled
                    if self.show_prompts:
                        self.console.print_prompt(
                            prompt, f"Prompt (Step {steps_taken})"
                        )

                # Get streaming response from ChatSDK with proper conversation history
                try:
                    response_stream = self.chat.send_messages_stream(
                        messages=messages, system_prompt=self.system_prompt
                    )

                    # Process the streaming response chunks as they arrive
                    full_response = ""
                    for chunk_response in response_stream:
                        if not chunk_response.is_complete:
                            chunk = chunk_response.text
                            # Display each chunk as it arrives
                            if hasattr(self.console, "print_streaming_text"):
                                self.console.print_streaming_text(chunk)
                            else:
                                print(chunk, end="", flush=True)
                            full_response += chunk

                    # Signal end of stream
                    if hasattr(self.console, "print_streaming_text"):
                        self.console.print_streaming_text("", end_of_stream=True)
                    else:
                        print("", flush=True)

                    # Get the full response from the buffer
                    response = full_response
                except ConnectionError as e:
                    # Handle LLM server connection errors specifically
                    error_msg = f"LLM Server Connection Failed (streaming): {str(e)}"
                    logger.error(error_msg)
                    self.console.print_error(error_msg)

                    # Add error to history
                    self.error_history.append(
                        {
                            "step": steps_taken,
                            "error": error_msg,
                            "type": "llm_connection_error",
                        }
                    )

                    # Return error response
                    final_answer = (
                        f"Unable to complete task due to LLM server error: {str(e)}"
                    )
                    break
                except Exception as e:
                    logger.error(f"Unexpected error during streaming: {e}")

                    # Add to error history
                    self.error_history.append(
                        {
                            "step": steps_taken,
                            "error": str(e),
                            "type": "llm_streaming_error",
                        }
                    )

                    # Return error response
                    final_answer = (
                        f"Unable to complete task due to streaming error: {str(e)}"
                    )
                    break
            else:
                # Use progress indicator for non-streaming mode
                self.console.start_progress("Thinking")

                # Debug logging before LLM call
                if self.debug:

                    print(f"\n[DEBUG] About to call LLM with {len(messages)} messages")
                    print(
                        f"[DEBUG] Last message role: {messages[-1]['role'] if messages else 'No messages'}"
                    )
                    if messages and len(messages[-1].get("content", "")) < 500:
                        print(
                            f"[DEBUG] Last message content: {messages[-1]['content']}"
                        )
                    else:
                        print(
                            f"[DEBUG] Last message content length: {len(messages[-1].get('content', ''))}"
                        )
                    print(f"[DEBUG] Execution state: {self.execution_state}")
                    print(
                        f"[DEBUG] Current step: {self.current_step}/{self.total_plan_steps}"
                    )

                # Get complete response from ChatSDK with proper conversation history
                try:
                    if self.debug:
                        print("[DEBUG] Calling chat.send_messages...")
                    chat_response = self.chat.send_messages(
                        messages=messages, system_prompt=self.system_prompt
                    )
                    if self.debug:
                        print("[DEBUG] LLM response received")
                    response = chat_response.text
                except ConnectionError as e:
                    # Handle LLM server connection errors specifically
                    error_msg = f"LLM Server Connection Failed: {str(e)}"
                    if self.debug:
                        print(f"[DEBUG] {error_msg}")
                    logger.error(error_msg)
                    self.console.print_error(error_msg)

                    # Add error to history and update state
                    self.error_history.append(
                        {
                            "step": steps_taken,
                            "error": error_msg,
                            "type": "llm_connection_error",
                        }
                    )

                    # Return error response
                    final_answer = (
                        f"Unable to complete task due to LLM server error: {str(e)}"
                    )
                    break
                except Exception as e:
                    if self.debug:
                        print(f"[DEBUG] Error calling LLM: {e}")
                    logger.error(f"Unexpected error calling LLM: {e}")

                    # Add to error history
                    self.error_history.append(
                        {"step": steps_taken, "error": str(e), "type": "llm_error"}
                    )

                    # Return error response
                    final_answer = f"Unable to complete task due to error: {str(e)}"
                    break

                # Stop the progress indicator
                self.console.stop_progress()

            # Print the LLM response to the console
            logger.debug(f"LLM response: {response[:200]}...")
            if self.show_prompts:
                self.console.print_response(response, "LLM Response")

            # Parse the response
            parsed = self._parse_llm_response(response)
            logger.debug(f"Parsed response: {parsed}")
            conversation.append({"role": "assistant", "content": parsed})

            # Add assistant response to messages for chat history
            messages.append({"role": "assistant", "content": response})

            # Validate the response has a plan if required
            self._validate_plan_required(parsed, steps_taken)

            # If the LLM needs to create a plan first, re-prompt it specifically for that
            if "needs_plan" in parsed and parsed["needs_plan"]:
                # Prepare a special prompt that specifically requests a plan
                deferred_tool = parsed.get("deferred_tool", None)
                deferred_args = parsed.get("deferred_tool_args", {})

                plan_prompt = (
                    f"You MUST create a detailed plan first before taking any action.\n\n"
                    f"User request: {user_input}\n\n"
                )

                if deferred_tool:
                    plan_prompt += (
                        f"You initially wanted to use the {deferred_tool} tool with these arguments:\n"
                        f"{json.dumps(deferred_args, indent=2)}\n\n"
                        f"However, you MUST first create a plan. Please create a plan that includes this tool usage as a step.\n\n"
                    )

                plan_prompt += (
                    "Create a detailed plan with all necessary steps in JSON format, including exact tool names and arguments.\n"
                    "Respond with your reasoning, plan, and the first tool to use."
                )

                # Apply format reminders to the prompt
                plan_prompt = self._add_format_reminder()

                # Store the plan prompt in conversation if debug is enabled
                if self.debug_prompts:
                    conversation.append(
                        {"role": "system", "content": {"prompt": plan_prompt}}
                    )
                    if self.show_prompts:
                        self.console.print_prompt(plan_prompt, "Plan Request Prompt")

                # Notify the user we're asking for a plan
                self.console.print_info("Requesting a detailed plan before proceeding")

                # Get the planning response
                if self.streaming:
                    # Add prompt to conversation if debug is enabled
                    if self.debug_prompts:
                        conversation.append(
                            {"role": "system", "content": {"prompt": plan_prompt}}
                        )
                        # Print the prompt if show_prompts is enabled
                        if self.show_prompts:
                            self.console.print_prompt(
                                plan_prompt, f"Prompt (Step {steps_taken})"
                            )

                    # Handle streaming as before
                    full_response = ""
                    # Add plan request to messages
                    messages.append({"role": "user", "content": plan_prompt})

                    # Use ChatSDK for streaming plan response
                    stream_gen = self.chat.send_messages_stream(
                        messages=messages, system_prompt=self.system_prompt
                    )

                    for chunk_response in stream_gen:
                        if not chunk_response.is_complete:
                            chunk = chunk_response.text
                            if hasattr(self.console, "print_streaming_text"):
                                self.console.print_streaming_text(chunk)
                            else:
                                print(chunk, end="", flush=True)
                            full_response += chunk

                    if hasattr(self.console, "print_streaming_text"):
                        self.console.print_streaming_text("", end_of_stream=True)
                    else:
                        print("", flush=True)

                    plan_response = full_response
                else:
                    # Use progress indicator for non-streaming mode
                    self.console.start_progress("Creating plan")

                    # Store the plan prompt in conversation if debug is enabled
                    if self.debug_prompts:
                        conversation.append(
                            {"role": "system", "content": {"prompt": plan_prompt}}
                        )
                        if self.show_prompts:
                            self.console.print_prompt(
                                plan_prompt, "Plan Request Prompt"
                            )

                    # Add plan request to messages
                    messages.append({"role": "user", "content": plan_prompt})

                    # Use ChatSDK for non-streaming plan response
                    chat_response = self.chat.send_messages(
                        messages=messages, system_prompt=self.system_prompt
                    )
                    plan_response = chat_response.text
                    self.console.stop_progress()

                # Parse the plan response
                parsed_plan = self._parse_llm_response(plan_response)
                logger.debug(f"Parsed plan response: {parsed_plan}")
                conversation.append({"role": "assistant", "content": parsed_plan})

                # Add plan response to messages for chat history
                messages.append({"role": "assistant", "content": plan_response})

                # Display the agent's reasoning for the plan
                self.console.print_thought(parsed_plan.get("thought", "Creating plan"))
                self.console.print_goal(parsed_plan.get("goal", "Planning for task"))

                # Set the parsed response to the new plan for further processing
                parsed = parsed_plan

            # Display the agent's reasoning in real-time
            self.console.print_thought(
                parsed.get("thought", "No explicit reasoning provided")
            )
            self.console.print_goal(parsed.get("goal", "No explicit goal provided"))

            # Process plan if available
            if "plan" in parsed:
                self.current_plan = parsed["plan"]
                self.current_step = 0
                self.total_plan_steps = len(self.current_plan)
                self.execution_state = self.STATE_EXECUTING_PLAN
                logger.debug(
                    f"New plan created with {self.total_plan_steps} steps: {self.current_plan}"
                )

            # If the response contains a tool call, execute it
            if "tool" in parsed and "tool_args" in parsed:

                # Display the current plan with the current step highlighted
                if self.current_plan:
                    self.console.print_plan(self.current_plan, self.current_step)

                # When both plan and tool are present, prioritize the plan execution
                # If we have a plan, we should execute from the plan, not the standalone tool call
                if "plan" in parsed and self.current_plan and self.total_plan_steps > 0:
                    # Skip the standalone tool execution and let the plan execution handle it
                    # The plan execution logic will handle this in the next iteration
                    logger.debug(
                        "Plan and tool both present - deferring to plan execution logic"
                    )
                    continue  # Skip tool execution, let plan execution handle it

                # If this was a single-step plan, mark as completed after tool execution
                if self.total_plan_steps == 1:
                    logger.debug(
                        "Single-step plan will be marked completed after tool execution"
                    )
                    self.execution_state = self.STATE_COMPLETION

                tool_name = parsed["tool"]
                tool_args = parsed["tool_args"]
                logger.debug(f"Tool call detected: {tool_name} with args {tool_args}")

                # Display the tool call in real-time
                self.console.print_tool_usage(tool_name)

                if tool_args:
                    self.console.pretty_print_json(tool_args, "Arguments")

                # Start progress indicator for tool execution
                self.console.start_progress("Executing tool")

                # Check for repeated tool calls
                if last_tool_call == (tool_name, str(tool_args)):
                    # Stop progress indicator
                    self.console.stop_progress()

                    logger.warning(f"Detected repeated tool call: {tool_name}")
                    # Force a final answer if the same tool is called repeatedly
                    final_answer = (
                        f"Task completed with {tool_name}. No further action needed."
                    )

                    self.console.print_repeated_tool_warning()
                    break

                # Execute the tool
                tool_result = self._execute_tool(tool_name, tool_args)

                # Stop progress indicator
                self.console.stop_progress()

                # Handle domain-specific post-processing
                self._post_process_tool_result(tool_name, tool_args, tool_result)

                # Handle large tool results
                truncated_result = self._handle_large_tool_result(
                    tool_result, conversation
                )

                # Note: Tool results are not added to messages array
                # The assistant's next response will incorporate the tool result

                # Display the tool result in real-time (show full result to user)
                self.console.print_tool_complete()

                self.console.pretty_print_json(tool_result, "Result")

                # Store the truncated output for future context
                previous_outputs.append(
                    {"tool": tool_name, "args": tool_args, "result": truncated_result}
                )

                # Update last tool call
                last_tool_call = (tool_name, str(tool_args))

                # For single-step plans, set final answer and break the loop after execution
                if (
                    self.execution_state == self.STATE_COMPLETION
                    and self.total_plan_steps == 1
                ):
                    logger.debug("Single-step plan execution completed, finalizing")
                    final_answer = f"Task completed with {tool_name}. {tool_result.get('result', {}).get('message', '')}"
                    self.console.print_final_answer(final_answer)
                    break

                # Check if tool execution resulted in an error
                if (
                    isinstance(tool_result, dict)
                    and tool_result.get("status") == "error"
                ):
                    error_count += 1
                    last_error = tool_result.get("error")
                    logger.warning(
                        f"Tool execution error in plan (count: {error_count}): {last_error}"
                    )
                    self.console.print_error(last_error)

                    # Switch to error recovery state
                    self.execution_state = self.STATE_ERROR_RECOVERY
                    self.console.print_state_info(
                        "ERROR RECOVERY: Handling tool execution failure"
                    )

                    # Break out of tool execution to trigger error recovery prompt
                    continue

            # If the response contains a final answer, we're done
            elif "answer" in parsed:
                logger.debug(f"Final answer received: {parsed['answer']}")
                final_answer = parsed["answer"]
                self.execution_state = self.STATE_COMPLETION
                self.console.print_final_answer(parsed["answer"])
                break  # Break the loop when we get a final answer

            # Always collect performance stats for tracking (but only display if enabled)
            perf_stats = self.chat.get_stats()

            # Remove decode_token_times from the stats before displaying and adding to conversation
            if perf_stats and "decode_token_times" in perf_stats:
                del perf_stats["decode_token_times"]

            # Display stats if enabled
            if self.show_stats:
                self.console.display_stats(perf_stats)

            # Always add performance stats to the conversation history for token tracking
            if perf_stats:
                conversation.append(
                    {
                        "role": "system",
                        "content": {
                            "type": "stats",
                            "step": steps_taken,
                            "performance_stats": perf_stats,
                        },
                    }
                )

            # Validate plan required
            self._validate_plan_required(parsed, steps_taken)

        # Print completion message
        self.console.print_completion(steps_taken, steps_limit)

        # Calculate total duration
        total_duration = time.time() - start_time

        # Aggregate token counts from conversation stats
        total_input_tokens = 0
        total_output_tokens = 0
        for entry in conversation:
            if entry.get("role") == "system" and isinstance(entry.get("content"), dict):
                content = entry["content"]
                if content.get("type") == "stats" and "performance_stats" in content:
                    stats = content["performance_stats"]
                    if stats.get("input_tokens") is not None:
                        total_input_tokens += stats["input_tokens"]
                    if stats.get("output_tokens") is not None:
                        total_output_tokens += stats["output_tokens"]

        # Return the result
        has_errors = len(self.error_history) > 0
        has_valid_answer = (
            final_answer and final_answer.strip()
        )  # Check for non-empty answer
        result = {
            "status": (
                "success"
                if has_valid_answer and not has_errors
                else ("failed" if has_errors else "incomplete")
            ),
            "result": (
                final_answer
                if final_answer
                else "Maximum steps reached without final answer"
            ),
            "system_prompt": self.system_prompt,  # Include system prompt in the result
            "conversation": conversation,
            "steps_taken": steps_taken,
            "duration": total_duration,  # Total query processing time in seconds
            "input_tokens": total_input_tokens,  # Total input tokens across all steps
            "output_tokens": total_output_tokens,  # Total output tokens across all steps
            "total_tokens": total_input_tokens
            + total_output_tokens,  # Combined token count
            "error_count": len(self.error_history),
            "error_history": self.error_history,  # Include the full error history
        }

        # Write result to file if requested
        if output_to_file:
            file_path = self._write_json_to_file(result, filename)
            result["output_file"] = file_path

        logger.debug(f"Query processing complete: {result}")

        # Store the result internally
        self.last_result = result

        return result

    def _post_process_tool_result(
        self, _tool_name: str, _tool_args: Dict[str, Any], _tool_result: Dict[str, Any]
    ) -> None:
        """
        Post-process the tool result for domain-specific handling.
        Override this in subclasses to provide domain-specific behavior.

        Args:
            _tool_name: Name of the tool that was executed
            _tool_args: Arguments that were passed to the tool
            _tool_result: Result returned by the tool
        """
        ...

    def display_result(
        self,
        title: str = "Result",
        result: Dict[str, Any] = None,
        print_result: bool = False,
    ) -> None:
        """
        Display the result and output file path information.

        Args:
            title: Optional title for the result panel
            result: Optional result dictionary to display. If None, uses the last stored result.
            print_result: If True, print the result to the console
        """
        # Use the provided result or fall back to the last stored result
        display_result = result if result is not None else self.last_result

        if display_result is None:
            self.console.print_warning("No result available to display.")
            return

        # Print the full result with syntax highlighting
        if print_result:
            self.console.pretty_print_json(display_result, title)

        # If there's an output file, display its path after the result
        if "output_file" in display_result:
            self.console.print_info(
                f"Output written to: {display_result['output_file']}"
            )

    def get_error_history(self) -> List[str]:
        """
        Get the history of errors encountered by the agent.

        Returns:
            List of error messages
        """
        return self.error_history

    def _validate_plan_required(self, parsed: Dict[str, Any], step: int) -> None:
        """
        Validate that the response includes a plan when required by the agent.

        Args:
            parsed: The parsed response from the LLM
            step: The current step number
        """
        # Skip validation if we're not in planning mode or if we're already executing a plan
        if self.execution_state != self.STATE_PLANNING or self.current_plan is not None:
            return

        # Allow simple single-tool operations without requiring a plan
        if "tool" in parsed and step == 1:
            tool_name = parsed.get("tool", "")
            # List of tools that can execute directly without a plan
            simple_tools = self.SIMPLE_TOOLS
            if tool_name in simple_tools:
                logger.debug(f"Allowing direct execution of simple tool: {tool_name}")
                return

        # Check if plan is missing on the first step
        if "plan" not in parsed and step == 1:
            warning_msg = f"No plan found in step {step} response. The agent should create a plan for all tasks."
            logger.warning(warning_msg)
            self.console.print_warning(warning_msg)

            # For the first step, we'll add a flag to indicate we need to re-prompt for a plan
            parsed["needs_plan"] = True

            # If there's a tool in the response, store it but don't execute it yet
            if "tool" in parsed:
                parsed["deferred_tool"] = parsed["tool"]
                parsed["deferred_tool_args"] = parsed.get("tool_args", {})
                # Remove the tool so it won't be executed
                del parsed["tool"]
                if "tool_args" in parsed:
                    del parsed["tool_args"]

            # Set state to indicate we need planning
            self.execution_state = self.STATE_PLANNING
