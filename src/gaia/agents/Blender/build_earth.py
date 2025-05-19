import socket
import json
import math
import os
from pprint import pprint
from typing import Dict, List, Tuple, Union, Optional, Any
from gaia.agents.Blender.core import BlenderMCP, SceneManager, MaterialManager, RenderManager, ObjectManager, ViewManager

class BlenderEarthBuilder:
    """High-level builder for creating Earth in Blender."""

    def __init__(self, host: str = 'localhost', port: int = 9876, debug: bool = False):
        """Initialize the Blender Earth builder with all managers."""
        self.mcp = BlenderMCP(host, port, debug=debug)
        self.scene = SceneManager(self.mcp)
        self.materials = MaterialManager(self.mcp)
        self.rendering = RenderManager(self.mcp)
        self.objects = ObjectManager(self.mcp)
        self.view = ViewManager(self.mcp)

    def create_complete_earth(self, texture_dir: str) -> Dict:
        """Create the complete Earth planet with all components."""
        results = []

        try:
            # Set up the scene
            print("\n--- STEP 1: SCENE RESET ---")
            results.append(self.scene.reset_scene())

            # Create base planet with error handling
            print("\n--- STEP 2: CREATING EARTH SPHERE ---")
            sphere_result = self.objects.create_base_sphere_from_cube()
            results.append(sphere_result)

            # Adjust view settings immediately after creating the large sphere
            print("\n--- STEP 2b: ADJUSTING VIEW FOR LARGE-SCALE OBJECT ---")
            view_result = self.view.adjust_for_large_scale(clip_end=100000, orbit_selection=True)
            results.append(view_result)

            # Set up shading and background
            print("\n--- STEP 3: SETTING UP SHADING ENVIRONMENT ---")
            # Set active tab to Shading (would need to implement this in ViewManager)
            results.append(self.view.set_shading_tab())
            # Set world background to black
            results.append(self.scene.set_world_background_black())

            # Add lighting
            print("\n--- STEP 4: ADDING SUNLIGHT ---")
            results.append(self.objects.add_sunlight(energy=5.0, angle_degrees=(60, 45)))

            # Load textures with updated filenames
            print("\n--- STEP 5: LOADING TEXTURES ---")
            results.append(self.objects.load_earth_texture("earth_ground", os.path.join(texture_dir, "Blue_Marble_Ground_21k.jpg")))
            results.append(self.objects.load_earth_texture("earth_maps", os.path.join(texture_dir, "Earth_Maps_21k.tif"), True))
            results.append(self.objects.load_earth_texture("earth_clouds", os.path.join(texture_dir, "Earth_Clouds_21k.jpg")))

            # Create materials and objects
            print("\n--- STEP 6: CREATING MATERIALS AND OBJECTS ---")
            results.append(self.materials.create_ground_material("earth_ground", "earth_maps"))
            results.append(self.objects.create_atmosphere_object())
            results.append(self.materials.create_atmosphere_material())
            results.append(self.objects.create_clouds_object())
            results.append(self.materials.create_clouds_material("earth_clouds"))

            # Set up rendering
            print("\n--- STEP 7: SETTING UP RENDERING ---")
            results.append(self.rendering.setup_volume_rendering())
            results.append(self.rendering.setup_color_grading())
            results.append(self.rendering.setup_camera())
            results.append(self.rendering.setup_render_settings())

            print("\n--- EARTH CREATION COMPLETE ---")

        except Exception as e:
            error_info = {"status": "error", "message": str(e)}
            results.append(error_info)
            print("\nERROR creating sphere:")
            print(str(e))

        return {"creation_results": results}

def main():
    # Example: Using the BlenderEarthBuilder to create an Earth planet
    import os

    # Initialize the builder
    blender = BlenderEarthBuilder(host='localhost', port=9876)

    # Create Earth using the builder
    texture_dir = r"C:\Users\kalin\Documents\Blender\Kruger Planet Course Equirectangular Maps (43k)\Equirectangular Maps"
    result = blender.create_complete_earth(texture_dir)
    print(result)

if __name__ == "__main__":
    main()
