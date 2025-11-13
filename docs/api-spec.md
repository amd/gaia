# GAIA API Server Specification

## Overview

The GAIA API Server exposes GAIA agents as "models" through an OpenAI-compatible REST API. The server implements a subset of OpenAI's Chat Completions API.

**Base URL:** `http://localhost:8080` (default)

**Architecture:**
```
External Client → FastAPI Server → GAIA Agents → Lemonade LLM Backend
```

## Endpoints

### Health Check

**`GET /health`**

Check server health.

**Response:**
```json
{
  "status": "ok",
  "service": "gaia-api"
}
```

---

### List Models

**`GET /v1/models`**

List available GAIA agent models.

**Response Schema:**
```json
{
  "object": "list",
  "data": [
    {
      "id": "string",
      "object": "model",
      "created": 1234567890,
      "owned_by": "amd-gaia",
      "description": "string",
      "max_input_tokens": 32768,
      "max_output_tokens": 8192
    }
  ]
}
```

**Example:**
```bash
curl http://localhost:8080/v1/models
```

---

### Chat Completions

**`POST /v1/chat/completions`**

Create a chat completion using a GAIA agent. Supports both streaming (SSE) and non-streaming responses.

#### Request Parameters

| Parameter | Type | Required | Default | Range | Description |
|-----------|------|----------|---------|-------|-------------|
| `model` | string | ✅ Yes | - | - | Model ID (e.g., "gaia-code") |
| `messages` | array | ✅ Yes | - | - | Array of message objects |
| `stream` | boolean | No | `false` | - | Enable Server-Sent Events streaming |
| `temperature` | number | No | `0.7` | 0.0 - 2.0 | Sampling temperature |
| `max_tokens` | integer | No | - | > 0 | Maximum tokens to generate |
| `top_p` | number | No | `1.0` | 0.0 - 1.0 | Nucleus sampling parameter |

#### Message Object

| Field | Type | Required | Values | Description |
|-------|------|----------|--------|-------------|
| `role` | string | ✅ Yes | `"system"`, `"user"`, `"assistant"`, `"tool"` | Message role |
| `content` | string | ✅ Yes | - | Message content |
| `tool_calls` | array | No | - | Tool calls (assistant role only) |
| `tool_call_id` | string | No | - | Tool call ID (tool role only) |

#### Non-Streaming Response

**Request:**
```bash
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gaia-code",
    "messages": [{"role": "user", "content": "Write a hello function"}],
    "stream": false
  }'
```

**Response:**
```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1677652288,
  "model": "gaia-code",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Here's a hello function..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 20,
    "completion_tokens": 150,
    "total_tokens": 170
  }
}
```

#### Streaming Response

**Request:**
```bash
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gaia-code",
    "messages": [{"role": "user", "content": "Write a hello function"}],
    "stream": true
  }'
```

**Response (Server-Sent Events):**
```
data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1677652288,"model":"gaia-code","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1677652288,"model":"gaia-code","choices":[{"index":0,"delta":{"content":"Here"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1677652288,"model":"gaia-code","choices":[{"index":0,"delta":{"content":"'s"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1677652288,"model":"gaia-code","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

## Available Models

### gaia-code

Autonomous Python development agent.

| Property | Value |
|----------|-------|
| `id` | `gaia-code` |
| `max_input_tokens` | 32768 |
| `max_output_tokens` | 8192 |
| `description` | Autonomous Python coding agent with planning, generation, and testing |

**Requirements:**
- Lemonade Server with `--ctx-size 32768`
- Model: `Qwen3-Coder-30B-A3B-Instruct-GGUF`

**Capabilities:**
- Code generation (functions, classes, projects)
- Test generation
- Linting & formatting (pylint, Black)
- Error detection and correction
- Project scaffolding
- Architectural planning

## Error Responses

All errors follow OpenAI's error format.

### 400 - Bad Request

```json
{
  "error": {
    "message": "messages is required",
    "type": "invalid_request_error",
    "code": "invalid_request"
  }
}
```

### 404 - Model Not Found

```json
{
  "error": {
    "message": "Model 'gaia-invalid' not found. Available models: gaia-code",
    "type": "invalid_request_error",
    "code": "model_not_found"
  }
}
```

### 500 - Internal Server Error

```json
{
  "error": {
    "message": "Agent processing failed: <details>",
    "type": "internal_error",
    "code": "agent_error"
  }
}
```

## OpenAI API Compatibility

### Supported Features

✅ `/v1/chat/completions` (streaming and non-streaming)
✅ `/v1/models`
✅ `messages` array with `role` and `content`
✅ `temperature`, `max_tokens`, `top_p` parameters
✅ Server-Sent Events (SSE) streaming

### Not Supported

❌ `frequency_penalty` parameter
❌ `presence_penalty` parameter
❌ `functions` and `tools` parameters
❌ `response_format` parameter
❌ `logprobs`, `n`, `stop` parameters
❌ `/v1/embeddings`
❌ `/v1/audio/*`
❌ `/v1/images/*`

## Adding New Agents

To expose a new agent via the API:

### 1. Create Agent Class

```python
# src/gaia/agents/myagent/agent.py
from gaia.agents.base.api_agent import ApiAgent
from gaia.agents.base.agent import Agent

class MyAgent(ApiAgent, Agent):
    def get_model_info(self):
        return {
            "max_input_tokens": 8192,
            "max_output_tokens": 4096,
        }
```

### 2. Register in Agent Registry

```python
# src/gaia/api/agent_registry.py
AGENT_MODELS = {
    "gaia-myagent": {
        "class_name": "gaia.agents.myagent.agent.MyAgent",
        "init_params": {"silent_mode": True},
        "description": "My custom agent"
    }
}
```

### 3. Restart Server

```bash
gaia api stop
gaia api start
```

## Security

**Current Implementation:**
- ❌ No authentication
- ❌ No rate limiting
- ✅ CORS enabled (all origins)

**Designed for local development only.**

For production deployment, implement:
- API key authentication
- Rate limiting middleware
- CORS restrictions
- HTTPS with valid certificates

## License

Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
