from typing import Dict
from gaia.mcp.blender_mcp_client import MCPClient


class ViewManager:
    """Manages Blender viewport and display settings."""

    def __init__(self, mcp: MCPClient):
        self.mcp = mcp

    def adjust_for_large_scale(
        self, clip_end: float = 100000, orbit_selection: bool = True
    ) -> Dict:
        """Adjust viewport settings to properly view large-scale objects like Earth.

        Args:
            clip_end: The maximum view distance to set for the 3D viewport (default: 100000)
            orbit_selection: Whether to enable orbit around selection (default: True, but may not work in all Blender versions)
        """

        def generate_code():
            return f"""
import bpy

# Adjust clip distance for all 3D viewports
for area in bpy.context.screen.areas:
    if area.type == 'VIEW_3D':
        for space in area.spaces:
            if space.type == 'VIEW_3D':
                # Adjust clip end distance
                space.clip_end = {clip_end}
                # Keep clip start reasonable
                space.clip_start = 0.1
                print(f"Set view clip end to {clip_end}")

# Focus on Earth object if it exists
earth = bpy.data.objects.get("Earth")
if earth:
    # Select the Earth
    bpy.ops.object.select_all(action='DESELECT')
    earth.select_set(True)
    bpy.context.view_layer.objects.active = earth

    # Frame selected (equivalent to Numpad '.') - uses the new context override method
    for area in bpy.context.screen.areas:
        if area.type == 'VIEW_3D':
            for region in area.regions:
                if region.type == 'WINDOW':
                    # Get the current context and override it using 'with'
                    with bpy.context.temp_override(area=area, region=region):
                        # No override dict needed as parameter anymore
                        bpy.ops.view3d.view_selected()
                        print("Focused view on Earth object")
                        break

# Return status
result = {{
    "clip_end_set": {clip_end},
    "focused_on_object": earth is not None
}}

print("View settings adjusted for large-scale objects")
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

    def set_shading_tab(self) -> Dict:
        """Switch to the Shading tab/workspace in Blender."""

        def generate_code():
            return """
import bpy

# Try to switch to the Shading tab in Blender
success = False

# First attempt: Try using the workspace API (Blender 2.8+)
try:
    # Get the 'Shading' workspace if it exists
    shading_ws = None
    for ws in bpy.data.workspaces:
        if 'Shading' in ws.name:
            shading_ws = ws
            break

    # Set active workspace to Shading
    if shading_ws:
        window = bpy.context.window
        window.workspace = shading_ws
        success = True
        print("Switched to Shading workspace")
    else:
        print("Shading workspace not found")
except Exception as e:
    print(f"Could not switch workspace: {str(e)}")

# Second attempt: If workspace API failed, try to switch editor type to NODE_EDITOR
if not success:
    try:
        # Find a 3D view area to replace with the node editor
        for area in bpy.context.screen.areas:
            if area.type == 'VIEW_3D':
                # Change the area type to node editor (similar to Shading tab)
                area.type = 'NODE_EDITOR'

                # Set the area to shader nodes
                for space in area.spaces:
                    if space.type == 'NODE_EDITOR':
                        space.shader_type = 'OBJECT'
                        space.show_shading = True

                success = True
                print("Converted 3D view to Shader editor")
                break
    except Exception as e:
        print(f"Could not switch to node editor: {str(e)}")

# Return status
result = {
    "success": success
}

print("Attempted to switch to Shading tab")
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
