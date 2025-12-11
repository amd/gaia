# GAIA API Server Documentation

## Overview

The GAIA API Server provides an OpenAI-compatible REST API that exposes GAIA agents as "models". This allows integration with tools that support OpenAI's API format, such as VSCode extensions and other OpenAI-compatible clients.

**For technical specifications, see [API Server Specification](./api-spec.md).**

## Quick Start

```bash
# 1. Start Lemonade Server with extended context
lemonade-server serve --ctx-size 32768

# 2. Start GAIA API Server
gaia api start

# 3. Test the server
curl http://localhost:8080/health

# 4. List available models
curl http://localhost:8080/v1/models

# 5. Make a request
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gaia-code",
    "messages": [{"role": "user", "content": "Write a hello world function"}]
  }'
```

## Prerequisites

1. **GAIA Installation**: Install GAIA with API support
   ```bash
   # Linux/Windows:
   pip install -e .[api]
   ```
   This installs FastAPI and Uvicorn, which are required for the API server.

2. **Lemonade Server**: Must be running with sufficient context size
   ```bash
   lemonade-server serve --ctx-size 32768
   ```
3. **Required Models**: Download `Qwen3-Coder-30B-A3B-Instruct-GGUF` via Lemonade's model manager

## Server Management

### Start Server

```bash
# Start in foreground
gaia api start

# Start in background
gaia api start --background

# Start with debug logging
gaia api start --debug

# Custom host/port
gaia api start --host 0.0.0.0 --port 8888
```

### Check Status

```bash
gaia api status
```

### Stop Server

```bash
gaia api stop
```

## Usage Examples

### Python (OpenAI Client)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8080/v1",
    api_key="not-needed"
)

# TypeScript/Express backend (routing detects "Express" → TypeScript)
response = client.chat.completions.create(
    model="gaia-code",
    messages=[{"role": "user", "content": "Create a REST API with Express and SQLite"}]
)

# Python backend (routing detects "Django" → Python)
response = client.chat.completions.create(
    model="gaia-code",
    messages=[{"role": "user", "content": "Create a Django REST API with authentication"}]
)

print(response.choices[0].message.content)
```

### Python (Streaming)

```python
stream = client.chat.completions.create(
    model="gaia-code",
    messages=[{"role": "user", "content": "Write a calculator class"}],
    stream=True
)

for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
```

### JavaScript/Node.js

```javascript
const axios = require('axios');

async function chat(message) {
    const response = await axios.post(
        'http://localhost:8080/v1/chat/completions',
        {
            model: 'gaia-code',
            messages: [{ role: 'user', content: message }]
        }
    );
    return response.data.choices[0].message.content;
}

// TypeScript/React frontend (routing detects "React" → TypeScript frontend)
chat('Build me a todo app using nextjs').then(console.log);

// Python script (routing detects Python for generic functions)
chat('Write a Python function to calculate factorial').then(console.log);
```

### cURL

```bash
# Non-streaming
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gaia-code",
    "messages": [{"role": "user", "content": "Write a function"}]
  }'

# Streaming
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gaia-code",
    "messages": [{"role": "user", "content": "Write a function"}],
    "stream": true
  }'
```

## Available Models

| Model ID | Description | Context | Requirements |
|----------|-------------|---------|--------------|
| `gaia-code` | Autonomous Python/TypeScript development agent with intelligent routing | 32K input / 8K output | Lemonade with `--ctx-size 32768` |

**Intelligent Routing**: The `gaia-code` model uses GAIA's Routing Agent to automatically detect your target programming language (Python or TypeScript) and project type (frontend, backend, fullstack) based on framework mentions in your request. For details on how routing works, see the **[Routing Guide](./routing.md)**.

For full model specifications, see [API Server Specification](./api-spec.md#available-models).

## Troubleshooting

### Server Won't Start

```bash
# Check if port is in use
lsof -i :8080  # Mac/Linux
netstat -ano | findstr :8080  # Windows

# Try different port
gaia api start --port 8888
```

### Connection Refused

```bash
# Verify server is running
gaia api status

# Check health endpoint
curl http://localhost:8080/health
```

### Agent Errors

```bash
# Check Lemonade is running
curl http://localhost:8000/health

# Verify context size
lemonade-server serve --ctx-size 32768

# Enable debug mode
gaia api start --debug
```

### Common Issues

| Issue | Solution |
|-------|----------|
| "Model not found" | Use correct model ID: `gaia-code` |
| "Agent processing failed" | Ensure Lemonade is running with `--ctx-size 32768` |
| "Port already in use" | Stop existing server or use `--port` flag |
| Streaming not working | Ensure `"stream": true` in request |

For more troubleshooting, see [FAQ](./faq.md).

## VSCode Integration

The GAIA API Server works with VSCode's Language Model Provider extension, allowing you to use GAIA Code directly in your editor.

**Quick Setup:**
1. Start Lemonade server: `lemonade-server serve --ctx-size 32768`
2. Start API server: `gaia api start`
3. Build and install VSCode extension (see [VSCode Integration Guide](./vscode.md))
4. Select GAIA model from VSCode model picker

**For complete setup instructions, see the [VSCode Integration Documentation](./vscode.md).**

## Technical Documentation

For detailed API specifications, request/response formats, and implementation details:

- **[API Server Specification](./api-spec.md)** - Complete API reference
- **[Routing Guide](./routing.md)** - Intelligent language/framework detection
- **[Code Agent Documentation](./code.md)** - Code agent capabilities and usage
- **[Development Guide](./dev.md)** - Development and contribution guidelines

## License

Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
