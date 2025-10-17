# GAIA Docker Agent Documentation

## Overview

The GAIA Docker Agent provides a natural language interface for containerizing applications. The agent analyzes your application structure, generates appropriate Dockerfiles, and provides guidance for building and running containers - all through conversational commands. No Docker expertise required.

## Quick Start

### Prerequisites

1. **Docker Installation** (Required):
   Docker Engine or Desktop: Download from [docker.com](https://www.docker.com/)

2. **Setup GAIA Development Environment**:
   Follow the [GAIA Development Guide](./dev.md) to set up GAIA:

   - Clone the repository
   - Create conda environment
   - Install GAIA: `pip install -e .` (or `pip install -e .[dev]` for development)

   The base installation includes all dependencies needed for the Docker agent.

3. **Download Required Model**:
   The Docker agent uses the `Qwen3-Coder-30B-A3B-Instruct-GGUF` model for reliable Dockerfile generation and application analysis.

   Use the Lemonade server's model manager to download it:
   1. Start Lemonade server with extended context size: `lemonade-server serve --ctx-size 8192`
   2. Open the model manager in your browser (typically http://localhost:8000)
   3. Search for and download: `Qwen3-Coder-30B-A3B-Instruct-GGUF`

   Note: The model is over 17GB and can take a while to download depending on your internet connection. It provides excellent results for Dockerfile generation and application analysis.
   
   **Important**: The Docker agent requires a higher context size (8192) than the default (4096) to handle complex application analysis and Dockerfile generation. For more details on Lemonade Server CLI options, see the [Lemonade Server documentation](https://lemonade-server.ai/docs/server/lemonade-server-cli/#command-line-options-for-serve-and-run).

### Verify Installation

```bash
# Check Docker is installed
docker --version

# Check that GAIA is installed correctly
gaia --version

# Test Docker agent with a Flask application
gaia docker "create a Dockerfile for my app" -d ./app
```

### Basic Usage

```bash
# Generate Dockerfile for your application
gaia docker "create a Dockerfile for my application" -d ./app
gaia docker "create a Dockerfile for my application and then build and run the container" -d ./app
```

## Architecture Overview

### Key Components

1. **DockerAgent** (`src/gaia/agents/docker/agent.py`)

   - Core agent that processes natural language queries
   - Analyzes application structure and dependencies
   - Uses LLM to generate appropriate Dockerfiles
   - Registers four main tools: `analyze_directory`, `generate_dockerfile`, `build_image`, `run_container`

2. **DockerApp** (`src/gaia/apps/docker/app.py`)

   - Application wrapper for the DockerAgent
   - Provides CLI interface and user interaction
   - Formats output for user display
   - Displays next steps after Dockerfile generation

3. **GAIA Docker CLI** (`gaia docker` command)

   - Easy command-line interface for Docker operations
   - Supports natural language queries with directory context
   - Automatically manages agent lifecycle
   - No coding required - just describe what you need

### How It Works

1. **Directory Analysis**: Scans application structure, detects frameworks, identifies dependencies
2. **Context Building**: Creates detailed application context for the LLM
3. **Natural Language Processing**: LLM interprets user intent and requirements
4. **Dockerfile Generation**: Creates appropriate Dockerfile with best practices
5. **Next Steps Guidance**: Provides build and run commands

## Usage Examples

### Natural Language Commands

```bash
# Just create a Dockerfile
gaia docker "create a Dockerfile for my application" -d ./app
gaia docker "create a Dockerfile for my application" -d "C:\Users\user\src\test\netscan"

# Framework-specific with requirements
gaia docker "containerize this Flask app with gunicorn" -d ./flask-app

# Full workflow: create Dockerfile, build image, and run container
gaia docker "create a Dockerfile for my application and then build and run the container" -d "C:\Users\user\src\test\netscan"

# Specify Python version
gaia docker "create Dockerfile with Python 3.11" -d ./project
```

The `command` parameter accepts natural language instructions. The agent can:
- Create just a Dockerfile (analyzes app, generates and saves Dockerfile)
- Build the Docker image (if you ask it to build)
- Run the container (if you ask it to run)
- Or do all three steps in sequence

## GitHub Copilot Integration

Use GAIA Docker directly within GitHub Copilot for seamless containerization assistance in your IDE.

### Prerequisites

1. **Start Lemonade Server with Extended Context**:
   ```bash
   lemonade-server serve --ctx-size 8192
   ```
   Note: The extended context size is required for handling complex Docker queries through Copilot.

2. **Start GAIA MCP Bridge**:
   ```bash
   gaia mcp start --port 8080
   ```

3. **Configure VSCode MCP Settings**:

   Add to your VSCode `mcp.json` (typically in `.vscode/mcp.json`):
   ```json
   {
       "servers": {
           "gaia-docker": {
               "url": "http://localhost:8080/mcp",
               "type": "http"
           }
       },
       "inputs": []
   }
   ```

4. **Restart VSCode** to load the MCP configuration

### Usage with Copilot

Once configured, you can reference GAIA Docker in your Copilot prompts using `#gaia-docker`:

```
# Ask Copilot to containerize your application in as simple as
"use #gaia-docker with my app"
```

Copilot will communicate with the GAIA Docker agent through MCP, analyzing your project and generating appropriate Dockerfile configurations. The agent has full context of your application structure and can provide intelligent recommendations.

### Workflow

1. **MCP Bridge**: Acts as the intermediary between VSCode/Copilot and GAIA agents
2. **Context Awareness**: The agent can access your project files and dependencies
3. **Interactive Generation**: Copilot presents the Dockerfile and next steps inline
4. **Iterative Refinement**: Continue the conversation to adjust the Dockerfile as needed

For more details on the MCP bridge, see [MCP Documentation](./mcp.md).

## Integration Methods

### 1. Python API (Direct Integration)

```python
from gaia.agents.docker.agent import DockerAgent

# Initialize and execute
agent = DockerAgent(model_id="Qwen3-Coder-30B-A3B-Instruct-GGUF", silent_mode=True)
result = agent.process_query("create a Dockerfile for my Flask app in directory: ./app")

if result['status'] == 'success':
    print(f"Steps taken: {result['steps_taken']}")
    # Extract Dockerfile content from conversation
    for msg in result.get('conversation', []):
        if msg.get('role') == 'system' and 'dockerfile_content' in msg.get('content', {}):
            print("Dockerfile generated successfully")
```

### 2. MCP Server (HTTP/JSON-RPC Integration)

GAIA's MCP support is powered by **[FastMCP](https://github.com/modelcontextprotocol/python-sdk)** from the Model Context Protocol Python SDK. The server uses FastMCP's "streamable-http" transport, providing both HTTP POST and SSE streaming at the `/mcp` endpoint.

**Start the Docker MCP Server:**
```bash
gaia mcp docker --port 8080
```

The server supports JSON-RPC interface and works with GitHub Copilot, Claude Desktop, and other MCP clients.

**Current MCP Limitations:**
- The MCP interface currently performs the complete workflow: analyze → create Dockerfile → build image → run container
- This is ideal for automation tools that need full containerization in a single operation
- Future versions will support more granular control (e.g., just creating Dockerfile without building)
- For granular control now, use the CLI interface which supports individual operations

**JSON-RPC Request Format:**
```json
{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "tools/call",
    "params": {
        "name": "gaia.docker",
        "arguments": {
            "query": "create a Dockerfile for this application",
            "directory": "./app"
        }
    }
}
```

Works with any HTTP client (JavaScript/fetch, Python/requests, cURL, etc.).

For detailed MCP integration examples, see:
- **[MCP Documentation](./mcp.md)** - Complete MCP bridge reference
- **[n8n Integration Guide](./n8n.md)** - Workflow automation examples

## Key Features

### Automatic Application Analysis

The agent automatically detects:
- Framework identification (Flask, Django, FastAPI, etc.)
- Python version requirements
- Dependencies from requirements.txt or pyproject.toml
- Application structure and entry points
- Port requirements for web applications

### Intelligent Dockerfile Generation

The agent generates Dockerfiles that include:
- Appropriate base images (Python official images)
- Dependency installation (pip install from requirements.txt)
- Working directory setup
- Application file copying
- Port exposure for web apps
- Runtime commands (ENTRYPOINT or CMD)
- Best practices (non-root user, layer optimization)

### Multi-Step Workflow

The agent orchestrates a complete containerization workflow:
1. **Analyze**: Scan application directory and identify structure
2. **Generate**: Create appropriate Dockerfile
3. **Validate**: Check Dockerfile syntax and completeness
4. **Guidance**: Provide next steps for build and run

### Next Steps Guidance

After Dockerfile generation, the agent provides:
- Build command with appropriate image tag
- Run command with port mappings and necessary flags
- Contextual tips based on application type

## Command Reference

### Basic Command Structure

```bash
gaia docker "command" [OPTIONS]
```

The `command` is a natural language instruction that tells the agent what Docker operations to perform (e.g., "create a Dockerfile", "build and run my app").

### Available Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `command` | string | Required | Natural language instruction (positional argument). Can request Dockerfile creation, building, running, or all three. |
| `-d`, `--directory` | string | `.` | Directory containing the application to containerize |
| `-v`, `--verbose` | flag | - | Enable verbose output |
| `--debug` | flag | - | Enable debug logging |
| `--model` | string | `Qwen3-Coder-30B-A3B-Instruct-GGUF` | LLM model to use |

## Troubleshooting

### Common Issues and Solutions

#### "Docker Not Installed"

```bash
# Check Docker installation
docker --version

# If not installed, download from docker.com
```

#### "Lemonade Server Not Running"

```bash
# Start the Lemonade server
lemonade-server serve

# Verify it's running
curl http://localhost:8000/health
```

#### "No Dockerfile Generated"

If the agent doesn't generate a Dockerfile:
1. Check that your application has identifiable structure (e.g., requirements.txt, app.py)
2. Ensure the Lemonade server is running
3. Try a more specific query describing your application type
4. Check the agent logs for error messages

#### "Model Not Found"

```bash
# Verify the Qwen3-Coder model is downloaded
# Open Lemonade UI: http://localhost:8000
# Check Models section for Qwen3-Coder-30B-A3B-Instruct-GGUF
```

#### MCP Integration Issues

```bash
# Check if MCP bridge is running
gaia mcp status

# Restart the bridge if needed
gaia mcp stop
gaia mcp start
```

### Debug Mode

For detailed troubleshooting, check the agent logs:

```bash
# Logs are written to gaia.log
tail -f gaia.log

# MCP logs (if using MCP bridge)
tail -f gaia.mcp.log
```

## Best Practices

1. **Organize Application**: Include requirements.txt/pyproject.toml, clear entry point (app.py), logical structure
2. **Review Output**: Verify base image, dependencies, ports, and runtime commands before building
3. **Test Incrementally**: Generate → Review → Build → Test container in sequence
4. **Use Natural Language**: When using GitHub Copilot integration, simple queries like `"use #gaia-docker with my app"` work well

## Limitations

Current limitations of the Docker agent:

- **Single-language support**: Primarily focused on Python applications
- **Simple configurations**: Best for straightforward containerization scenarios
- **No multi-stage builds**: Generated Dockerfiles use single-stage builds
- **Limited customization**: Advanced Docker features may require manual editing
- **No docker-compose**: Does not generate docker-compose.yml files

## Testing Your Integration

**Quick Python Test:**
```python
from gaia.agents.docker.agent import DockerAgent

agent = DockerAgent(silent_mode=True)
result = agent.process_query("create a Dockerfile for Flask app in: ./app")
print("✅ Success!" if result['status'] == 'success' else "❌ Failed")
```

**MCP Test:**
```bash
gaia mcp start && python tests/mcp/test_mcp_docker.py
```

## See Also

- [GAIA CLI Documentation](./cli.md) - Full command line interface guide
- [MCP Server Documentation](./mcp.md) - External integration details
- [Jira Agent Documentation](./jira.md) - Natural language Jira operations
- [Blender Agent Documentation](./blender.md) - 3D content creation
- [Features Overview](./features.md) - Complete GAIA capabilities

## License

Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
