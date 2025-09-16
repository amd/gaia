# GAIA Blender Agent

GAIA provides a powerful Blender agent that enables natural language interaction with Blender for 3D scene creation and modification. The agent can create objects, apply materials, manipulate transformations, and manage scenes through conversational commands.

## Overview

The GAIA Blender agent bridges the gap between natural language and 3D modeling by:

- **Natural Language Processing**: Understanding complex 3D scene creation requests
- **Automated Planning**: Breaking down complex tasks into manageable steps
- **Real-time Execution**: Direct communication with Blender through MCP (Model Context Protocol)
- **Interactive Workflows**: Supporting both example-based learning and custom queries

## Key Features

### Scene Management
- Clear scenes and remove objects
- Get scene information and object listings
- Manage scene hierarchy and organization

### Object Creation
- **Primitive Objects**: Cubes, spheres, cylinders, cones, and torus objects
- **Positioning**: Precise placement using 3D coordinates
- **Scaling**: Custom sizing and proportions
- **Multiple Objects**: Create and arrange multiple objects in complex scenes

### Material System
- **Color Assignment**: Apply RGBA colors to objects
- **Material Properties**: Set material characteristics and appearance
- **Visual Consistency**: Maintain material standards across objects

### Interactive Planning
- **Multi-step Execution**: Break complex requests into logical steps
- **Automatic Planning**: AI-driven task decomposition
- **Progress Tracking**: Monitor execution through each step
- **Error Handling**: Graceful handling of invalid operations

## Installation & Setup

### Prerequisites

1. **Blender Installation**: Blender version 4.3+ recommended
2. **GAIA Installation**: Core GAIA system must be installed
3. **Lemonade Server**: Must be running for AI processing

### MCP Server Setup

The Blender agent requires the MCP (Model Context Protocol) server to communicate with Blender:

#### Step-by-Step Setup:

1. **Open Blender** (version 4.3 or newer recommended)

2. **Access Add-ons Menu**:
   - Go to `Edit > Preferences > Add-ons`

3. **Install the MCP Server**:
   - Click the down arrow button, then `Install...`
   - Navigate to: `src/gaia/mcp/blender_mcp_server.py`
   - Select and install the file

4. **Enable the Add-on**:
   - Find `Simple Blender MCP` in the add-ons list
   - Check the box to enable it

5. **Configure the Server**:
   - Open the 3D viewport sidebar (press `N` key if not visible)
   - Find the `Blender MCP` panel in the sidebar
   - Set port to `9876` (default) or customize as needed
   - Click `Start Server`

6. **Verify Connection**:
   - The server status should show as running
   - GAIA CLI will validate the connection when starting Blender commands

#### Visual Setup Guide

For detailed setup instructions with screenshots, see: `workshop/blender.ipynb`

## Command Reference

### Basic Command Structure

```bash
gaia blender [OPTIONS]
```

### Available Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--model` | string | "Llama-3.2-3B-Instruct-Hybrid" | Model ID to use for AI processing |
| `--example` | int (1-6) | None | Run a specific example, if not specified run interactive mode |
| `--steps` | int | 5 | Maximum number of steps per query |
| `--output-dir` | string | "output" | Directory to save output files |
| `--stream` | flag | False | Enable streaming mode for LLM responses |
| `--stats` | flag | True | Display performance statistics |
| `--query` | string | None | Custom query to run instead of examples |
| `--interactive` | flag | False | Enable interactive mode for continuous queries |
| `--debug-prompts` | flag | False | Enable debug prompts for development |
| `--print-result` | flag | False | Print results to console |
| `--mcp-port` | int | 9876 | Port for the Blender MCP server |

## Usage Examples

### Running Built-in Examples

```bash
# Run all Blender examples in sequence
gaia blender

# Run a specific example (1-5 available)
gaia blender --example 2

# Run example with custom model
gaia blender --example 3 --model "custom-model"
```

### Interactive Mode

```bash
# Start interactive Blender mode for custom 3D scene creation
gaia blender --interactive

# Interactive mode with debug information
gaia blender --interactive --debug-prompts --output-dir ./blender_results
```

### Custom Queries

```bash
# Single custom query to create specific 3D objects
gaia blender --query "Create a red cube and blue sphere arranged in a line"

# Complex scene setup
gaia blender --query "Clear the scene, then create a green cylinder at (0,0,0) and a yellow cone 3 units above it"

# Advanced scene with multiple operations
gaia blender --query "Create a complex 3D scene with multiple colored objects arranged in a circle"
```

### Advanced Configuration

```bash
# Use different model with streaming enabled
gaia blender --model "custom-model" --stream --query "Create a sunset scene with mountains"

# Custom MCP port and output directory
gaia blender --mcp-port 9877 --output-dir ./my_scenes --query "Create a modern office space"

# Performance monitoring
gaia blender --stats --steps 10 --query "Build a complete house structure"
```

## Built-in Examples

The Blender agent includes several built-in examples to demonstrate capabilities:

### Example 1: Clearing the Scene
**Purpose**: Remove all objects from the scene
**Command**: Clear the scene to start fresh
**Learning**: Basic scene management and cleanup

### Example 2: Creating a Basic Cube
**Purpose**: Create a red cube at the center
**Command**: Create a red cube at the center of the scene with red material
**Learning**: Object creation and material assignment

### Example 3: Creating a Sphere with Properties
**Purpose**: Blue sphere with custom position and scale
**Command**: Create a blue sphere at position (3, 0, 0) with scale (2, 2, 2)
**Learning**: Positioning, scaling, and color properties

### Example 4: Multiple Objects
**Purpose**: Green cube and red sphere arrangement
**Command**: Create a green cube at (0, 0, 0) and a red sphere 3 units above it
**Learning**: Multi-object scenes and spatial relationships

### Example 5: Object Modification
**Purpose**: Create and then modify a blue cylinder
**Command**: Create a blue cylinder, then make it taller and move it up 2 units
**Learning**: Object modification and transformation workflows

**Note**: Examples 1-5 are currently implemented. Example 6 is planned for future releases.

## Agent Capabilities

### Scene Management
- **Clear Scenes**: Remove all objects to start fresh
- **Object Inventory**: List and identify existing objects
- **Scene Information**: Get comprehensive scene details
- **Hierarchy Management**: Organize complex scenes

### Object Creation & Manipulation
- **Primitive Objects**:
  - Cubes with customizable dimensions
  - Spheres with radius control
  - Cylinders with height and radius settings
  - Cones with base and tip configuration
  - Torus objects with major and minor radius
- **Positioning**: Precise 3D coordinate placement
- **Rotation**: Object orientation control
- **Scaling**: Non-uniform scaling on X, Y, Z axes

### Material & Appearance
- **Color Assignment**: Full RGBA color control
- **Material Properties**: Basic material characteristics
- **Visual Consistency**: Standardized material application
- **Material Library**: Reusable material definitions

### Advanced Features
- **Multi-step Planning**: Complex task breakdown
- **Dependency Resolution**: Handle object relationships
- **Error Recovery**: Graceful handling of invalid operations
- **Progress Monitoring**: Step-by-step execution tracking

## Interactive Mode

Interactive mode provides a continuous interface for 3D scene creation:

### Starting Interactive Mode
```bash
gaia blender --interactive
```

### Interactive Commands
- **Scene Creation**: Describe the scene you want to create
- **Object Modification**: Request changes to existing objects
- **Scene Queries**: Ask questions about the current scene
- **Control Commands**:
  - Type `exit`, `quit`, or `q` to exit
  - Use `Ctrl+C` for immediate termination

### Example Interactive Session
```
Enter Blender query: Create a red cube at the origin
[Agent processes and creates cube]

Enter Blender query: Add a blue sphere 2 units above the cube
[Agent adds sphere with proper positioning]

Enter Blender query: Make the cube twice as large
[Agent modifies cube scale]

Enter Blender query: exit
Exiting Blender interactive mode.
```

## Requirements & Dependencies

### System Requirements
- **Blender**: Version 4.3+ (4.2 may work but not fully tested)
- **Python**: Compatible with GAIA's Python environment
- **Memory**: Sufficient RAM for both GAIA and Blender (8GB+ recommended)
- **Storage**: Space for 3D scene files and outputs

### Software Dependencies
- **GAIA Core**: Full GAIA installation required
- **Lemonade Server**: Must be running for AI processing
- **Blender MCP Server**: Must be installed and running in Blender

### Network Requirements
- **Local Communication**: MCP server runs on localhost
- **Port Availability**: Default port 9876 (customizable)
- **Firewall**: Allow local connections on MCP port

## Troubleshooting

### Common Issues

#### Lemonade Server Not Running
**Error**: Connection errors or "server not accessible" messages
**Solution**:
```bash
lemonade-server serve
```

#### Blender MCP Server Issues
**Error**: "Blender MCP server is not running or not accessible"
**Solutions**:
1. Verify Blender is open with the MCP add-on installed
2. Check the MCP server is started in Blender's sidebar panel
3. Confirm port 9876 is available (or use custom port with `--mcp-port`)
4. Restart Blender if the server appears unresponsive

#### Installation Problems
**Error**: MCP server add-on not found or won't install
**Solutions**:
1. Verify you're navigating to the correct file: `src/gaia/mcp/blender_mcp_server.py`
2. Check Blender version compatibility (4.3+ recommended)
3. Ensure GAIA is properly installed
4. Try restarting Blender after installation

#### Performance Issues
**Error**: Slow response times or timeouts
**Solutions**:
1. Reduce `--steps` parameter for simpler operations
2. Use smaller models for faster processing
3. Ensure adequate system resources
4. Check for memory constraints in both GAIA and Blender

#### Port Conflicts
**Error**: MCP server won't start due to port conflicts
**Solutions**:
1. Use `--mcp-port` to specify an alternative port
2. Check for other applications using port 9876
3. Kill conflicting processes: `gaia kill --port 9876`

### Debug Mode

Enable debug mode for detailed troubleshooting:
```bash
gaia blender --debug-prompts --interactive --logging-level DEBUG
```

This provides:
- Detailed prompt information
- Step-by-step execution logs
- Error stack traces
- Communication details between GAIA and Blender

### Getting Help

For additional support:
1. Check the workshop notebook: `workshop/blender.ipynb`
2. Review the [Development Guide](./dev.md#troubleshooting)
3. Consult the [FAQ](./faq.md) for common solutions
4. Enable debug mode to gather detailed information

## Advanced Usage

### Custom Models
The Blender agent supports different AI models for varying performance characteristics:

```bash
# Use a more powerful model for complex scenes
gaia blender --model "Llama-3.2-8B-Instruct" --query "Create a detailed cityscape"

# Use a smaller model for simple operations
gaia blender --model "Llama-3.2-1B-Instruct" --query "Create a red cube"
```

### Batch Operations
Process multiple scenes or operations efficiently:

```bash
# Save results to organized output directory
gaia blender --output-dir ./batch_scenes --query "Create scene 1: office setup"

# Use streaming for real-time feedback
gaia blender --stream --query "Build a complex architectural structure"
```

### Integration with Other Tools
The Blender agent can be integrated into larger workflows:

- **Automated Content Creation**: Script multiple scene generations
- **Educational Tools**: Demonstrate 3D concepts through natural language
- **Rapid Prototyping**: Quick visualization of 3D ideas
- **AI-Assisted Modeling**: Accelerate traditional 3D modeling workflows

## Best Practices

### Query Construction
- **Be Specific**: Include precise positions, colors, and sizes
- **Use Clear Language**: Avoid ambiguous terms
- **Break Complex Requests**: Split large scenes into manageable parts
- **Specify Relationships**: Clearly describe object positioning relative to others

### Scene Organization
- **Start Clean**: Begin with `Clear the scene` for predictable results
- **Logical Progression**: Build scenes step by step
- **Test Incrementally**: Verify each step before adding complexity
- **Use Consistent Naming**: Reference objects clearly in follow-up commands

### Performance Optimization
- **Appropriate Models**: Match model size to task complexity
- **Step Limits**: Set reasonable `--steps` values
- **Resource Monitoring**: Use `--stats` to track performance
- **Batch Similar Operations**: Group related tasks together

## Future Enhancements

Planned improvements to the Blender agent include:

- **Extended Object Library**: More primitive types and complex shapes
- **Advanced Materials**: PBR materials, textures, and lighting
- **Animation Support**: Keyframe animation and motion paths
- **Scene Templates**: Pre-built scene configurations
- **Import/Export**: Integration with external 3D file formats
- **Collaborative Features**: Multi-user scene editing capabilities

## License

Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
