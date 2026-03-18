# GAIA Chat Module

The GAIA Chat module provides a powerful SDK for text-based conversations with LLMs, featuring conversation memory, RAG support, and multiple interaction modes.

## Quick Start

```python
from gaia.chat.sdk import AgentSDK, AgentConfig

# Create chat instance
config = AgentConfig(assistant_name="gaia", show_stats=True)
chat = AgentSDK(config)

# Send message
response = chat.send("Hello!")
print(response.text)

# Enable RAG for document Q&A
chat.enable_rag(documents=["manual.pdf"])
response = chat.send("What does the manual say about setup?")
print(response.text)
```

## Key Features

- **Conversation Memory**: Maintains context across exchanges
- **RAG Support**: Document-based Q&A with PDF indexing
- **Streaming**: Real-time response streaming
- **Model Flexibility**: Support for local and cloud models
- **Session Management**: Multiple independent conversations

## CLI Usage

```bash
# Interactive chat
gaia chat

# Chat with documents (RAG)
gaia chat --index manual.pdf

# Single query
gaia chat --query "What is Python?"
```

## Module Structure

```
chat/
├── sdk.py          # AgentSDK, AgentConfig, SimpleChat, AgentSession
├── app.py          # CLI application and demos
├── prompts.py      # System prompts and templates
└── README.md       # This file
```

## Documentation

For comprehensive documentation including:
- Complete API reference
- Advanced usage examples
- RAG configuration and troubleshooting
- Session management
- Performance tuning

See: **[docs/chat.md](../../../docs/chat.md)**

## Testing

```bash
# Run chat SDK tests
pytest tests/test_chat_sdk.py -v
```

## License

Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
