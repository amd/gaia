# GAIA Chat Module

The GAIA Chat module provides a powerful SDK and application for text-based conversations with Large Language Models (LLMs). It features conversation history management, multiple interaction modes, and seamless integration with local and cloud-based models.

## Overview

The chat module consists of:
- **ChatSDK**: Core SDK for programmatic chat integration
- **Chat App**: Demo application showcasing SDK capabilities
- **SimpleChat**: Simplified API for quick integration
- **ChatSession**: Session management for multiple concurrent conversations

## Features

- 🧠 **Conversation Memory**: Maintains context across multiple exchanges
- 💬 **Interactive Sessions**: Full CLI experience with commands
- 🔄 **Streaming Support**: Real-time response streaming
- 🎯 **Model Flexibility**: Support for multiple LLM models
- 📊 **Performance Stats**: Optional performance metrics
- 🎨 **Custom Assistants**: Configurable assistant names and personalities
- 🗂️ **Session Management**: Handle multiple independent conversations

## Installation

The chat module is included with GAIA. No additional installation required.

```bash
# Verify installation
python -c "from gaia.chat.sdk import ChatSDK; print('Chat SDK ready!')"
```

## Quick Start

### Simple Usage

```python
from gaia.chat.sdk import SimpleChat

# Create a simple chat instance
chat = SimpleChat()

# Ask a question
response = chat.ask("What's the weather like?")
print(response)

# The assistant remembers context
response = chat.ask("What did I just ask about?")
print(response)  # Will reference the weather question
```

### SDK Usage

```python
from gaia.chat.sdk import ChatSDK, ChatConfig

# Configure the SDK
config = ChatConfig(
    model="Llama-3.2-3B-Instruct-Hybrid",
    assistant_name="gaia",
    max_history_length=4,  # Keep 4 conversation pairs
    show_stats=True
)

# Create SDK instance
chat = ChatSDK(config)

# Send a message
response = chat.send("Hello! I'm learning Python.")
print(f"Gaia: {response.text}")

# Ask a follow-up (context is maintained)
response = chat.send("Can you help me with lists?")
print(f"Gaia: {response.text}")

# Check conversation history
history = chat.get_formatted_history()
for entry in history:
    print(f"{entry['role']}: {entry['message']}")
```

### Streaming Responses

```python
from gaia.chat.sdk import ChatSDK, ChatConfig

config = ChatConfig(assistant_name="gaia")
chat = ChatSDK(config)

# Stream the response
print("Gaia: ", end="", flush=True)
for chunk in chat.send_stream("Tell me a short story"):
    if not chunk.is_complete:
        print(chunk.text, end="", flush=True)
print()
```

## Running the Demo App

The chat app provides various demonstration modes:

```bash
# Run basic chat demo
python src/gaia/chat/app.py basic

# Run streaming demo
python src/gaia/chat/app.py stream

# Run session management demo
python src/gaia/chat/app.py sessions

# Show all demos
python src/gaia/chat/app.py all

# Show integration examples
python src/gaia/chat/app.py examples
```

## Configuration

### ChatConfig Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `model` | str | "Llama-3.2-3B-Instruct-Hybrid" | LLM model to use |
| `max_tokens` | int | 512 | Maximum tokens to generate |
| `system_prompt` | str | None | System prompt for the AI |
| `max_history_length` | int | 4 | Number of conversation pairs to keep |
| `assistant_name` | str | "gaia" | Name for the assistant |
| `show_stats` | bool | False | Show performance statistics |
| `logging_level` | str | "INFO" | Logging verbosity |
| `use_local_llm` | bool | True | Use local vs cloud models |

### Dynamic Configuration

```python
chat = ChatSDK(config)

# Update configuration at runtime
chat.update_config(
    assistant_name="helper",
    max_history_length=10,
    show_stats=True
)
```

## Advanced Features

### Session Management

Manage multiple independent conversations:

```python
from gaia.chat.sdk import ChatSession

# Create session manager
sessions = ChatSession()

# Create different sessions
work_chat = sessions.create_session(
    "work",
    system_prompt="You are a professional assistant",
    assistant_name="WorkBot"
)

personal_chat = sessions.create_session(
    "personal",
    system_prompt="You are a friendly companion",
    assistant_name="Buddy"
)

# Use different sessions
work_response = work_chat.send("Draft a professional email")
personal_response = personal_chat.send("What's a good movie to watch?")
```

### Interactive CLI Session

Start a full interactive session with commands:

```python
import asyncio
from gaia.chat.sdk import ChatSDK, ChatConfig

async def run_interactive():
    config = ChatConfig(
        assistant_name="gaia",
        show_stats=True
    )
    chat = ChatSDK(config)
    await chat.start_interactive_session()

# Run the interactive session
asyncio.run(run_interactive())
```

Interactive commands:
- `/clear` - Clear conversation history
- `/history` - Show conversation history
- `/stats` - Show performance statistics
- `/help` - Show help message
- `quit`, `exit`, `bye` - End session

### Conversation History Management

```python
# Get raw history
history = chat.get_history()
# Returns: ["user: Hello", "gaia: Hi there!", ...]

# Get formatted history
formatted = chat.get_formatted_history()
# Returns: [{"role": "user", "message": "Hello"}, ...]

# Clear history
chat.clear_history()

# Check history length
print(f"History entries: {chat.history_length}")
print(f"Conversation pairs: {chat.conversation_pairs}")
```

### Performance Monitoring

```python
config = ChatConfig(show_stats=True)
chat = ChatSDK(config)

response = chat.send("Hello!")

# Get statistics
stats = chat.get_stats()
print(f"Generation time: {stats.get('generation_time', 0):.3f}s")
print(f"Tokens generated: {stats.get('tokens_generated', 0)}")
print(f"Tokens/second: {stats.get('tokens_per_second', 0):.2f}")

# Display formatted stats
chat.display_stats(stats)
```

## API Reference

### ChatSDK

Main SDK class for chat functionality.

**Methods:**
- `send(message: str, **kwargs) -> ChatResponse`: Send message and get response
- `send_stream(message: str, **kwargs) -> Generator`: Stream response chunks
- `get_history() -> List[str]`: Get conversation history
- `get_formatted_history() -> List[Dict]`: Get structured history
- `clear_history()`: Clear conversation memory
- `get_stats() -> Dict`: Get performance statistics
- `display_stats(stats: Dict)`: Display formatted statistics
- `update_config(**kwargs)`: Update configuration dynamically
- `start_interactive_session()`: Start interactive CLI session

### SimpleChat

Simplified interface for basic usage.

**Methods:**
- `ask(question: str) -> str`: Ask a question and get response
- `ask_stream(question: str) -> Generator`: Stream response
- `clear_memory()`: Clear conversation history
- `get_conversation() -> List[Dict]`: Get conversation history

### ChatSession

Session manager for multiple conversations.

**Methods:**
- `create_session(session_id: str, config: ChatConfig) -> ChatSDK`: Create new session
- `get_session(session_id: str) -> ChatSDK`: Get existing session
- `delete_session(session_id: str) -> bool`: Delete session
- `list_sessions() -> List[str]`: List all session IDs
- `clear_all_sessions()`: Clear all sessions

### Convenience Functions

```python
# Quick one-off chat (no memory)
from gaia.chat.sdk import quick_chat
response = quick_chat("Hello!", assistant_name="gaia")

# Multi-turn chat with memory
from gaia.chat.sdk import quick_chat_with_memory
responses = quick_chat_with_memory([
    "My name is John",
    "What's my name?"
], assistant_name="gaia")
```

## Integration Examples

### Web Application Integration

```python
from flask import Flask, request, jsonify
from gaia.chat.sdk import ChatSession

app = Flask(__name__)
sessions = ChatSession()

@app.route('/chat', methods=['POST'])
def chat():
    session_id = request.json.get('session_id', 'default')
    message = request.json.get('message')
    
    # Get or create session
    chat = sessions.get_session(session_id)
    if not chat:
        chat = sessions.create_session(session_id)
    
    # Get response
    response = chat.send(message)
    return jsonify({
        'response': response.text,
        'session_id': session_id
    })
```

### Async Integration

```python
import asyncio
from gaia.chat.sdk import ChatSDK, ChatConfig

async def process_messages(messages):
    config = ChatConfig(assistant_name="gaia")
    chat = ChatSDK(config)
    
    responses = []
    for msg in messages:
        response = chat.send(msg)
        responses.append(response.text)
    
    return responses

# Run async
messages = ["Hello", "How are you?", "Tell me a joke"]
responses = asyncio.run(process_messages(messages))
```

### Custom System Prompts

```python
from gaia.chat.sdk import ChatSDK, ChatConfig

# Create specialized assistants
code_helper = ChatSDK(ChatConfig(
    system_prompt="You are an expert Python programmer. Provide clean, efficient code with explanations.",
    assistant_name="PyExpert"
))

creative_writer = ChatSDK(ChatConfig(
    system_prompt="You are a creative writer. Be imaginative and descriptive.",
    assistant_name="StoryTeller"
))

# Use different assistants for different tasks
code = code_helper.send("Write a function to reverse a list")
story = creative_writer.send("Start a mystery story")
```

## Troubleshooting

### Common Issues

1. **Import Error**: Ensure GAIA is properly installed and `src` is in your Python path
2. **No Response**: Check that Lemonade server is running for local models
3. **Memory Issues**: Adjust `max_history_length` for long conversations
4. **Performance**: Enable stats with `show_stats=True` to identify bottlenecks

### Debug Mode

```python
config = ChatConfig(
    logging_level="DEBUG",  # Enable debug logging
    show_stats=True         # Show performance metrics
)
```

## Contributing

To contribute to the chat module:

1. Follow the GAIA contribution guidelines
2. Add tests for new features
3. Update this README with new functionality
4. Ensure backward compatibility

## License

Copyright (C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.  
SPDX-License-Identifier: MIT

## Support

For issues or questions:
- Check the [main GAIA documentation](../../../README.md)
- Contact the GAIA team at gaia@amd.com
- Submit issues on the project repository
