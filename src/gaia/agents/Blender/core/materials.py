from typing import Dict
from gaia.mcp.blender_mcp_client import MCPClient


class MaterialManager:
    """Manages Blender material operations."""

    def __init__(self, mcp: MCPClient):
        self.mcp = mcp

    def create_ground_material(
        self, ground_texture_name: str, maps_texture_name: str
    ) -> Dict:
        """Create the ground material with separate land and water shaders, using displacement."""

        def generate_code():
            return f"""
import bpy

# Get the Earth object
earth = bpy.data.objects.get("Earth")
if not earth:
    print("Error: Earth object not found")
    exit()

# Create new material
ground_mat = bpy.data.materials.new(name="ground")
ground_mat.use_nodes = True
earth.data.materials.append(ground_mat)

# Get material nodes and links
nodes = ground_mat.node_tree.nodes
links = ground_mat.node_tree.links

# Clear default nodes
for node in nodes:
    nodes.remove(node)

# Create nodes for ground material
output = nodes.new(type='ShaderNodeOutputMaterial')
output.location = (800, 0)

# Add texture image for Earth ground
tex_ground = nodes.new(type='ShaderNodeTexImage')
tex_ground.location = (-600, 200)
tex_ground.image = bpy.data.images.get("{ground_texture_name}")
tex_ground.projection = 'SPHERE'
tex_ground.interpolation = 'Linear'  # Updated to Linear as shown in screenshot

# Add texture coordinate
tex_coord = nodes.new(type='ShaderNodeTexCoord')
tex_coord.location = (-900, 0)

# Earth maps (water mask and displacement)
tex_maps = nodes.new(type='ShaderNodeTexImage')
tex_maps.location = (-600, -200)
tex_maps.image = bpy.data.images.get("{maps_texture_name}")
tex_maps.projection = 'SPHERE'
tex_maps.interpolation = 'Linear'  # Already set to Linear

# Separate RGB for maps
separate_rgb = nodes.new(type='ShaderNodeSeparateRGB')
separate_rgb.location = (-300, -200)

# Single Principled BSDF with values matching screenshot
principled = nodes.new(type='ShaderNodeBsdfPrincipled')
principled.location = (400, 200)
principled.inputs['Metallic'].default_value = 0.030  # As shown in screenshot
principled.inputs['Roughness'].default_value = 0.500  # As shown in screenshot
principled.inputs['IOR'].default_value = 1.500  # As shown in screenshot
principled.inputs['Alpha'].default_value = 1.000  # As shown in screenshot

# Alternative approach - keep both land and water shaders as before
# Land material
land_shader = nodes.new(type='ShaderNodeBsdfPrincipled')
land_shader.location = (100, 200)
land_shader.inputs['Specular'].default_value = 0.0
land_shader.inputs['Roughness'].default_value = 1.0

# Water material
water_shader = nodes.new(type='ShaderNodeBsdfPrincipled')
water_shader.location = (100, -100)
water_shader.inputs['Roughness'].default_value = 0.4
water_shader.inputs['IOR'].default_value = 1.333

# Mix shader
mix_shader = nodes.new(type='ShaderNodeMixShader')
mix_shader.location = (500, 0)

# Displacement node
displace = nodes.new(type='ShaderNodeDisplacement')
displace.location = (500, -300)
displace.inputs['Scale'].default_value = 0.005  # Match tutorial value for displacement

# Connect nodes
# Option 1: Using single Principled BSDF (as shown in screenshot)
links.new(tex_coord.outputs['Generated'], tex_ground.inputs['Vector'])
links.new(tex_coord.outputs['Generated'], tex_maps.inputs['Vector'])
links.new(tex_ground.outputs['Color'], principled.inputs['Base Color'])
links.new(tex_maps.outputs['Color'], separate_rgb.inputs['Image'])
links.new(separate_rgb.outputs['R'], displace.inputs['Height'])  # R (red) channel is height
links.new(principled.outputs['BSDF'], output.inputs['Surface'])
links.new(displace.outputs['Displacement'], output.inputs['Displacement'])

# Set material displacement method to match tutorial
ground_mat.displacement_method = 'DISPLACEMENT'

print("Ground material created exactly as shown in screenshot")
"""

        return self.mcp.execute_code(generate_code())

    def create_atmosphere_material(self) -> Dict:
        """Create the atmosphere material with volume scatter."""

        def generate_code():
            return """
import bpy

# Create atmosphere material
atm_mat = bpy.data.materials.new(name="atmosphere")
atm_mat.use_nodes = True

# Get material nodes
nodes = atm_mat.node_tree.nodes
links = atm_mat.node_tree.links

# Clear default nodes
for node in nodes:
    nodes.remove(node)

# Create nodes for atmosphere material as shown in tutorial
output = nodes.new(type='ShaderNodeOutputMaterial')
output.location = (800, 0)

# Add texture coordinate for atmosphere
tex_coord = nodes.new(type='ShaderNodeTexCoord')
tex_coord.location = (-900, 0)

# Add volume scatter - match tutorial color exactly
volume_scatter = nodes.new(type='ShaderNodeVolumeScatter')
volume_scatter.location = (500, 200)
volume_scatter.inputs['Color'].default_value = (0.3, 0.6, 1.0, 1.0)  # Blue color from tutorial

# Value for atmosphere thickness - 1% of planet radius as in tutorial
thickness = nodes.new(type='ShaderNodeValue')
thickness.location = (-600, -300)
thickness.outputs[0].default_value = 0.01  # 1% of planet radius as specified in tutorial

# Vector length for atmosphere density gradient
vec_math = nodes.new(type='ShaderNodeVectorMath')
vec_math.location = (-600, 0)
vec_math.operation = 'LENGTH'

# Subtract 1 to get 0 at surface level
math_sub = nodes.new(type='ShaderNodeMath')
math_sub.location = (-400, 0)
math_sub.operation = 'SUBTRACT'
math_sub.inputs[1].default_value = 1.0

# Divide by thickness to normalize distance - exactly as in tutorial
math_div = nodes.new(type='ShaderNodeMath')
math_div.location = (-200, 0)
math_div.operation = 'DIVIDE'
math_div.use_clamp = True

# Multiply by 15 for density adjustment - exact value from tutorial
math_mul1 = nodes.new(type='ShaderNodeMath')
math_mul1.location = (0, 0)
math_mul1.operation = 'MULTIPLY'
math_mul1.inputs[1].default_value = 15.0  # Tutorial uses exactly 15

# Power for exponential falloff - uses e (Euler's number) in tutorial
math_pow = nodes.new(type='ShaderNodeMath')
math_pow.location = (200, 0)
math_pow.operation = 'POWER'
math_pow.inputs[1].default_value = 1.0  # Power of e (Euler's number)

# Multiply by 0.05 for final density - exact value from tutorial
math_mul2 = nodes.new(type='ShaderNodeMath')
math_mul2.location = (400, 0)
math_mul2.operation = 'MULTIPLY'
math_mul2.inputs[1].default_value = 0.05  # Tutorial uses exactly 0.05

# Displacement for atmosphere
displace = nodes.new(type='ShaderNodeDisplacement')
displace.location = (500, -200)
displace.inputs['Scale'].default_value = 1.0

# Connect nodes exactly as demonstrated in tutorial
links.new(tex_coord.outputs['Object'], vec_math.inputs[0])
links.new(vec_math.outputs[0], math_sub.inputs[0])
links.new(math_sub.outputs[0], math_div.inputs[0])
links.new(thickness.outputs[0], math_div.inputs[1])
links.new(math_div.outputs[0], math_mul1.inputs[0])
links.new(math_mul1.outputs[0], math_pow.inputs[0])
links.new(math_pow.outputs[0], math_mul2.inputs[0])
links.new(math_mul2.outputs[0], volume_scatter.inputs['Density'])
links.new(thickness.outputs[0], displace.inputs['Scale'])
links.new(volume_scatter.outputs['Volume'], output.inputs['Volume'])
links.new(displace.outputs['Displacement'], output.inputs['Displacement'])

# Set material displacement method as in tutorial
atm_mat.displacement_method = 'DISPLACEMENT'

print("Atmosphere material created exactly as in tutorial")
"""

        return self.mcp.execute_code(generate_code())

    def create_clouds_material(self, clouds_texture_name: str) -> Dict:
        """Create the clouds material with subsurface scattering."""

        def generate_code():
            return f"""
import bpy

# Create clouds material
clouds_mat = bpy.data.materials.new(name="clouds")
clouds_mat.use_nodes = True

# Get material nodes
nodes = clouds_mat.node_tree.nodes
links = clouds_mat.node_tree.links

# Clear default nodes
for node in nodes:
    nodes.remove(node)

# Create nodes for clouds material - match tutorial exactly
output = nodes.new(type='ShaderNodeOutputMaterial')
output.location = (1000, 0)

# Add texture coordinate for clouds
tex_coord = nodes.new(type='ShaderNodeTexCoord')
tex_coord.location = (-900, 0)

# Add cloud texture
tex_clouds = nodes.new(type='ShaderNodeTexImage')
tex_clouds.location = (-600, 0)
tex_clouds.image = bpy.data.images.get("{clouds_texture_name}")
tex_clouds.projection = 'SPHERE'
tex_clouds.interpolation = 'Linear'  # Match tutorial setting

# Gamma correction for cloud texture - exactly 0.5 as in tutorial
gamma = nodes.new(type='ShaderNodeGamma')
gamma.location = (-400, 0)
gamma.inputs['Gamma'].default_value = 0.5  # Exactly as in tutorial

# Second gamma correction - exactly 0.9 as in tutorial
gamma2 = nodes.new(type='ShaderNodeGamma')
gamma2.location = (-200, 0)
gamma2.inputs['Gamma'].default_value = 0.9  # Exactly as in tutorial

# Add Transparent BSDF
transparent = nodes.new(type='ShaderNodeBsdfTransparent')
transparent.location = (400, 100)

# Add Subsurface Scattering BSDF - exactly as in tutorial
subsurface = nodes.new(type='ShaderNodeSubsurfaceScattering')
subsurface.location = (400, -100)
subsurface.inputs['Radius'].default_value = (1, 1, 1)  # Tutorial setting

# Multiply for cloud intensity - exactly 5.0 as in tutorial
math_mul = nodes.new(type='ShaderNodeMath')
math_mul.location = (0, -200)
math_mul.operation = 'MULTIPLY'
math_mul.inputs[1].default_value = 5.0  # Exactly as in tutorial

# Power for cloud exponential control - as in tutorial
math_pow = nodes.new(type='ShaderNodeMath')
math_pow.location = (200, -200)
math_pow.operation = 'POWER'
math_pow.inputs[1].default_value = 1.0  # Tutorial setting

# Mix shader
mix_shader = nodes.new(type='ShaderNodeMixShader')
mix_shader.location = (700, 0)

# Displacement node - exactly 0.005 as in tutorial
displace = nodes.new(type='ShaderNodeDisplacement')
displace.location = (700, -300)
displace.inputs['Scale'].default_value = 0.005  # Exactly as in tutorial

# Connect nodes exactly as in tutorial
links.new(tex_coord.outputs['Generated'], tex_clouds.inputs['Vector'])
links.new(tex_clouds.outputs['Color'], gamma.inputs['Color'])
links.new(gamma.outputs['Color'], gamma2.inputs['Color'])
links.new(gamma2.outputs['Color'], subsurface.inputs['Color'])
links.new(gamma2.outputs['Color'], math_mul.inputs[0])
links.new(math_mul.outputs[0], math_pow.inputs[0])
links.new(math_pow.outputs[0], mix_shader.inputs['Fac'])
links.new(transparent.outputs['BSDF'], mix_shader.inputs[1])
links.new(subsurface.outputs['BSSRDF'], mix_shader.inputs[2])
links.new(tex_clouds.outputs['Color'], displace.inputs['Height'])
links.new(mix_shader.outputs['Shader'], output.inputs['Surface'])
links.new(displace.outputs['Displacement'], output.inputs['Displacement'])

# Set material displacement method as in tutorial
clouds_mat.displacement_method = 'BUMP'  # Tutorial setting

print("Clouds material created exactly as in tutorial")
"""

        return self.mcp.execute_code(generate_code())

    def set_material_color(self, object_name: str, color: tuple = (1, 0, 0, 1)) -> Dict:
        """
        Set the material color for an object. Creates a new material if one doesn't exist.

        Args:
            object_name: Name of the object to modify
            color: RGBA color values as tuple (red, green, blue, alpha), values from 0-1

        Returns:
            Dictionary with the operation result
        """

        def generate_code():
            return f"""
import bpy

result = {{"status": "processing"}}

# Get the object
obj = bpy.data.objects.get("{object_name}")
if not obj:
    result = {{"status": "error", "error": f"Object '{object_name}' not found"}}
else:
    # Create a new material if needed
    mat_name = "{object_name}_material"
    if mat_name in bpy.data.materials:
        mat = bpy.data.materials[mat_name]
    else:
        mat = bpy.data.materials.new(name=mat_name)

    # Enable nodes for the material
    mat.use_nodes = True
    nodes = mat.node_tree.nodes

    # Clear existing nodes
    for node in nodes:
        nodes.remove(node)

    # Create new nodes
    output = nodes.new(type='ShaderNodeOutputMaterial')
    output.location = (300, 0)

    bsdf = nodes.new(type='ShaderNodeBsdfPrincipled')
    bsdf.location = (0, 0)

    # Set color - use correct format for Blender 4.0
    bsdf.inputs["Base Color"].default_value = {color}

    # Also set viewport display color for material
    mat.diffuse_color = {color}

    # Connect nodes
    mat.node_tree.links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    # Assign material to object
    if len(obj.data.materials) == 0:
        obj.data.materials.append(mat)
    else:
        obj.data.materials[0] = mat

    # Set the active material slot
    obj.active_material_index = 0
    obj.active_material = mat

    # Set the render engine to show materials properly
    # For Blender 4.0, use valid enum values
    if hasattr(bpy.context.scene, 'render'):
        if hasattr(bpy.context.scene.render, 'engine'):
            current_engine = bpy.context.scene.render.engine
            if current_engine not in ['CYCLES', 'BLENDER_EEVEE_NEXT', 'BLENDER_WORKBENCH']:
                try:
                    bpy.context.scene.render.engine = 'CYCLES'
                except Exception as e:
                    print(f"Could not set render engine: {{e}}")

    # Force update of all 3D viewports
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()

    result = {{
        "status": "success",
        "message": "Material color set successfully",
        "debug_info": {{
            "object_name": obj.name,
            "material_name": mat.name,
            "color_set": list({color}),
            "material_slots": len(obj.material_slots),
            "active_material": obj.active_material.name if obj.active_material else None,
            "render_engine": bpy.context.scene.render.engine if hasattr(bpy.context.scene, 'render') else "unknown"
        }}
    }}

result
"""

        return self.mcp.execute_code(generate_code())


def generate_material_assignment_code(
    object_name: str, material_name: str = None, color: tuple = (0.8, 0.8, 0.8, 1.0)
) -> str:
    """
    Generates Python code to create a material and assign it to an object with proper error handling.

    Args:
        object_name: Name of the object to assign material to
        material_name: Name for the new material (default: derived from object name)
        color: RGBA color tuple (r, g, b, a) with values from 0.0 to 1.0

    Returns:
        String containing Python code that can be executed in Blender
    """
    if material_name is None:
        material_name = f"{object_name}_material"

    # Build a code block with proper error handling
    code = f"""
import bpy

result = {{"status": "processing"}}

# Get the object
obj = bpy.data.objects.get('{object_name}')
if not obj:
    result = {{"status": "error", "message": "Object '{object_name}' not found"}}
else:
    # Create the material if it doesn't exist
    mat = bpy.data.materials.get('{material_name}')
    if not mat:
        mat = bpy.data.materials.new(name="{material_name}")

    # Set the color
    mat.diffuse_color = {color}

    # Assign material to object
    if len(obj.data.materials) == 0:
        obj.data.materials.append(mat)
    else:
        obj.data.materials[0] = mat

    result = {{
        "status": "success",
        "message": "Material created and assigned",
        "object": "{object_name}",
        "material": "{material_name}"
    }}

result
"""
    return code


def generate_materials_for_all_objects_code() -> str:
    """
    Generates Python code that creates default materials for all objects in the scene
    that don't already have materials assigned.

    Returns:
        String containing Python code that can be executed in Blender
    """
    code = """
import bpy

result = {"status": "processing", "created": 0, "objects": []}

# Process all objects in the scene
for obj in bpy.data.objects:
    # Skip objects that can't have materials or already have materials
    if obj.type not in {'MESH', 'CURVE', 'SURFACE', 'META', 'FONT'} or len(obj.material_slots) > 0 and obj.active_material:
        continue

    # Create a new material for this object
    mat_name = f"{obj.name}_material"
    mat = bpy.data.materials.new(name=mat_name)

    # Set default color based on object name (for consistent results)
    import hashlib
    name_hash = int(hashlib.md5(obj.name.encode()).hexdigest(), 16)
    r = ((name_hash & 0xFF0000) >> 16) / 255.0
    g = ((name_hash & 0x00FF00) >> 8) / 255.0
    b = (name_hash & 0x0000FF) / 255.0
    mat.diffuse_color = (r, g, b, 1.0)

    # Assign the material to the object
    obj.data.materials.append(mat)

    result["objects"].append({"name": obj.name, "material": mat_name})
    result["created"] += 1

result["status"] = "success"
result["message"] = f"Created materials for {result['created']} objects"
result
"""
    return code
