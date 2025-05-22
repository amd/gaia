# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Blender-specific agent for creating and modifying 3D scenes.
"""

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import tool
from gaia.agents.base.console import AgentConsole
from gaia.mcp.blender_mcp_client import MCPClient
from gaia.agents.Blender.core.scene import generate_scene_diagnosis_code

from typing import Dict, Any, Optional
import logging
import re

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class BlenderAgent(Agent):
    """
    Blender-specific agent focused on 3D scene creation and modification.
    Inherits core functionality from the base Agent class.
    """

    # Define Blender-specific tools that can execute directly without requiring a plan
    SIMPLE_TOOLS = ["clear_scene", "get_scene_info"]

    def __init__(
        self,
        use_local_llm: bool = True,
        mcp: Optional[MCPClient] = None,
        model_id: str = None,
        base_url: str = "http://localhost:8000/api/v0",
        max_steps: int = 5,
        debug_prompts: bool = False,
        output_dir: str = None,
        streaming: bool = False,
        show_stats: bool = True,
    ):
        """
        Initialize the BlenderAgent with MCP client and LLM client.

        Args:
            mcp: An optional pre-configured MCP client, otherwise a new one will be created
            model_id: The ID of the model to use with LLM server
            base_url: Base URL for the local LLM server API
            max_steps: Maximum number of steps the agent can take before terminating
            debug_prompts: If True, includes prompts in the conversation history
            output_dir: Directory for storing JSON output files (default: current directory)
            streaming: If True, enables real-time streaming of LLM responses (default: False)
            show_stats: If True, displays LLM performance stats after each response (default: True)
        """
        # Initialize the MCP client for Blender communication
        self.mcp = mcp if mcp else MCPClient()

        # Call the parent class constructor
        super().__init__(
            use_local_llm=use_local_llm,
            model_id=model_id,
            base_url=base_url,
            max_steps=max_steps,
            debug_prompts=debug_prompts,
            output_dir=output_dir,
            streaming=streaming,
            show_stats=show_stats,
        )

        # Register Blender-specific tools
        self._register_tools()

    def _create_console(self) -> AgentConsole:
        """
        Create and return a Agent-specific console output handler.

        Returns:
            A AgentConsole instance
        """
        return AgentConsole()

    def _get_system_prompt(self) -> str:
        """Generate the system prompt for the Blender agent."""
        # Get formatted tools from registry
        return f"""
You are a specialized Blender 3D assistant that can create and modify 3D scenes.
You will use a set of tools to accomplish tasks based on the user's request.

==== JSON RESPONSE FORMAT ====
ALWAYS respond with a single valid JSON object. NO text outside this structure.
- Use double quotes for keys and string values
- Ensure all braces and brackets are properly closed
- No trailing commas in arrays or objects
- All required fields must be included
- Never wrap your JSON in code blocks or backticks

Your JSON response must follow this format:
{{
    "thought": "your reasoning about what to do",
    "goal": "clear statement of what you're achieving",
    "plan": [
        {{"tool": "tool1", "tool_args": {{"arg1": "val1"}}}},
        {{"tool": "tool2", "tool_args": {{"arg1": "val1"}}}}
    ],
    "tool": "first_tool_to_execute",
    "tool_args": {{"arg1": "val1", "arg2": "val2"}}
}}

For final answers:
{{
    "thought": "your reasoning",
    "goal": "what was achieved",
    "answer": "your final answer"
}}

==== CRITICAL RULES ====
1. Create a plan for multi-step tasks, but simple single operations (like clear_scene) can execute directly
2. Each plan step must be atomic (one tool call per step)
3. For colored objects, ALWAYS include both create_object AND set_material_color steps
4. When clearing a scene, ONLY use clear_scene without creating new objects unless requested
5. Always use the actual returned object names for subsequent operations
6. Never repeat the same tool call with identical arguments

==== COLORED OBJECT DETECTION ====
🔍 SCAN the user request for color words:
- "red", "green", "blue", "yellow", "purple", "cyan", "white", "black"
- "colored", "paint", "material"

⚠️ IF you find ANY color words, you MUST:
1. Create the object with create_object
2. Set its color with set_material_color
3. Then do any other modifications

❌ NEVER skip the color step if a color is mentioned!

Examples of colored requests:
- "blue cylinder" → needs create_object + set_material_color
- "red sphere" → needs create_object + set_material_color
- "green cube and yellow cone" → needs 4 steps total

==== TOOL PARAMETER RULES ====
⚠️  CRITICAL: create_object does NOT accept a 'color' parameter!
✅ CORRECT workflow for colored objects:
   Step 1: create_object (type, name, location, rotation, scale ONLY)
   Step 2: set_material_color (object_name, color)

⚠️  CRITICAL: Colors must be RGBA format with 4 values [r, g, b, a]
   ❌ WRONG: [0, 0, 1] (only 3 values)
   ✅ CORRECT: [0, 0, 1, 1] (4 values including alpha)

⚠️  CRITICAL: EVERY colored object must have BOTH steps!
   If user asks for "green cube and red sphere", you need 4 steps:
   1. create_object (cube)
   2. set_material_color (cube, green)
   3. create_object (sphere)
   4. set_material_color (sphere, red)

==== COMMON WORKFLOWS ====
1. Clearing a scene: Use clear_scene() with no arguments
2. Creating a single colored object:
   - Step 1: create_object(type="CYLINDER", name="my_obj", location=[0,0,0])
   - Step 2: set_material_color(object_name="my_obj", color=[0,0,1,1])
3. Creating multiple colored objects:
   - Step 1: create_object(type="CUBE", name="cube1", location=[0,0,0])
   - Step 2: set_material_color(object_name="cube1", color=[0,1,0,1])
   - Step 3: create_object(type="SPHERE", name="sphere1", location=[3,0,0])
   - Step 4: set_material_color(object_name="sphere1", color=[1,0,0,1])
4. Modifying objects: Use modify_object with the parameters you want to change
"""

    def _register_tools(self):
        """Register all Blender-related tools for the agent."""

        @tool
        def clear_scene() -> Dict[str, Any]:
            """
            Remove all objects from the current Blender scene.

            Returns:
                Dictionary containing the operation result

            Example JSON response:
            ```json
            {
                "thought": "I will clear the scene to start fresh",
                "goal": "Clear the scene to start fresh",
                "tool": "clear_scene",
                "tool_args": {}
            }
            ```
            """
            try:
                from gaia.agents.Blender.core.scene import SceneManager

                scene_manager = SceneManager(self.mcp)
                return scene_manager.clear_scene()
            except Exception as e:
                self.error_history.append(str(e))
                return {"status": "error", "error": str(e)}

        @tool
        def create_object(
            type: str = "CUBE",
            name: str = None,
            location: tuple = (0, 0, 0),
            rotation: tuple = (0, 0, 0),
            scale: tuple = (1, 1, 1),
        ) -> Dict[str, Any]:
            """
            Create a 3D object in Blender.

            Args:
                type: Object type (CUBE, SPHERE, CYLINDER, CONE, TORUS)
                name: Optional name for the object (default: generated from type)
                location: (x, y, z) coordinates for object position (default: (0,0,0))
                rotation: (rx, ry, rz) rotation in radians (default: (0,0,0))
                scale: (sx, sy, sz) scaling factors for the object (default: (1,1,1))

            Returns:
                Dictionary containing the creation result

            Example JSON response:
            ```json
            {
                "thought": "I will create a cube at the center of the scene",
                "goal": "Create a red cube at the center of the scene",
                "tool": "create_object",
                "tool_args": {
                    "type": "CUBE",
                    "name": "my_cube",
                    "location": [0, 0, 0],
                    "rotation": [0, 0, 0],
                    "scale": [1, 1, 1]
                }
            }
            ```
            """
            try:
                result = self.mcp.create_object(
                    type=type.upper(),
                    name=name or f"generated_{type.lower()}",
                    location=location,
                    rotation=rotation,
                    scale=scale,
                )
                return result
            except Exception as e:
                self.error_history.append(str(e))
                return {"status": "error", "error": str(e)}

        @tool
        def set_material_color(
            object_name: str, color: tuple = (1, 0, 0, 1)
        ) -> Dict[str, Any]:
            """
            Set the material color for an object. Creates a new material if one doesn't exist.

            Args:
                object_name: Name of the object to modify
                color: RGBA color values as tuple (red, green, blue, alpha), values from 0-1

            Returns:
                Dictionary with the operation result

            Example JSON response:
            ```json
            {
                "thought": "I will set the cube's material to red",
                "goal": "Create a red cube at the center of the scene",
                "tool": "set_material_color",
                "tool_args": {
                    "object_name": "my_cube",
                    "color": [1, 0, 0, 1]
                }
            }
            ```
            """
            try:
                from gaia.agents.Blender.core.materials import MaterialManager

                material_manager = MaterialManager(self.mcp)
                return material_manager.set_material_color(object_name, color)
            except Exception as e:
                self.error_history.append(str(e))
                return {"status": "error", "error": str(e)}

        # @tool
        def get_object_info(name: str) -> Dict[str, Any]:
            """
            Get information about an object in the scene.

            Args:
                name: Name of the object

            Returns:
                Dictionary containing object information

            Example JSON response:
            ```json
            {
                "thought": "I will get information about the cube",
                "goal": "Create a red cube at the center of the scene",
                "tool": "get_object_info",
                "tool_args": {
                    "name": "my_cube"
                }
            }
            ```
            """
            try:
                return self.mcp.get_object_info(name)
            except Exception as e:
                self.error_history.append(str(e))
                return {"status": "error", "error": str(e)}

        @tool
        def modify_object(
            name: str,
            location: tuple = None,
            scale: tuple = None,
            rotation: tuple = None,
        ) -> Dict[str, Any]:
            """
            Modify an existing object in Blender.

            Args:
                name: Name of the object to modify
                location: New (x, y, z) location or None to keep current
                scale: New (sx, sy, sz) scale or None to keep current
                rotation: New (rx, ry, rz) rotation or None to keep current

            Returns:
                Dictionary with the modification result

            Example JSON response:
            ```json
            {
                "thought": "I will move the cube up by 2 units",
                "goal": "Create a red cube at the center of the scene",
                "tool": "modify_object",
                "tool_args": {
                    "name": "my_cube",
                    "location": [0, 0, 2],
                    "scale": null,
                    "rotation": null
                }
            }
            ```
            """
            try:
                return self.mcp.modify_object(
                    name=name, location=location, scale=scale, rotation=rotation
                )
            except Exception as e:
                self.error_history.append(str(e))
                return {"status": "error", "error": str(e)}

        # @tool
        def delete_object(name: str) -> Dict[str, Any]:
            """
            Delete an object from the scene.

            Args:
                name: Name of the object to delete

            Returns:
                Dictionary with the deletion result

            Example JSON response:
            ```json
            {
                "thought": "I will delete the cube",
                "goal": "Clear the scene to start fresh",
                "tool": "delete_object",
                "tool_args": {
                    "name": "my_cube"
                }
            }
            ```
            """
            try:
                return self.mcp.delete_object(name)
            except Exception as e:
                self.error_history.append(str(e))
                return {"status": "error", "error": str(e)}

        @tool
        def get_scene_info() -> Dict[str, Any]:
            """
            Get information about the current scene.

            Returns:
                Dictionary containing scene information

            Example JSON response:
            ```json
            {
                "thought": "I will get information about the current scene",
                "goal": "Clear the scene to start fresh",
                "tool": "get_scene_info",
                "tool_args": {}
            }
            ```
            """
            try:
                return self.mcp.get_scene_info()
            except Exception as e:
                self.error_history.append(str(e))
                return {"status": "error", "error": str(e)}

        # @tool
        def execute_blender_code(code: str) -> Dict[str, Any]:
            """
            Execute arbitrary Python code in Blender with error handling.

            Args:
                code: Python code to execute in Blender

            Returns:
                Dictionary with execution results or error information

            Example JSON response:
            ```json
            {
                "thought": "I will execute custom code to create a complex shape",
                "goal": "Create a red cube at the center of the scene",
                "tool": "execute_blender_code",
                "tool_args": {
                    "code": "import bpy\\nbpy.ops.mesh.primitive_cube_add()"
                }
            }
            ```
            """
            try:
                return self.mcp.execute_code(code)
            except Exception as e:
                self.error_history.append(str(e))
                return {"status": "error", "error": str(e)}

        # @tool
        def diagnose_scene() -> Dict[str, Any]:
            """
            Diagnose the current Blender scene for common issues.
            Returns information about objects, materials, and potential problems.

            Returns:
                Dictionary with diagnostic information

            Example JSON response:
            ```json
            {
                "thought": "I will diagnose the scene for any issues",
                "goal": "Clear the scene to start fresh",
                "tool": "diagnose_scene",
                "tool_args": {}
            }
            ```
            """
            try:
                # Use the core library's scene diagnosis code generator
                diagnostic_code = generate_scene_diagnosis_code()
                return self.mcp.execute_code(diagnostic_code)
            except Exception as e:
                self.error_history.append(str(e))
                return {"status": "error", "error": str(e)}

    def _post_process_tool_result(
        self, tool_name: str, tool_args: Dict[str, Any], tool_result: Dict[str, Any]
    ) -> None:
        """
        Post-process the tool result for Blender-specific handling.

        Args:
            tool_name: Name of the tool that was executed
            tool_args: Arguments that were passed to the tool
            tool_result: Result returned by the tool
        """
        # Track object name if created
        if tool_name == "create_object":
            actual_name = self._track_object_name(tool_result)
            if actual_name:
                logger.debug(f"Actual object name created: {actual_name}")
                self.console.print_info(
                    f"Note: Blender assigned name '{actual_name}' to the created object"
                )

                # Update subsequent steps in the plan that might use this object
                if self.current_plan and self.current_step < len(self.current_plan) - 1:
                    for i in range(self.current_step + 1, len(self.current_plan)):
                        future_step = self.current_plan[i]
                        if isinstance(future_step, dict) and "tool_args" in future_step:
                            args = future_step["tool_args"]
                            # Look for object_name or name parameters
                            if "object_name" in args and args[
                                "object_name"
                            ] == tool_args.get("name"):
                                logger.debug(
                                    f"Updating object_name in future step {i+1} from {args['object_name']} to {actual_name}"
                                )
                                self.current_plan[i]["tool_args"][
                                    "object_name"
                                ] = actual_name
                            if "name" in args and args["name"] == tool_args.get("name"):
                                logger.debug(
                                    f"Updating name in future step {i+1} from {args['name']} to {actual_name}"
                                )
                                self.current_plan[i]["tool_args"]["name"] = actual_name

    def _track_object_name(self, result):
        """
        Extract and track the actual object name returned by Blender.

        Args:
            result: The result dictionary from a tool execution

        Returns:
            The actual object name if found, None otherwise
        """
        try:
            if isinstance(result, dict):
                if result.get("status") == "success":
                    if "result" in result and isinstance(result["result"], dict):
                        # Extract name from create_object result
                        if "name" in result["result"]:
                            actual_name = result["result"]["name"]
                            logger.debug(f"Extracted object name: {actual_name}")
                            return actual_name
            return None
        except Exception as e:
            logger.error(f"Error extracting object name: {str(e)}")
            return None

    def create_interactive_scene(
        self,
        scene_description: str,
        max_steps: int = None,
        output_to_file: bool = True,
        filename: str = None,
    ) -> Dict[str, Any]:
        """
        Create a more complex scene with multiple objects and relationships.

        Args:
            scene_description: Description of the scene to create
            max_steps: Maximum number of steps to take in the conversation (overrides class default if provided)
            output_to_file: If True, write results to a JSON file
            filename: Optional filename for output, if None a timestamped name will be generated

        Returns:
            Dict containing the scene creation result
        """
        # Same process as process_query but with more steps allowed if specified
        return self.process_query(
            f"Create a complete 3D scene with the following description: {scene_description}",
            max_steps=max_steps if max_steps is not None else self.max_steps * 2,
            output_to_file=output_to_file,
            filename=filename,
        )
