# Gaia Chat SDK

The Gaia Chat SDK provides a unified, programmable interface for integrating text chat capabilities with conversation memory into applications. It offers multiple levels of abstraction from simple one-off questions to advanced session management.

## Table of Contents

- [Quick Start](#quick-start)
- [Core Classes](#core-classes)
- [Usage Examples](#usage-examples)
- [Assistant Naming](#assistant-naming)
- [CLI Integration](#cli-integration)
- [Advanced Features](#advanced-features)
- [API Reference](#api-reference)

## Quick Start

### Installation

The Chat SDK is included with Gaia. Import the components you need:

```python
from gaia.chat.sdk import ChatSDK, ChatConfig, SimpleChat
```

### Simple Usage

```python
from gaia.chat.sdk import SimpleChat

# Create a simple chat instance
chat = SimpleChat()

# Ask a question
response = chat.ask("What is Python?")
print(response)

# Ask follow-up questions (conversation memory included)
response = chat.ask("Give me an example")
print(response)
```

## Core Classes

### ChatSDK

The main SDK class providing full chat functionality with conversation memory, streaming, and performance statistics.

### ChatConfig

Configuration class for customizing chat behavior:

```python
@dataclass
class ChatConfig:
    model: str = "Llama-3.2-3B-Instruct-Hybrid"
    max_tokens: int = 512
    system_prompt: Optional[str] = None
    max_history_length: int = 4  # Number of conversation pairs to keep
    show_stats: bool = False
    logging_level: str = "INFO"
    use_local_llm: bool = True
    assistant_name: str = "assistant"  # Name to use for the assistant in conversations
```

### ChatResponse

Response object containing the AI's reply and optional metadata:

```python
@dataclass
class ChatResponse:
    text: str
    history: Optional[List[str]] = None
    stats: Optional[Dict[str, Any]] = None
    is_complete: bool = True
```

### SimpleChat

Lightweight wrapper for basic chat functionality without complex configuration.

### ChatSession

Session manager for handling multiple separate conversations with different contexts.

## Usage Examples

### Basic Chat with Memory

```python
from gaia.chat.sdk import ChatSDK, ChatConfig

# Create SDK instance with custom configuration
config = ChatConfig(
    model="Llama-3.2-3B-Instruct-Hybrid",
    show_stats=True,
    max_history_length=6
)
chat = ChatSDK(config)

# Send messages with conversation memory
response1 = chat.send("Hello! My name is Alex and I'm a software developer.")
print(response1.text)

response2 = chat.send("What's my name?")
print(response2.text)  # Will remember "Alex"

response3 = chat.send("What's my profession?")
print(response3.text)  # Will remember "software developer"

# Display performance statistics
if response3.stats:
    chat.display_stats(response3.stats)
```

### Streaming Chat

```python
from gaia.chat.sdk import ChatSDK

chat = ChatSDK()

print("AI: ", end="", flush=True)
for chunk in chat.send_stream("Tell me a story"):
    if not chunk.is_complete:
        print(chunk.text, end="", flush=True)
    else:
        # Final chunk with stats
        if chunk.stats:
            chat.display_stats(chunk.stats)
print()  # Newline after complete response
```

### Simple Integration

```python
from gaia.chat.sdk import SimpleChat

# Ultra-simple interface with default assistant name
chat = SimpleChat()
response = chat.ask("What's the weather like?")
print(response)

# With custom system prompt and assistant name
professional_chat = SimpleChat(
    system_prompt="You are a professional business assistant.",
    assistant_name="BusinessBot"
)
response = professional_chat.ask("Draft a meeting agenda")
print(response)

# Streaming version
for chunk in chat.ask_stream("Tell me about Python"):
    print(chunk, end="", flush=True)
```

## Assistant Naming

The Chat SDK supports customizable assistant names, allowing you to personalize the AI's identity in conversations and interactive sessions.

### Basic Assistant Naming

```python
from gaia.chat.sdk import ChatSDK, ChatConfig

# Create chat with custom assistant name
config = ChatConfig(
    model="Llama-3.2-3B-Instruct-Hybrid",
    assistant_name="Gaia"
)
chat = ChatSDK(config)

response = chat.send("What's your name?")
print(f"Gaia: {response.text}")

# Interactive session will display "Gaia:" instead of "Assistant:"
await chat.start_interactive_session()
```

### Different Assistant Names for Different Contexts

```python
from gaia.chat.sdk import ChatSDK, ChatConfig

# Code helper
code_config = ChatConfig(
    assistant_name="CodeBot",
    system_prompt="You are an expert programming assistant."
)
code_chat = ChatSDK(code_config)

# Creative helper
creative_config = ChatConfig(
    assistant_name="Muse", 
    system_prompt="You are a creative writing assistant."
)
creative_chat = ChatSDK(creative_config)

# Each has distinct identity
code_response = code_chat.send("Help me debug this Python function")
creative_response = creative_chat.send("Help me write a short story")

print(f"CodeBot: {code_response.text}")
print(f"Muse: {creative_response.text}")
```

### Assistant Names in Conversation History

```python
chat = ChatSDK(ChatConfig(assistant_name="Helper"))

chat.send("Hello!")
chat.send("How are you?")

# History maintains assistant name
history = chat.get_formatted_history()
for entry in history:
    print(f"{entry['role']}: {entry['message']}")
# Output:
# user: Hello!
# Helper: Hi there! I'm doing well, thank you for asking!
# user: How are you?
# Helper: I'm doing great! How can I help you today?
```

### Dynamic Assistant Name Changes

```python
chat = ChatSDK(ChatConfig(assistant_name="Bot"))

response1 = chat.send("Hello")
print(f"Bot: {response1.text}")

# Change assistant name dynamically
chat.update_config(assistant_name="Gaia")

response2 = chat.send("What's your name now?")
print(f"Gaia: {response2.text}")
```

### Session Management

```python
from gaia.chat.sdk import ChatSession

# Create session manager
sessions = ChatSession()

# Create different chat sessions with different contexts and assistant names
work_chat = sessions.create_session(
    "work", 
    system_prompt="You are a professional assistant for workplace tasks.",
    assistant_name="WorkBot"
)

personal_chat = sessions.create_session(
    "personal", 
    system_prompt="You are a friendly companion for casual conversation.",
    assistant_name="Buddy"
)

# Chat in different contexts
work_response = work_chat.send("Draft an email to my team about the project update")
personal_response = personal_chat.send("What's a good recipe for dinner?")

print(f"WorkBot: {work_response.text}")
print(f"Buddy: {personal_response.text}")

# Sessions maintain separate conversation histories
print(f"Work chat history: {work_chat.get_formatted_history()}")
print(f"Personal chat history: {personal_chat.get_formatted_history()}")

# List all sessions
print(f"Active sessions: {sessions.list_sessions()}")
```

### Quick One-off Usage

```python
from gaia.chat.sdk import quick_chat, quick_chat_with_memory

# Single message without conversation memory
response = quick_chat("What is machine learning?")
print(response)

# With custom assistant name
response = quick_chat("What is machine learning?", assistant_name="Expert")
print(response)

# Multi-turn conversation with memory and custom assistant name
responses = quick_chat_with_memory([
    "My name is John and I live in Seattle",
    "What's my name?",
    "Where do I live?"
], assistant_name="Helper")

for i, response in enumerate(responses, 1):
    print(f"Helper Response {i}: {response}")
```

### Interactive Chat Session

```python
from gaia.chat.sdk import ChatSDK
import asyncio

async def interactive_demo():
    config = ChatConfig(show_stats=True)
    chat = ChatSDK(config)
    
    # Start interactive session with built-in commands
    await chat.start_interactive_session()

# Run the interactive session
asyncio.run(interactive_demo())
```

## CLI Integration

The Chat SDK is integrated into the Gaia CLI for command-line usage:

### Interactive Mode

```bash
# Start interactive chat session
gaia chat

# Start with custom model
gaia chat --model "Llama-3.2-3B-Instruct-Hybrid"

# Start with performance statistics
gaia chat --stats
```

### Single Message Mode

```bash
# Send single message
gaia chat "What is artificial intelligence?"

# With custom parameters
gaia chat "Explain Python" --model "Llama-3.2-3B-Instruct-Hybrid" --max-tokens 1000

# With system prompt
gaia chat "Help me code" --system-prompt "You are an expert Python developer"

# Show performance statistics
gaia chat "Hello" --stats
```

### Interactive Session Commands

When in interactive mode, use these commands:

- `/clear` - Clear conversation history
- `/history` - Show conversation history
- `/stats` - Show performance statistics
- `/help` - Show help message
- `quit`, `exit`, or `bye` - End conversation

## Advanced Features

### Dynamic Configuration Updates

```python
chat = ChatSDK()

# Update configuration on the fly
chat.update_config(
    max_tokens=1000,
    show_stats=True,
    max_history_length=10,
    assistant_name="UpdatedBot"  # Change assistant name dynamically
)
```

### Conversation History Management

```python
chat = ChatSDK()

# Send some messages
chat.send("Hello")
chat.send("How are you?")

# Get raw history
history = chat.get_history()
print(history)  # ['user: Hello', 'assistant: ...', 'user: How are you?' 'assistant: ...']
# Note: Assistant name in history reflects the configured name

# Get formatted history
formatted = chat.get_formatted_history()
for entry in formatted:
    print(f"{entry['role']}: {entry['message']}")

# Clear history
chat.clear_history()

# Check conversation metrics
print(f"History length: {chat.history_length}")
print(f"Conversation pairs: {chat.conversation_pairs}")
```

### Performance Monitoring

```python
config = ChatConfig(show_stats=True)
chat = ChatSDK(config)

response = chat.send("Hello")

# Get performance statistics
stats = chat.get_stats()
print(stats)

# Display formatted statistics
chat.display_stats(stats)

# Stats include:
# - time_to_first_token: Time to generate first token (seconds)
# - tokens_per_second: Generation speed
# - input_tokens: Number of input tokens
# - output_tokens: Number of generated tokens
```

### Error Handling

```python
from gaia.chat.sdk import ChatSDK

chat = ChatSDK()

try:
    response = chat.send("")  # Empty message
except ValueError as e:
    print(f"Invalid input: {e}")

try:
    response = chat.send("Hello")
except Exception as e:
    print(f"Chat error: {e}")
```

## API Reference

### ChatSDK Methods

#### Core Methods
- `send(message: str, **kwargs) -> ChatResponse` - Send message and get complete response
- `send_stream(message: str, **kwargs)` - Send message and get streaming response
- `get_history() -> List[str]` - Get conversation history
- `clear_history() -> None` - Clear conversation history
- `get_formatted_history() -> List[Dict[str, str]]` - Get structured history

#### Configuration & Stats
- `update_config(**kwargs) -> None` - Update configuration dynamically
- `get_stats() -> Dict[str, Any]` - Get performance statistics
- `display_stats(stats: Optional[Dict[str, Any]] = None) -> None` - Display formatted stats

#### Interactive Mode
- `start_interactive_session() -> None` - Start CLI-style interactive session

#### Properties
- `history_length: int` - Number of history entries
- `conversation_pairs: int` - Number of conversation pairs

### SimpleChat Methods

- `__init__(system_prompt: Optional[str] = None, model: Optional[str] = None, assistant_name: Optional[str] = None)` - Initialize with optional assistant name
- `ask(question: str) -> str` - Ask question and get response
- `ask_stream(question: str)` - Ask question and get streaming response
- `clear_memory() -> None` - Clear conversation memory
- `get_conversation() -> List[Dict[str, str]]` - Get conversation history

### ChatSession Methods

- `create_session(session_id: str, config: Optional[ChatConfig] = None, **config_kwargs) -> ChatSDK`
  - `config_kwargs` supports `assistant_name`, `system_prompt`, `model`, etc.
- `get_session(session_id: str) -> Optional[ChatSDK]`
- `delete_session(session_id: str) -> bool`
- `list_sessions() -> List[str]`
- `clear_all_sessions() -> None`

### Utility Functions

- `quick_chat(message: str, system_prompt: Optional[str] = None, model: Optional[str] = None, assistant_name: Optional[str] = None) -> str`
- `quick_chat_with_memory(messages: List[str], system_prompt: Optional[str] = None, model: Optional[str] = None, assistant_name: Optional[str] = None) -> List[str]`

## Best Practices

1. **Choose the Right Interface**: Use `SimpleChat` for basic needs, `ChatSDK` for advanced features, and `ChatSession` for multi-context applications.

2. **Memory Management**: Configure `max_history_length` based on your needs. Longer histories provide better context but use more memory.

3. **Performance Monitoring**: Enable `show_stats=True` during development to monitor performance.

4. **Error Handling**: Always wrap chat operations in try-catch blocks for production applications.

5. **Resource Cleanup**: Clear sessions and history when no longer needed to free memory.

6. **Model Selection**: Choose appropriate models based on your performance and accuracy requirements.

7. **Assistant Naming**: Use meaningful assistant names to create distinct identities for different use cases (e.g., "CodeBot" for programming, "Writer" for creative tasks).

## Examples Repository

For more examples and integration patterns, run:

```bash
python src/gaia/chat/app.py examples
```

This will show additional usage patterns and integration examples.