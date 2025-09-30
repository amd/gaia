from typing import Dict, Optional
from gaia.mcp.blender_mcp_client import MCPClient


class SceneManager:
    """Manages Blender scene operations."""

    def __init__(self, mcp: MCPClient):
        self.mcp = mcp

    def reset_scene(self) -> Dict:
        """Reset Blender to a clean state, removing all objects and unused data."""

        def generate_reset_code():
            return """
import bpy

# Select all objects
bpy.ops.object.select_all(action='SELECT')

# Delete all selected objects
bpy.ops.object.delete()

# Clear orphaned data
for block in bpy.data.meshes:
    if block.users == 0:
        bpy.data.meshes.remove(block)

for block in bpy.data.materials:
    if block.users == 0:
        bpy.data.materials.remove(block)

for block in bpy.data.textures:
    if block.users == 0:
        bpy.data.textures.remove(block)

for block in bpy.data.images:
    if block.users == 0:
        bpy.data.images.remove(block)

# Add default cube back (similar to Blender's default startup)
bpy.ops.mesh.primitive_cube_add(size=2, enter_editmode=False, align='WORLD', location=(0, 0, 0))

# Reset the default view
for area in bpy.context.screen.areas:
    if area.type == 'VIEW_3D':
        for region in area.regions:
            if region.type == 'WINDOW':
                # Updated context override syntax for Blender 4.4
                with bpy.context.temp_override(area=area, region=region):
                    bpy.ops.view3d.view_all()

# Return info for the test verification
has_default_cube = False
for obj in bpy.context.scene.objects:
    if obj.name.startswith("Cube") and obj.type == 'MESH':
        has_default_cube = True
        break

object_count = len(bpy.context.scene.objects)

# Set result variable that will be returned by the MCP addon
result = {
    "has_default_cube": has_default_cube,
    "object_count": object_count
}

print("Blender scene has been reset with default cube")
"""

        response = self.mcp.execute_code(generate_reset_code())
        # Extract the returned result from the MCP if available
        if response.get("result") and isinstance(response["result"], dict):
            return {"status": "success", **response["result"]}
        # Add stdout to the response for debugging
        if "stdout" in response:
            return {"status": "success", "message": response.get("stdout", "")}
        # Fallback
        return {"status": "success"}

    def clear_scene(self) -> Dict:
        """Remove all objects from the current Blender scene."""

        def generate_clear_code():
            return """
import bpy

# Select all objects
bpy.ops.object.select_all(action='SELECT')
# Delete all selected objects
bpy.ops.object.delete()

# Return info about the cleared scene
object_count = len(bpy.context.scene.objects)
result = {
    "object_count": object_count,
    "message": "Scene cleared successfully"
}

print("Scene cleared successfully")
"""

        response = self.mcp.execute_code(generate_clear_code())
        # Extract the returned result from the MCP if available
        if response.get("result") and isinstance(response["result"], dict):
            return {"status": "success", **response["result"]}
        # Add stdout to the response for debugging
        if "stdout" in response:
            return {"status": "success", "message": response.get("stdout", "")}
        # Fallback
        return {"status": "success"}

    def set_world_background_black(self) -> Dict:
        """Set the world background to black."""

        def generate_code():
            return """
import bpy

world = bpy.data.worlds["World"]
world.use_nodes = True
bg_node = world.node_tree.nodes["Background"]
bg_node.inputs[0].default_value = (0, 0, 0, 1)  # Black
bg_node.inputs[1].default_value = 0  # Strength to 0

# Verify the world background is black
bg_color = world.node_tree.nodes["Background"].inputs[0].default_value[:3]
is_black = all(c < 0.01 for c in bg_color)

# Set result variable that will be returned by the MCP addon
result = {
    "is_black": is_black,
    "color": list(bg_color) if world else None
}

print("World background set to black")
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


def generate_scene_diagnosis_code() -> str:
    """
    Generates Python code that provides comprehensive diagnostics about the current Blender scene.
    This includes information about all objects, materials, and potential issues.

    Returns:
        String containing Python code that can be executed in Blender
    """
    code = """
import bpy

result = {"status": "processing", "objects": []}

# Get all objects in the scene
for obj in bpy.data.objects:
    obj_info = {
        "name": obj.name,
        "type": obj.type,
        "visible": obj.visible_get(),
        "location": list(obj.location),
        "scale": list(obj.scale),
        "material_slots": len(obj.material_slots),
        "has_materials": len(obj.material_slots) > 0 and obj.active_material is not None
    }

    # Add material info if available
    if obj.active_material:
        obj_info["active_material"] = {
            "name": obj.active_material.name,
            "has_diffuse_color": hasattr(obj.active_material, "diffuse_color"),
        }
        if hasattr(obj.active_material, "diffuse_color"):
            obj_info["active_material"]["diffuse_color"] = list(obj.active_material.diffuse_color)

    result["objects"].append(obj_info)

# Add overall scene info
result["object_count"] = len(result["objects"])
result["material_count"] = len(bpy.data.materials)

# Check for common issues
result["issues"] = []

# Check for objects without materials that typically need them
for obj in result["objects"]:
    if obj["type"] in ["MESH", "CURVE", "SURFACE", "META", "FONT"] and not obj.get("has_materials", False):
        result["issues"].append({
            "type": "missing_material",
            "object": obj["name"],
            "message": f"Object '{obj['name']}' has no material assigned"
        })

# Check for objects with unusual scales (potentially errors)
for obj in result["objects"]:
    scales = obj.get("scale", [1, 1, 1])
    if any(s < 0.0001 or s > 1000 for s in scales):
        result["issues"].append({
            "type": "unusual_scale",
            "object": obj["name"],
            "scale": scales,
            "message": f"Object '{obj['name']}' has unusual scale: {scales}"
        })

result["status"] = "success"
result
"""
    return code
