# Workshop Plan: Building Local LLM Agents for Blender with Ryzen AI

## Overview

This hands-on workshop will guide participants through building local LLM agents using the Ryzen AI stack to interact with the Blender 3D creation suite. Participants will learn how to create an agent that can understand natural language instructions and translate them into Blender operations, all running locally on Ryzen AI hardware using the powerful NPU/iGPU hybrid execution mode.

## Introduction to GAIA

GAIA (Generative AI Is Awesome) is an open-source framework designed to simplify the creation and deployment of AI agents on local hardware. Developed by AMD, GAIA makes it easy to build powerful LLM-based applications that run entirely on your personal computer without cloud dependencies.

Key benefits of GAIA include:
- **User-friendly**: Simple setup and intuitive interfaces for both beginners and advanced users
- **Local processing**: Complete privacy and offline capability with no data sent to external servers
- **Hardware optimization**: Leverages AMD Ryzen AI's neural processing unit (NPU) and integrated GPU for efficient execution in hybrid mode
- **Extensible**: Easily build custom agents for specific use cases like 3D modeling in Blender
- **Agent Framework**: Comprehensive base classes with tool registration, planning, execution, and observability

GAIA works with Lemonade, a specialized LLM server that optimizes model execution on Ryzen AI hardware through NPU/iGPU hybrid mode, allowing LLMs to run as efficiently and fast as possible.

## Experience

Participants will:
- Learn the basics of GAIA and Lemonade for local AI development on Ryzen AI hardware
- Understand how to leverage NPU/iGPU hybrid mode for optimal performance
- Set up and test the Multi-Context Protocol (MCP) for Blender communication
- Build a comprehensive LLM agent using GAIA's Agent framework
- Create intelligent 3D modeling workflows with natural language commands
- Implement advanced features like tool registration, planning, and error recovery
- Use rich observability features to understand agent reasoning and execution

Throughout this 2-hour workshop, participants will work with practical examples, creating and manipulating 3D objects in Blender through natural language instructions, all processed locally on Ryzen AI-equipped devices using the performance benefits of hybrid mode execution.

## Objectives

1. **Understand GAIA Framework and Lemonade**
   - Learn the architecture of the GAIA Agent framework
   - Understand how Lemonade optimizes LLMs for local execution using hybrid mode
   - Configure the environment for agent development on Ryzen AI hardware

2. **Set up Multi-Context Protocol (MCP) for Blender**
   - Install and configure the Blender MCP server addon
   - Understand the socket-based communication protocol between Python and Blender
   - Test basic communication and object creation

3. **Build a comprehensive Blender Agent**
   - Understand the GAIA Agent base class and tool registration system
   - Implement systematic tool registration with the @tool decorator
   - Create intelligent planning and execution workflows
   - Add error handling and recovery mechanisms

4. **Implement Advanced Agent Features**
   - Object name tracking and plan adaptation
   - Multi-step scene creation with natural language descriptions
   - Rich observability and debugging capabilities
   - Performance monitoring and optimization

5. **Create Complex 3D Scenes**
   - Build scenes from natural language descriptions
   - Implement coordinated multi-object workflows
   - Handle material assignment and object modification
   - Test with increasingly complex scenarios

## Success Criteria

By the end of this workshop, participants will have:
- Understanding of GAIA Agent framework and Lemonade for local AI development using NPU/iGPU hybrid execution
- A working MCP server integrated with Blender for bidirectional communication
- A comprehensive BlenderAgent class built on GAIA's Agent framework
- Experience with tool registration, planning, and execution workflows
- A functional agent that can create complex 3D scenes from natural language descriptions
- Knowledge of advanced features like object tracking and plan adaptation
- Understanding of rich observability and debugging capabilities
- Appreciation of the performance benefits of hybrid mode execution on Ryzen AI hardware

## Key Messages

- **Local AI Power**: Ryzen AI hardware provides sufficient computational power to run complex LLM agents locally without cloud dependencies.
- **Hybrid Performance**: The NPU/iGPU hybrid mode delivers competitive performance, enabling responsive AI interactions on consumer PC hardware.
- **Agent Framework**: GAIA's Agent framework significantly simplifies building sophisticated AI applications with planning, tool integration, and error recovery.
- **Practical Applications**: LLM agents can transform how users interact with complex software like Blender, enabling natural language interfaces.
- **Accessible Development**: Building agents with GAIA and Lemonade lowers the barrier to entry for creating sophisticated AI tools.
- **Performance Optimization**: The hybrid NPU/iGPU approach intelligently distributes workloads across specialized hardware for optimal balance of speed, efficiency, and responsiveness.

## Workshop Schedule

### Part 1: Introduction and Environment Setup
- Overview of GAIA Agent framework and architecture
- Introduction to Lemonade server and hybrid NPU/iGPU execution mode
- Performance benefits of Ryzen AI hybrid execution for local LLMs
- Demo of existing GAIA agents and capabilities
- Setting up the development environment:
  - GAIA installation verification
  - Conda environment setup
  - Jupyter notebook configuration
  - Starting Lemonade Server
- Testing basic LLM functionality with OpenAI API
- Q&A about GAIA framework and local LLM execution

### Part 2: Multi-Context Protocol (MCP) Setup and Testing
- Introduction to MCP architecture for Blender integration
- Client-server communication model
- Installing the Blender MCP addon:
  - Addon installation in Blender
  - Server configuration and startup
  - Port configuration (default: 9876)
- Testing MCP functionality:
  - Basic scene information retrieval
  - Simple object creation
  - Material assignment example
- Troubleshooting common connection issues

### Part 3: Building a Simple Function-Based Agent
- Basic natural language to 3D object creation
- Setting up LLM client with system prompts
- Creating a simple parsing function
- Testing with basic commands:
  - Creating cubes, spheres, cylinders
  - Understanding location and scale parameters
  - Handling basic error cases
- Limitations of the simple approach

### Part 4: Comprehensive Agent Framework Implementation
- **Step 1: Agent Class Foundation**
  - Understanding the GAIA Agent base class
  - Setting up BlenderAgent inheritance
  - Implementing required abstract methods
  - System prompt engineering for 3D modeling

- **Step 2: Tool Registration System**
  - Using the @tool decorator for automatic registration
  - Implementing core Blender tools:
    - `clear_scene()`: Scene cleanup
    - `create_object()`: Object creation with full parameters
    - `set_material_color()`: Material assignment
    - `modify_object()`: Object transformation
    - `get_scene_info()`: Scene inspection
  - Proper error handling and result formatting

- **Step 3: Advanced Features**
  - Object name tracking and plan adaptation
  - Post-processing tool results
  - Automatic plan updates for object references
  - Error recovery mechanisms

- **Step 4: Testing and Validation**
  - Testing individual tools
  - Creating colored objects with multi-step plans
  - Verifying plan execution and adaptation
  - Understanding agent reasoning and decision-making

### Part 5: Complex Scene Creation and Advanced Workflows
- **Interactive Scene Creation Method**
  - Building the `create_interactive_scene()` method
  - Leveraging base Agent planning capabilities
  - Multi-step scene decomposition

- **Complex Scene Examples**
  - Creating themed scenes (desk setup, kitchen, etc.)
  - Coordinating multiple objects with proper positioning
  - Material assignment and object relationships
  - Handling complex natural language descriptions

- **Rich Observability and Debugging**
  - Understanding agent execution flow
  - Plan visualization and step tracking
  - Performance monitoring and optimization
  - Debugging failed operations

### Part 6: Testing, Optimization, and Q&A
- Best practices for agent development
- Performance optimization tips for Ryzen AI
- Extending the agent with additional capabilities
- Resources for further development
- Open Q&A and troubleshooting

## Requirements

- All PCs will be provided with AMD Ryzen AI processors configured for hybrid NPU/iGPU execution
- Blender 4.3 or newer will be pre-installed
- No prior experience with LLMs or AI required
- Basic familiarity with Python helpful
- Basic understanding of 3D concepts helpful but not necessary

## Resources

- **Interactive Jupyter Notebook** (`blender.ipynb`):
  - Complete step-by-step implementation guide
  - Live code examples for each workshop part
  - Interactive exercises with immediate feedback
  - Built-in testing and debugging tools
  - Comprehensive documentation and explanations
  - Ready-to-run code snippets for modification and experimentation

- **Workshop GitHub Repository**:
  - Starter code and complete implementations
  - Documentation for GAIA Agent framework and Lemonade
  - Blender MCP addon and client libraries
  - Sample scenes and test cases

- **Additional Resources**:
  - Guide to optimizing agents for NPU/iGPU hybrid mode
  - Troubleshooting guide for common issues
  - Best practices for agent development
  - Performance monitoring and optimization techniques

## Software Requirements
- https://lemonade-server.ai/
- https://conda-forge.org/download/
- https://git-scm.com/downloads/win
- https://code.visualstudio.com/download
- https://www.blender.org/download/releases/4-4/

