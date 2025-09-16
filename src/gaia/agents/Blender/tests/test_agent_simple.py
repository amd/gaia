import pytest
import json
import logging
from gaia.agents.blender.agent_simple import BlenderAgentSimple
from gaia.llm.llm_client import LLMClient
from gaia.agents.blender.mcp.mcp_client import MCPClient

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


@pytest.fixture
def llm_client():
    logger.debug("Creating LLMClient for tests")
    # Using a test-specific system prompt
    system_prompt = """
You are a 3D modeling assistant for testing. IMPORTANT: For EACH user request, respond with EXACTLY ONE LINE in this format:
TYPE,x,y,z,sx,sy,sz

Where:
- TYPE is one of: CUBE, SPHERE, CYLINDER, CONE, TORUS
- x,y,z are the LOCATION coordinates in 3D space (where to place the object)
- sx,sy,sz are the SCALE factors (how large the object should be in each dimension)

For testing purposes:
- If asked for a cube, respond with: CUBE,1,2,3,0.5,1,1.5
- If asked for a sphere, respond with: SPHERE,0,0,0,1,1,1
- For any other request, respond with: CYLINDER,0,2,0,0.5,0.5,3
"""
    # Using local LLM for faster testing
    return LLMClient(system_prompt=system_prompt)


@pytest.fixture
def mcp_client():
    logger.debug("Creating MCPClient for tests")
    # Initialize with localhost for testing
    return MCPClient(host="localhost", port=9876)


@pytest.fixture
def blender_agent(llm_client, mcp_client):
    logger.debug("Creating BlenderAgentSimple for tests")
    return BlenderAgentSimple(llm=llm_client, mcp=mcp_client)


@pytest.fixture(autouse=True)
def clean_blender_scene(mcp_client):
    """Clear all objects from Blender scene before each test."""
    logger.debug("Cleaning Blender scene before test")
    # Blender Python code to delete all objects
    cleanup_code = """
import bpy

# Delete all objects
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()

# Report back how many objects are in the scene
print(f"Scene cleared. {len(bpy.data.objects)} objects remain.")
"""
    try:
        result = mcp_client.execute_code(cleanup_code)
        logger.debug(f"Cleanup result: {result}")
    except Exception as e:
        logger.error(f"Failed to clean scene: {e}")
        # Don't fail the test if cleanup fails, just log it

    # Proceed with the test
    yield

    # We could also clean up after each test if needed
    # But generally before-test cleanup is sufficient


def test_parse_llm_response(blender_agent):
    logger.debug("Running test_parse_llm_response")
    # Test valid response
    test_response = "CUBE,1,2,3,0.5,1,1.5"
    obj_type, location, scale = blender_agent._parse_llm_response(test_response)

    logger.debug(f"Parsed: type={obj_type}, location={location}, scale={scale}")

    assert obj_type == "CUBE"
    assert location == (1.0, 2.0, 3.0)
    assert scale == (0.5, 1.0, 1.5)

    # Test invalid response
    logger.debug("Testing invalid LLM response")
    with pytest.raises(ValueError):
        blender_agent._parse_llm_response("INVALID_FORMAT")


@pytest.mark.integration
def test_process_cube_query(blender_agent):
    logger.debug("Running test_process_cube_query")
    # Test processing a query for a cube
    result = blender_agent.process_query("Create a cube")

    # Debug print the actual result structure
    logger.debug(f"LLM response: {result.get('llm_response', 'N/A')}")
    logger.debug(f"Result structure: {json.dumps(result, indent=2, default=str)}")

    # Direct print for immediate visibility (useful for urgent debugging)
    print("\n\n==== TEST OUTPUT ====")
    print(f"Result status: {result['status']}")
    print(f"Blender result: {json.dumps(result['blender_result'], indent=2)}")
    print("=====================\n\n")

    # Verify result structure
    assert result["status"] == "success"
    assert result["object_type"] == "CUBE"
    assert result["location"] == (1.0, 2.0, 3.0)
    assert result["scale"] == (0.5, 1.0, 1.5)

    # Check that blender_result has the required structure
    assert "result" in result["blender_result"]
    assert "status" in result["blender_result"]
    assert result["blender_result"]["status"] == "success"
    # Check that the object was created with the right name
    assert "name" in result["blender_result"]["result"]
    # Blender may add suffixes like .001, .002 to make names unique
    assert result["blender_result"]["result"]["name"].startswith("llm_generated_cube")


@pytest.mark.integration
def test_process_sphere_query(blender_agent):
    logger.debug("Running test_process_sphere_query")
    # Test processing a query for a sphere
    result = blender_agent.process_query("Create a sphere at the origin")

    # Debug print the actual result structure
    logger.debug(f"LLM response: {result.get('llm_response', 'N/A')}")
    logger.debug(f"Result structure: {json.dumps(result, indent=2, default=str)}")

    # Verify result structure
    assert result["status"] == "success"
    assert result["object_type"] == "SPHERE"
    assert result["location"] == (0.0, 0.0, 0.0)
    assert result["scale"] == (1.0, 1.0, 1.0)

    # Check that blender_result has the required structure
    assert "result" in result["blender_result"]
    assert "status" in result["blender_result"]
    assert result["blender_result"]["status"] == "success"
    # Check that the object was created with the right name
    assert "name" in result["blender_result"]["result"]
    # Blender may add suffixes like .001, .002 to make names unique
    assert result["blender_result"]["result"]["name"].startswith("llm_generated_sphere")


@pytest.mark.integration
def test_agent_initialization():
    logger.debug("Running test_agent_initialization")
    # Test default initialization with default parameters
    agent = BlenderAgentSimple()

    # Verify clients were created properly
    assert isinstance(agent.llm, LLMClient)
    assert isinstance(agent.mcp, MCPClient)

    # Verify default system prompt is set
    logger.debug(f"System prompt: {agent.llm.system_prompt}")
    assert agent.llm.system_prompt == agent.SYSTEM_PROMPT


if __name__ == "__main__":
    """
    Main function to run tests directly from this file.
    Usage: python test_agent_v1.py [options]

    For unit tests only:
        python test_agent_v1.py -k "not integration"

    For integration tests only:
        python test_agent_v1.py -k "integration"

    For all tests:
        python test_agent_v1.py
    """
    import sys

    logger.debug("Starting test execution via main function")

    # Add custom arguments
    args = ["-xvs", "--log-cli-level=INFO", __file__]

    # Add any command line arguments passed to the script
    if len(sys.argv) > 1:
        args.extend(sys.argv[1:])
        logger.debug(f"Added command line arguments: {sys.argv[1:]}")

    logger.debug(f"Running pytest with args: {args}")
    # Run pytest with these arguments
    pytest.main(args)
