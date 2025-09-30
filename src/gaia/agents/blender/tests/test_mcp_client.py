# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.

import pytest
import sys
import logging
from contextlib import contextmanager
from gaia.mcp.blender_mcp_client import MCPClient, MCPError

logging.getLogger("asyncio").setLevel(logging.INFO)


@contextmanager
def suppress_client_logs():
    """Temporarily suppress MCPClient logs during tests with expected errors."""
    # Save the original log level
    client_logger = logging.getLogger("gaia.mcp.blender_mcp_client")
    original_level = client_logger.level

    # Set to a higher level to suppress expected errors
    client_logger.setLevel(logging.CRITICAL)

    try:
        yield
    finally:
        # Restore the original log level
        client_logger.setLevel(original_level)


@pytest.fixture(scope="module")
def mcp_client():
    """Create an MCP client and check server connectivity."""
    print("\n=== Initializing MCP Client ===")
    client = MCPClient()

    # Check if server is running
    print("Checking connection to Blender MCP server...")
    try:
        response = client.execute_code("import bpy")
        print("Connection successful! Server is running.")
    except MCPError as e:
        print(f"ERROR: {str(e)}")
        pytest.skip(
            "Blender MCP server is not running. Please start Blender with the MCP addon first."
        )

    return client


@pytest.fixture(scope="function")
def cleanup_test_objects(mcp_client):
    """Cleanup fixture to remove test objects after each test."""
    yield

    # Get all objects in the scene
    with suppress_client_logs():
        try:
            scene_info = mcp_client.get_scene_info()
            # Delete test objects (any object starting with "Test")
            deleted = 0
            for obj in scene_info["result"]["objects"]:
                if obj["name"].startswith("Test"):
                    try:
                        mcp_client.delete_object(obj["name"])
                        deleted += 1
                    except MCPError:
                        pass  # Ignore errors during cleanup

            if deleted > 0:
                print(f"Cleanup: {deleted} test objects deleted")
        except MCPError as e:
            print(f"Cleanup failed: {str(e)}")


@pytest.mark.asyncio
class TestBlenderMCP:

    async def test_connection(self, mcp_client):
        """Test connection to the Blender MCP server."""
        print("\n=== Test: Connection to server ===")
        response = mcp_client.execute_code("import bpy")
        assert response["status"] == "success"
        assert "executed" in response["result"]

    async def test_get_scene_info(self, mcp_client):
        """Test retrieving scene information."""
        print("\n=== Test: Get scene info ===")
        response = mcp_client.get_scene_info()
        assert response["status"] == "success"
        assert "name" in response["result"]
        assert "object_count" in response["result"]
        assert "objects" in response["result"]

        print(
            f"Scene: {response['result']['name']} with {response['result']['object_count']} objects"
        )

    async def test_create_object(self, mcp_client, cleanup_test_objects):
        """Test creating objects of different types."""
        print("\n=== Test: Create objects ===")
        test_objects = [
            {"type": "CUBE", "name": "TestCube", "location": (0, 0, 0)},
            {"type": "SPHERE", "name": "TestSphere", "location": (2, 0, 0)},
            {"type": "CYLINDER", "name": "TestCylinder", "location": (4, 0, 0)},
        ]

        for obj_params in test_objects:
            print(f"Creating {obj_params['type']} '{obj_params['name']}'")
            response = mcp_client.create_object(**obj_params)
            assert response["status"] == "success"
            assert response["result"]["name"] == obj_params["name"]
            assert response["result"]["type"] == "MESH"

            # Verify object was created with get_object_info
            info_response = mcp_client.get_object_info(obj_params["name"])
            assert info_response["status"] == "success"
            assert info_response["result"]["name"] == obj_params["name"]

    async def test_get_object_info(self, mcp_client, cleanup_test_objects):
        """Test getting detailed information about an object."""
        print("\n=== Test: Get object info ===")
        # Create test object
        mcp_client.create_object(type="CUBE", name="TestInfoCube", location=(0, 0, 3))

        # Get object info
        response = mcp_client.get_object_info("TestInfoCube")
        assert response["status"] == "success"
        assert response["result"]["name"] == "TestInfoCube"
        assert response["result"]["type"] == "MESH"
        assert "location" in response["result"]
        assert "rotation" in response["result"]
        assert "scale" in response["result"]
        assert "mesh" in response["result"]
        assert "vertices" in response["result"]["mesh"]
        assert "edges" in response["result"]["mesh"]
        assert "polygons" in response["result"]["mesh"]

        # Verify location is correct (with small epsilon for floating point)
        loc = response["result"]["location"]
        assert abs(loc[0] - 0) < 0.001
        assert abs(loc[1] - 0) < 0.001
        assert abs(loc[2] - 3) < 0.001

    async def test_modify_object(self, mcp_client, cleanup_test_objects):
        """Test modifying an existing object."""
        print("\n=== Test: Modify object ===")
        # Create test cube
        mcp_client.create_object(type="CUBE", name="TestModifyCube", location=(0, 0, 0))

        # Initial position check
        initial_info = mcp_client.get_object_info("TestModifyCube")
        assert initial_info["status"] == "success"
        assert abs(initial_info["result"]["location"][2] - 0) < 0.001

        # Modify object
        new_location = (1, 2, 3)
        new_scale = (2, 2, 2)
        print(f"Modifying 'TestModifyCube'")
        response = mcp_client.modify_object(
            name="TestModifyCube", location=new_location, scale=new_scale
        )

        assert response["status"] == "success"

        # Verify changes
        modified_info = mcp_client.get_object_info("TestModifyCube")
        assert modified_info["status"] == "success"

        # Check location (with small epsilon for floating point)
        loc = modified_info["result"]["location"]
        assert abs(loc[0] - new_location[0]) < 0.001
        assert abs(loc[1] - new_location[1]) < 0.001
        assert abs(loc[2] - new_location[2]) < 0.001

        # Check scale
        scale = modified_info["result"]["scale"]
        assert abs(scale[0] - new_scale[0]) < 0.001
        assert abs(scale[1] - new_scale[1]) < 0.001
        assert abs(scale[2] - new_scale[2]) < 0.001

    async def test_delete_object(self, mcp_client):
        """Test deleting an object."""
        print("\n=== Test: Delete object ===")
        # Create and delete test object
        mcp_client.create_object(type="CUBE", name="TestDeleteCube", location=(0, 0, 0))

        # Verify it exists
        info_response = mcp_client.get_object_info("TestDeleteCube")
        print(f"Info response: {info_response}")
        assert info_response["status"] == "success"

        # Delete object
        print("Deleting 'TestDeleteCube'")
        delete_response = mcp_client.delete_object("TestDeleteCube")
        assert delete_response["status"] == "success"
        assert delete_response["result"]["deleted"] == "TestDeleteCube"

        # Verify it's gone - expect an exception but suppress the logs
        with suppress_client_logs():
            try:
                mcp_client.get_object_info("TestDeleteCube")
                assert False, "Expected MCPError was not raised"
            except MCPError as e:
                assert "not found" in str(e)
                print(f"Verified object was deleted: {str(e)}")

    async def test_execute_code(self, mcp_client, cleanup_test_objects):
        """Test executing Python code in Blender."""
        print("\n=== Test: Execute Python code ===")
        # Execute code that creates an object programmatically
        code = """
import bpy
bpy.ops.mesh.primitive_cube_add(location=(0, 0, 5), scale=(2, 2, 2))
cube = bpy.context.active_object
cube.name = "TestScriptCube"
"""
        print("Executing code to create a cube")
        response = mcp_client.execute_code(code)
        assert response["status"] == "success"

        # Verify the object was created
        info_response = mcp_client.get_object_info("TestScriptCube")
        assert info_response["status"] == "success"
        assert info_response["result"]["name"] == "TestScriptCube"
        assert abs(info_response["result"]["location"][2] - 5) < 0.001

    async def test_material_creation(self, mcp_client, cleanup_test_objects):
        """Test creating and assigning materials."""
        print("\n=== Test: Material creation ===")
        # Create a test cube
        mcp_client.create_object(
            type="CUBE", name="TestMaterialCube", location=(0, 0, 0)
        )

        # Create and assign a material with Python code
        print("Creating red material and assigning to cube")
        material_code = """
import bpy
import random

# Create a new material with random color
mat = bpy.data.materials.new(name="TestMaterial")
mat.diffuse_color = (1.0, 0.0, 0.0, 1.0)  # Red

# Assign to the test cube
cube = bpy.data.objects.get("TestMaterialCube")
if cube and cube.data:
    if cube.data.materials:
        cube.data.materials[0] = mat
    else:
        cube.data.materials.append(mat)
"""
        response = mcp_client.execute_code(material_code)
        assert response["status"] == "success"

        # Verify material exists with another code execution
        verify_code = """
import bpy
mat = bpy.data.materials.get("TestMaterial")
if mat is None:
    raise Exception("TestMaterial not found")
"""
        verify_response = mcp_client.execute_code(verify_code)
        assert verify_response["status"] == "success"

    async def test_complex_scene_creation(self, mcp_client, cleanup_test_objects):
        """Test creating a more complex scene with multiple objects."""
        print("\n=== Test: Complex scene creation ===")
        # Create several objects at different positions
        objects = [
            {
                "type": "CUBE",
                "name": "TestComplex_Cube",
                "location": (0, 0, 0),
                "scale": (1, 1, 1),
            },
            {
                "type": "SPHERE",
                "name": "TestComplex_Sphere",
                "location": (3, 0, 0),
                "scale": (1.5, 1.5, 1.5),
            },
            {
                "type": "CYLINDER",
                "name": "TestComplex_Cylinder",
                "location": (0, 3, 0),
                "scale": (0.8, 0.8, 2),
            },
            {
                "type": "CONE",
                "name": "TestComplex_Cone",
                "location": (3, 3, 0),
                "scale": (1, 1, 2),
            },
        ]

        print(f"Creating {len(objects)} objects for complex scene")
        for obj in objects:
            response = mcp_client.create_object(**obj)
            assert response["status"] == "success"

        # Get scene info and verify all objects were created
        scene_info = mcp_client.get_scene_info()
        assert scene_info["status"] == "success"

        # Get the names of all objects in the scene
        scene_object_names = [obj["name"] for obj in scene_info["result"]["objects"]]

        # Verify all test objects are in the scene
        missing_objects = []
        for obj in objects:
            if obj["name"] not in scene_object_names:
                missing_objects.append(obj["name"])

        assert not missing_objects, f"Objects not found in scene: {missing_objects}"

    async def test_code_execution_error_handling(self, mcp_client):
        """Test error handling in code execution."""
        print("\n=== Test: Code execution error handling ===")
        # Execute invalid Python code
        invalid_code = """
import bpy
# This will raise a NameError
nonexistent_variable + 1
"""
        print("Executing code with error (expected to fail)")

        # Suppress logs for expected error
        with suppress_client_logs():
            try:
                mcp_client.execute_code(invalid_code)
                assert False, "Expected MCPError was not raised"
            except MCPError as e:
                error_message = str(e)
                # Check for enhanced error message with helpful context
                assert "nonexistent_variable" in error_message
                assert "Make sure to declare it before use" in error_message
                print(f"Received enhanced error: {error_message}")


if __name__ == "__main__":
    # Add command line arguments to pytest
    pytest_args = [
        __file__,
        "-vv",  # Verbose output
        # "-s",   # Show print statements
        # "-k test_delete_object",
        "--asyncio-mode=auto",
    ]

    print("Starting MCP tests...")
    # Run the tests
    exit_code = pytest.main(pytest_args)
    sys.exit(exit_code)
