# GAIA MCP Server Documentation

## Overview

The GAIA MCP Server provides an HTTP-native bridge that exposes GAIA's AI agents to third-party applications through standard REST and JSON-RPC protocols. This enables external tools like n8n, Zapier, web applications, and custom integrations to leverage GAIA's AI capabilities without direct Python dependencies.

## When to Use MCP

**Use the MCP Server when:**
- Integrating GAIA with external applications (n8n, Zapier, web apps)
- Building non-Python applications that need GAIA's AI capabilities
- Creating workflow automations with tools that support HTTP/REST
- Accessing GAIA from browsers or mobile applications

**You DON'T need MCP when:**
- Using GAIA CLI directly (`gaia jira`, `gaia llm`, etc.)
- Building Python applications (use GAIA's Python API directly)
- Running GAIA agents locally from the command line

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│ External Apps   │────▶│  GAIA MCP Server │────▶│  GAIA Agents    │
│ (n8n, Zapier,   │     │   HTTP/REST      │     │ (LLM, Chat,     │
│  Web Apps, etc) │     │   Port 8765      │     │  Jira, Blender) │
└─────────────────┘     └──────────────────┘     └─────────────────┘
        HTTP                    Python                  Python
     Requests                   Bridge                  Native
```

The MCP Server acts as an HTTP bridge between external applications and GAIA's native Python agents.

## Quick Start

```bash
# 1. Install GAIA (MCP is included in base installation)
pip install -e .

# 2. Start the MCP server
gaia mcp start

# 3. Test the server is running
curl http://localhost:8765/health

# 4. Make your first API call (conversational chat)
curl -X POST http://localhost:8765/chat \
  -H "Content-Type: application/json" \
  -d '{"query":"Hello GAIA"}'
```

### Starting the MCP Server

```bash
# Start in foreground (see logs)
gaia mcp start

# Start in background
gaia mcp start --background

# Start with custom port
gaia mcp start --port 9000

# Start with verbose logging (logs all HTTP requests)
# Logs go to console (foreground) or gaia.log and gaia.mcp.log (background)
gaia mcp start --verbose
```

**Logging Behavior:**
- **Foreground mode**: Logs appear in the console
- **Background mode**: Logs are written to `gaia.mcp.log` (default)
- **With `--verbose`**: All HTTP requests/responses are logged (including health checks)
- **Main log**: All MCP logs also go to `gaia.log` regardless of mode

### Managing the Server
```bash
# Check if server is running
gaia mcp status

# Test with a simple query
gaia mcp test --query "Hello, GAIA!"

# Stop the server
gaia mcp stop
```

## Setup

### Prerequisites

1. **GAIA Installation**: Follow the [Development Guide](./dev.md) to install GAIA
2. **Lemonade Server**: Ensure the LLM backend is running:
   ```bash
   # Default context size (4096) - sufficient for chat, LLM, and Jira
   lemonade-server serve
   
   # Extended context size (8192) - required for Docker agent
   lemonade-server serve --ctx-size 8192
   ```
3. **Docker (for Docker agent)**: Install Docker Engine or Desktop from [docker.com](https://www.docker.com/)

### Configuration (Optional)

The MCP server works out of the box with default settings. For advanced configuration, you can use environment variables:

```bash
# MCP Server Configuration
GAIA_MCP_HOST=localhost  # Host to bind to
GAIA_MCP_PORT=8765       # Port to listen on

# LLM Backend Configuration
LEMONADE_BASE_URL=http://localhost:8000/api/v0
```

Or modify the configuration in `src/gaia/mcp/mcp.json` for advanced settings like rate limiting.

### Verify Installation

```bash
# Check server status
gaia mcp status

# Test with a simple query
gaia mcp test --query "Hello GAIA!"
```


## API Endpoints

### REST Endpoints

| Endpoint | Method | Description | Example |
|----------|--------|-------------|----------|
| `/health` | GET | Server health check | `curl http://localhost:8765/health` |
| `/tools` | GET | List available tools | `curl http://localhost:8765/tools` |
| `/chat` | POST | Conversational chat (with context) | See examples below |
| `/llm` | POST | Direct LLM queries (no context) | See examples below |
| `/jira` | POST | Jira operations | See examples below |
| `/` | POST | JSON-RPC 2.0 endpoint | See examples below |

**Note**: Docker uses a newer framework using FastMCP-powered server. See examples in [Docker section](#docker-operations-fastmcp-framework) below.

### Example API Calls

#### Health Check
```bash
curl http://localhost:8765/health
# Response: {"status": "healthy", "agents": 4, "tools": 8}
```

#### Chat (Conversational)
```bash
# First message
curl -X POST http://localhost:8765/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "Hello! My name is Alice."}'

# Follow-up (remembers context)
curl -X POST http://localhost:8765/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "What is my name?"}'
```

#### LLM Query (No Context)
```bash
curl -X POST http://localhost:8765/llm \
  -H "Content-Type: application/json" \
  -d '{"query": "What is artificial intelligence?"}'
```

#### Jira Operations
```bash
curl -X POST http://localhost:8765/jira \
  -H "Content-Type: application/json" \
  -d '{"query": "show my open issues"}'
```

#### Docker Operations
```bash
# Start Docker MCP server
gaia mcp docker --port 8080

# Containerize application via JSON-RPC
curl -X POST http://localhost:8080/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "tools/call",
    "params": {
      "name": "dockerize",
      "arguments": {
        "appPath": "/absolute/path/to/app",
        "port": 5000
      }
    }
  }'
```

#### JSON-RPC Protocol
```bash
curl -X POST http://localhost:8765/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "tools/call",
    "params": {
      "name": "gaia.chat",
      "arguments": {"query": "Hello GAIA"}
    }
  }'

## Integration Examples

### Python Integration

```python
import requests
import json

def chat_with_gaia(query):
    """Chat with GAIA via MCP server (maintains context)"""
    response = requests.post(
        'http://localhost:8765/chat',
        json={'query': query}
    )
    return response.json()

# Example usage
result = chat_with_gaia("What is machine learning?")
print(result['result'])
```

### JavaScript/Node.js Integration

```javascript
const axios = require('axios');

async function chatWithGAIA(query) {
    const response = await axios.post(
        'http://localhost:8765/chat',
        { query: query }
    );
    return response.data;
}

// Example usage
const result = await chatWithGAIA('What is AI?');
console.log(result.result);
```

### cURL Examples

```bash
# Chat query (maintains conversation context)
curl -X POST http://localhost:8765/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "Explain neural networks"}'

# Direct LLM query (no context)
curl -X POST http://localhost:8765/llm \
  -H "Content-Type: application/json" \
  -d '{"query": "What is quantum computing?"}'

# Jira operations
curl -X POST http://localhost:8765/jira \
  -H "Content-Type: application/json" \
  -d '{"query": "create idea: Add dark mode feature"}'
```

## n8n Integration

For detailed n8n workflow automation examples, see the [n8n Integration Guide](./n8n.md).

### Quick n8n Setup

1. Start the MCP server:
   ```bash
   gaia mcp start
   ```

2. In n8n, add an HTTP Request node:
   - **Method**: POST
   - **URL**: `http://127.0.0.1:8765/chat` (use 127.0.0.1, not localhost)
   - **Body**: JSON with your query

3. Import the example workflow:
   - Go to **Workflows** → **Import**
   - Import `src/gaia/mcp/n8n.json`

## Available Tools

The MCP server exposes the following GAIA agents as tools:

| Tool | Description | Example Arguments |
|------|-------------|-------------------|
| `gaia.chat` | Conversational chat with context | `{"query": "Hello GAIA"}` |
| `gaia.query` | Direct LLM queries (no context) | `{"query": "What is AI?"}` |
| `gaia.jira` | Natural language Jira operations | `{"query": "show my issues"}` |
| `gaia.blender.create` | 3D content creation | `{"command": "create_cube", "parameters": {}}` |

**Docker Tool** (FastMCP framework on port 8080):
| Tool | Description | Example Arguments |
|------|-------------|-------------------|
| `dockerize` | Containerize application (analyze → create Dockerfile → build → run) | `{"appPath": "/absolute/path/to/app", "port": 5000}` |

To use the Docker tool, start the Docker MCP server: `gaia mcp docker --port 8080`

**Note**: The Docker agent is GAIA's first to use the new FastMCP framework. See [Docker Agent Documentation](./docker.md).




## Troubleshooting

### Common Issues

1. **Connection refused**
   - Ensure the GAIA MCP Bridge is running
   - Check firewall settings
   - Verify the correct port (default: 8765)

2. **LLM server not responding**
   - Start the Lemonade server: `lemonade-server serve`
   - Check GAIA_BASE_URL is correct
   - Verify models are loaded

3. **Server won't start**
   - Check if port 8765 is already in use: `netstat -an | findstr 8765`
   - Stop existing MCP server: `gaia mcp stop`
   - Try different port: `gaia mcp start --port 8766`

4. **No tools available**
   - Check server logs: `gaia mcp start --verbose`
   - Verify GAIA installation: `pip show gaia`
   - Reinstall MCP dependencies: `pip install -e .[mcp]`

### CLI Testing & Debugging

Use GAIA's built-in MCP commands:

```bash
# Check server status
gaia mcp status

# Test with a query
gaia mcp test --query "What is artificial intelligence?"

# Start with verbose logging
gaia mcp start --verbose

# Test specific host/port
gaia mcp test --host localhost --port 8765
```

### Validation Scripts

GAIA provides test scripts in the `tests/mcp/` directory to validate the MCP bridge:

```bash
# Run from the project root directory

# Simple validation test
python tests/mcp/test_mcp_simple.py

# Comprehensive HTTP validation test
python tests/mcp/test_mcp_http_validation.py

# Jira-specific MCP tests
python tests/mcp/test_mcp_jira.py

# Docker-specific MCP tests
python tests/mcp/test_mcp_docker.py

# Integration tests
python tests/mcp/test_mcp_integration.py
```

The tests validate:
- Health checks and tool listing
- Direct endpoints (/chat, /jira, /docker, /llm)
- JSON-RPC protocol compliance
- Error handling and CORS headers

## Security Considerations

1. **Authentication**: Always use authentication tokens in production
2. **SSL/TLS**: Use encrypted connections for sensitive data
3. **Rate Limiting**: Implement rate limits to prevent abuse
4. **Access Control**: Restrict access to specific IP addresses or networks
5. **Audit Logging**: Enable logging for compliance and debugging

## Architecture Notes

- **HTTP-Native**: Pure REST/JSON-RPC, no WebSocket dependencies
- **Stateless**: Each request is independent for better scalability
- **Agent Access**: Direct access to all GAIA agents
- **CORS Enabled**: Works with browser-based applications

## Applications

GAIA provides applications that connect to the MCP server. For using existing apps or building new ones, see the [Apps Documentation](./apps/).


## See Also

- [n8n Integration Guide](./n8n.md) - Detailed workflow automation examples
- [Jira Agent Documentation](./jira.md) - Natural language Jira operations
- [Docker Agent Documentation](./docker.md) - Natural language Docker containerization
- [GAIA CLI Guide](./cli.md) - Command line interface reference
- [Development Guide](./dev.md) - Setup and contribution guidelines

## License

Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT