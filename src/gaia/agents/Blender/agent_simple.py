from gaia.llm.llm_client import LLMClient
from gaia.mcp.blender_mcp_client import MCPClient
from typing import Dict, Any, Optional, Tuple


class BlenderAgentSimple:
    """Agent wrapper for the Blender Object Creator that handles LLM-driven object creation.

    This is a 'simple' agent because it provides a streamlined interface between natural language
    input and Blender operations, focusing only on basic object creation. It uses an LLM to parse
    user requests into structured commands and the MCP client to execute those commands in Blender.
    Unlike more complex agents, it doesn't handle advanced modeling, scene composition, or multi-step
    operations - it's designed for single-object creation with minimal parameters (type, location, scale).
    """

    # Embed system prompt directly in the class
    SYSTEM_PROMPT = """
You are a 3D modeling assistant. IMPORTANT: For EACH user request, respond with EXACTLY ONE LINE in this format:
TYPE,x,y,z,sx,sy,sz

Where:
- TYPE is one of: CUBE, SPHERE, CYLINDER, CONE, TORUS - no other types allowed
- x,y,z are the LOCATION coordinates in 3D space (must be numbers)
- sx,sy,sz are the SCALE factors (must be numbers)

You MUST include ALL 7 parameters separated by commas.
You MUST respond with ONLY ONE LINE.
You MUST NOT include any other text.

Example: "Create a large sphere at the origin" → SPHERE,0,0,0,2,2,2
Example: "Make a tall cylinder" → CYLINDER,0,0,0,1,1,3
"""

    def __init__(
        self,
        llm: Optional[LLMClient] = None,
        mcp: Optional[MCPClient] = None,
        use_local: bool = True,
        base_url: Optional[str] = "http://localhost:8000/api/v0",
    ):
        """
        Initialize the BlenderAgentSimple with LLM and MCP clients.

        Args:
            llm: An optional pre-configured LLM client, otherwise a new one will be created
            mcp: An optional pre-configured MCP client, otherwise a new one will be created
            use_local: Whether to use a local LLM (True) or a remote API (False)
            base_url: Base URL for the local LLM API if using local LLM. If None and use_local=True,
                      defaults to "http://localhost:8000/api/v0"
        """
        self.llm = (
            llm
            if llm
            else LLMClient(
                use_local=use_local, system_prompt=self.SYSTEM_PROMPT, base_url=base_url
            )
        )
        self.mcp = mcp if mcp else MCPClient()

    def process_query(self, user_input: str) -> Dict[str, Any]:
        """
        Process a user query to create a 3D object.

        Args:
            user_input: User's description of the object to create

        Returns:
            Dict containing the result of the operation and any relevant data
        """
        try:
            # Get object creation instruction from LLM based on user input
            llm_response = self.llm.generate(user_input).strip()

            # Parse the LLM response
            obj_type, location, scale = self._parse_llm_response(llm_response)

            # Create the object in Blender
            result = self.mcp.create_object(
                type=obj_type,
                name=f"llm_generated_{obj_type.lower()}",
                location=location,
                scale=scale,
            )

            return {
                "status": "success",
                "llm_response": llm_response,
                "object_type": obj_type,
                "location": location,
                "scale": scale,
                "blender_result": result,
            }

        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "llm_response": llm_response if "llm_response" in locals() else None,
            }

    def _parse_llm_response(
        self, llm_response: str
    ) -> Tuple[str, Tuple[float, float, float], Tuple[float, float, float]]:
        """
        Parse the LLM response into object parameters.

        Args:
            llm_response: The response from the LLM in the format TYPE,x,y,z,sx,sy,sz

        Returns:
            Tuple containing (object_type, location_tuple, scale_tuple)

        Raises:
            ValueError: If the response format is invalid
        """
        try:
            # Simple parsing, assuming format: TYPE,x,y,z,sx,sy,sz
            parts = llm_response.split(",")
            if len(parts) != 7:
                raise ValueError(f"Expected 7 parts in response, got {len(parts)}")

            obj_type = parts[0].strip().upper()
            location = (float(parts[1]), float(parts[2]), float(parts[3]))
            scale = (float(parts[4]), float(parts[5]), float(parts[6]))

            return obj_type, location, scale

        except Exception as e:
            raise ValueError(
                f"Failed to parse LLM response: {e}. Raw response: {llm_response}"
            )
