import math
from typing import Dict, Tuple
from gaia.mcp.blender_mcp_client import MCPClient


class ObjectManager:
    """Manages Blender object operations."""

    def __init__(self, mcp: MCPClient):
        self.mcp = mcp

    def create_base_sphere_from_cube(self, radius: float = 6000) -> Dict:
        """Create a highly detailed sphere from a subdivided cube for planet Earth.
        The default radius is 6000 meters, which corresponds to Earth's approximate radius of 6000 km
        (at 1:10,000 scale, where 1 meter in Blender = 1 km in real life)."""

        def generate_code():
            return f"""
import bpy
import time

# Start with a clean slate but keep the default cube
for obj in bpy.data.objects:
    if obj.name != "Cube":
        obj.select_set(True)
    else:
        obj.select_set(False)
bpy.ops.object.delete()

# Use the default cube or create one if missing
bpy.ops.object.select_all(action='DESELECT')
cube = bpy.data.objects.get("Cube")
if not cube:
    bpy.ops.mesh.primitive_cube_add(size=2, enter_editmode=False, align='WORLD')
    cube = bpy.context.active_object
else:
    cube.select_set(True)
    bpy.context.view_layer.objects.active = cube

# Rename to Earth
cube.name = "Earth"
print("Working with cube named: " + cube.name)

# Enter edit mode
bpy.ops.object.mode_set(mode='EDIT')
print("Entered Edit mode")

# Select all vertices (matching step 4 in the documentation)
bpy.ops.mesh.select_all(action='SELECT')
print("Selected all vertices")

# Subdivide multiple times using Blender's subdivide operator
# Note: This matches the tutorial which uses W key + Shift+R for repeated subdivisions
# Using fewer subdivisions for better performance
for i in range(4):  # Reduced subdivisions temporarily
    bpy.ops.mesh.subdivide()
    print("Completed subdivision " + str(i+1) + "/4")
    time.sleep(0.1)  # Small delay to ensure operation completes

# Spherify the cube (equivalent to Shift+Alt+S in the tutorial)
bpy.ops.transform.tosphere(value=1.0)
print("Applied spherify transform")

# Exit edit mode
bpy.ops.object.mode_set(mode='OBJECT')
print("Exited Edit mode")

# Smooth shading
bpy.ops.object.shade_smooth()
print("Applied smooth shading")

# Get the active object (should be our sphere)
obj = bpy.context.active_object
if not obj:
    print("ERROR: No active object found!")
else:
    print("Working with object: " + obj.name)

# Set real-world scale - start very small for testing
# First ensure dimensions are reset
obj.dimensions = (2, 2, 2)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
print("Reset dimensions to 2x2x2, actual dimensions: " + str(obj.dimensions))

# Then scale up - using small test value first
obj.scale = ({radius}, {radius}, {radius})
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
print("Applied scale of {radius}, actual dimensions: " + str(obj.dimensions))

# Center the object (critical for visibility)
obj.location = (0, 0, 0)
print("Centered object at origin, location: " + str(obj.location))

# Return info for test verification
sphere = bpy.context.active_object
vertex_count = len(sphere.data.vertices) if sphere else 0
face_count = len(sphere.data.polygons) if sphere else 0
is_sphere = True if sphere and vertex_count > 100 else False

# Set result variable that will be returned by the MCP addon
result = {{
    "found": sphere is not None,
    "vertex_count": vertex_count,
    "face_count": face_count,
    "is_sphere": is_sphere,
    "dimensions": [float(d) for d in sphere.dimensions] if sphere else [],
    "location": [float(l) for l in sphere.location] if sphere else []
}}

print("Base Earth sphere created with " + str(vertex_count) + " vertices and " + str(face_count) + " faces")
"""

        code = generate_code()
        response = self.mcp.execute_code(code)

        # Extract the returned result from the MCP if available
        if response.get("result") and isinstance(response["result"], dict):
            return {"status": "success", **response["result"]}
        # Add stdout to the response for debugging
        if "stdout" in response:
            return {"status": "success", "message": response.get("stdout", "")}
        # Fallback
        return {"status": "success"}

    def add_sunlight(
        self, energy: float = 5.0, angle_degrees: Tuple[float, float] = (60, 45)
    ) -> Dict:
        """Add a sun light to illuminate the planet."""

        def generate_code():
            angle_x = math.radians(angle_degrees[0])
            angle_z = math.radians(angle_degrees[1])
            return f"""
import bpy
import math

bpy.ops.object.light_add(type='SUN', radius=1, align='WORLD')
sun = bpy.context.active_object
sun.name = "Sun"
sun.rotation_euler = ({angle_x}, 0, {angle_z})
sun.data.energy = {energy}

# Return info for test verification
result = {{
    "found": sun is not None,
    "is_sun": True if sun and sun.data.type == 'SUN' else False,
    "name": sun.name if sun else None
}}

print("Sunlight added")
"""

        response = self.mcp.execute_code(generate_code())
        # Extract the returned result from the MCP if available
        if response.get("result") and isinstance(response["result"], dict):
            return {"status": "success", **response["result"]}
        # Add stdout to the response for debugging
        if "stdout" in response:
            return {"status": "success", "message": response.get("stdout", "")}
        # Fallback
        return {"status": "success"}

    def load_earth_texture(
        self, texture_name: str, texture_path: str, is_noncolor: bool = False
    ) -> Dict:
        """Load a texture image for the Earth."""

        def generate_code():
            # Convert backslashes to forward slashes to avoid unicode escape issues
            safe_path = texture_path.replace("\\", "/")

            noncolor_code = """
if img:
    img.colorspace_settings.name = 'Non-Color'
"""
            return f"""
import bpy
import os

# Load image
img = bpy.data.images.load(r"{safe_path}")
if img:
    img.name = "{texture_name}"
    {"" if not is_noncolor else noncolor_code}

    # Return info for test verification
    result = {{
        "found": img is not None,
        "name": img.name if img else None,
        "filepath": img.filepath if img else None
    }}

    print(f"Texture '{texture_name}' loaded")
else:
    result = {{"found": False}}
    print(f"Failed to load texture from {safe_path}")
"""

        response = self.mcp.execute_code(generate_code())
        # Extract the returned result from the MCP if available
        if (
            response.get("status") == "success"
            and isinstance(response["result"], dict)
            and "found" in response["result"]
        ):
            return {"status": "success", **response["result"]}

        # Add stdout to the response for debugging
        if response.get("status") == "success" and "stdout" in response.get(
            "result", {}
        ):
            return {
                "status": "success",
                "message": response["result"].get("stdout", ""),
            }

        # Fallback for error
        if response.get("status") == "error":
            return {
                "status": "error",
                "message": response.get(
                    "message", "Unknown error in load_earth_texture"
                ),
            }

        # General fallback
        return {"status": "success", "found": False}

    def create_atmosphere_object(self) -> Dict:
        """Create the atmosphere object."""

        def generate_code():
            return """
import bpy

# Duplicate Earth for atmosphere
bpy.ops.object.select_all(action='DESELECT')
earth = bpy.data.objects.get("Earth")
if not earth:
    print("Error: Earth object not found")
    result = {"found": False, "error": "Earth object not found"}
    exit()

earth.select_set(True)
bpy.context.view_layer.objects.active = earth
bpy.ops.object.duplicate_move(OBJECT_OT_duplicate={"linked":False})
atm = bpy.context.active_object
atm.name = "Atmosphere"

# Return info for test verification
result = {
    "found": atm is not None,
    "name": atm.name if atm else None,
    "is_duplicate": True if atm and atm.data.users > 1 else False
}

print("Atmosphere object created")
"""

        response = self.mcp.execute_code(generate_code())
        # Extract the returned result from the MCP if available
        if response.get("result") and isinstance(response["result"], dict):
            return {"status": "success", **response["result"]}
        # Add stdout to the response for debugging
        if "stdout" in response:
            return {"status": "success", "message": response.get("stdout", "")}
        # Fallback
        return {"status": "success"}

    def create_clouds_object(self) -> Dict:
        """Create the clouds object."""

        def generate_code():
            return """
import bpy

# Duplicate Earth for clouds
bpy.ops.object.select_all(action='DESELECT')
atm = bpy.data.objects.get("Atmosphere")
if not atm:
    print("Error: Atmosphere object not found")
    result = {"found": False, "error": "Atmosphere object not found"}
    exit()

atm.select_set(True)
bpy.context.view_layer.objects.active = atm
bpy.ops.object.duplicate_move(OBJECT_OT_duplicate={"linked":False})
clouds = bpy.context.active_object
clouds.name = "Clouds"

# Scale up slightly to place above the Earth - exactly as in tutorial
clouds.scale = (1.001, 1.001, 1.001)  # Tutorial uses 1.001 scale
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

# Return info for test verification
result = {
    "found": clouds is not None,
    "name": clouds.name if clouds else None
}

print("Clouds object created")
"""

        response = self.mcp.execute_code(generate_code())
        # Extract the returned result from the MCP if available
        if response.get("result") and isinstance(response["result"], dict):
            return {"status": "success", **response["result"]}
        # Add stdout to the response for debugging
        if "stdout" in response:
            return {"status": "success", "message": response.get("stdout", "")}
        # Fallback
        return {"status": "success"}
