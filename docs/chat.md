# Gaia Chat SDK

The Gaia Chat SDK provides a programmable interface for text chat with conversation memory and optional document Q&A (RAG).

## Table of Contents

- [Quick Start](#quick-start)
- [Core Classes](#core-classes)
- [CLI Usage](#cli-usage)
- [Python SDK Examples](#python-sdk-examples)
- [Document Q&A (RAG)](#document-qa-rag)
- [API Reference](#api-reference)

## Quick Start

### Installation

```bash
# Activate virtual environment (if not already active)
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# Linux/macOS:
source .venv/bin/activate

# Using uv (recommended - much faster):
uv pip install -e .[rag]  # Include RAG for document Q&A

# Or using pip:
# Linux/Windows:
pip install -e .[rag]

# macOS:
pip install -e ".[rag]"
```

### Simple Chat

```python
from gaia.chat.sdk import SimpleChat

chat = SimpleChat()
response = chat.ask("What is Python?")
print(response)

# Follow-up with conversation memory
response = chat.ask("Give me an example")
print(response)
```

### Full SDK

```python
from gaia.chat.sdk import ChatSDK, ChatConfig

config = ChatConfig(
    show_stats=True,
    max_history_length=6
)
chat = ChatSDK(config)

response = chat.send("Hello! My name is Alex.")
print(response.text)

response = chat.send("What's my name?")
print(response.text)  # Will remember "Alex"
```

## Core Classes

### ChatConfig

```python
@dataclass
class ChatConfig:
    model: str = "Qwen3-Coder-30B-A3B-Instruct-GGUF"  # Default validated model
    max_tokens: int = 512
    system_prompt: Optional[str] = None
    max_history_length: int = 4
    show_stats: bool = False
    use_local_llm: bool = True
    assistant_name: str = "assistant"
```

### ChatResponse

```python
@dataclass
class ChatResponse:
    text: str
    history: Optional[List[str]] = None
    stats: Optional[Dict[str, Any]] = None
```

### SimpleChat

Lightweight wrapper for basic chat without complex configuration.

## CLI Usage

### Interactive Mode

```bash
# Start interactive chat
gaia chat

# With performance statistics
gaia chat --stats
```

**Interactive Commands:**
- `/clear` - Clear conversation history
- `/history` - Show conversation history
- `/stats` - Show performance statistics
- `/help` - Show help message
- `quit`, `exit`, `bye` - End conversation

### Single Query Mode

```bash
# One-shot query
gaia chat "What is artificial intelligence?"

# With statistics
gaia chat --query "Hello" --show-stats
```

## Python SDK Examples

### Streaming Chat

```python
from gaia.chat.sdk import ChatSDK

chat = ChatSDK()

print("AI: ", end="", flush=True)
for chunk in chat.send_stream("Tell me a story"):
    if not chunk.is_complete:
        print(chunk.text, end="", flush=True)
print()
```

### Custom Assistant Name

```python
config = ChatConfig(
    assistant_name="Gaia",
    system_prompt="You are Gaia, a helpful AI assistant."
)
chat = ChatSDK(config)

response = chat.send("What's your name?")
print(f"Gaia: {response.text}")
```

### Session Management

```python
from gaia.chat.sdk import ChatSession

sessions = ChatSession()

# Create different contexts with custom names
work_chat = sessions.create_session(
    "work",
    system_prompt="You are a professional assistant.",
    assistant_name="WorkBot"
)

personal_chat = sessions.create_session(
    "personal",
    system_prompt="You are a friendly companion.",
    assistant_name="Buddy"
)

# Separate conversation histories
work_response = work_chat.send("Draft a team email")
personal_response = personal_chat.send("What's for dinner?")
```

### Conversation History

```python
chat = ChatSDK()

chat.send("Hello")
chat.send("How are you?")

# Get formatted history
for entry in chat.get_formatted_history():
    print(f"{entry['role']}: {entry['message']}")

# Clear history
chat.clear_history()

# Check metrics
print(f"Conversation pairs: {chat.conversation_pairs}")
```

## Document Q&A (RAG)

RAG enables chatting with PDF documents using semantic search and context retrieval.

### Python SDK with RAG

```python
from gaia.chat.sdk import ChatSDK

chat = ChatSDK()

# Enable RAG and index documents
chat.enable_rag(documents=["manual.pdf", "guide.pdf"])

# Chat with document context
response = chat.send("What does the manual say about installation?")
print(response.text)

# Add more documents
chat.add_document("troubleshooting.pdf")

# Disable RAG when done
chat.disable_rag()
```

### CLI with RAG

```bash
# Chat with single document
gaia chat --index manual.pdf

# Chat with multiple documents
gaia chat --index doc1.pdf doc2.pdf doc3.pdf

# One-shot query with document
gaia chat --index report.pdf --query "Summarize the key findings"

# Voice with documents
gaia talk --index manual.pdf
```

### RAG Configuration

```python
# Advanced RAG setup
chat.enable_rag(
    documents=["doc1.pdf", "doc2.pdf"],
    chunk_size=600,      # Larger chunks for more context
    max_chunks=4,        # More chunks per query
    chunk_overlap=100,   # Overlap for context preservation
    show_stats=True
)

# Check indexed documents
if chat.rag:
    status = chat.rag.get_status()
    print(f"Indexed {status['indexed_files']} files")
    print(f"Total chunks: {status['total_chunks']}")
```

### RAG Debug Mode

Enable debug mode to see detailed retrieval information:

```bash
# CLI with debug
gaia chat --index document.pdf --debug
```

```python
# Python SDK with debug
from gaia.agents.chat.agent import ChatAgent

agent = ChatAgent(
    rag_documents=['document.pdf'],
    debug=True,
    silent_mode=False
)

result = agent.process_query("What is the vision statement?")
print(f"Debug trace saved to: {result['output_file']}")
```

**Debug info includes:**
- Search keys generated by the LLM
- Chunks found for each search
- Relevance scores
- Deduplication statistics
- Score distributions

### Chunking Strategies

```python
# 1. Structural Chunking (Default) - Fast
agent = ChatAgent(
    rag_documents=['document.pdf'],
    chunk_size=500,
    chunk_overlap=50
)

# 2. LLM-Based Semantic Chunking - More accurate
agent = ChatAgent(
    rag_documents=['document.pdf'],
    use_llm_chunking=True,
    chunk_size=500
)
```

### RAG Troubleshooting

**Missing dependencies:**
```bash
# Using uv (recommended):
uv pip install -e .[rag]

# Or using pip:
# Linux/Windows:
pip install -e .[rag]
# macOS:
pip install -e ".[rag]"
```

**PDF issues:**
- Ensure PDF has extractable text (not scanned images)
- Check file is not password-protected
- Verify file is not corrupted

**Performance tuning:**
```python
# Faster processing
chat.enable_rag(documents=["doc.pdf"], chunk_size=300, max_chunks=2)

# Better quality
chat.enable_rag(documents=["doc.pdf"], chunk_size=600, max_chunks=5, chunk_overlap=100)

# Memory efficient
chat.enable_rag(documents=["doc.pdf"], chunk_size=400, max_chunks=2)
```

## API Reference

### ChatSDK Core Methods

**Messaging:**
- `send(message: str) -> ChatResponse` - Send message, get complete response
- `send_stream(message: str)` - Send message, get streaming response

**History:**
- `get_history() -> List[str]` - Get conversation history
- `clear_history()` - Clear conversation history
- `get_formatted_history() -> List[Dict]` - Get structured history

**RAG (Document Q&A):**
- `enable_rag(documents=None, **kwargs)` - Enable RAG with optional documents
- `disable_rag()` - Disable RAG
- `add_document(path: str) -> bool` - Add document to index

**Configuration:**
- `update_config(**kwargs)` - Update configuration dynamically
- `get_stats() -> Dict` - Get performance statistics
- `display_stats(stats=None)` - Display formatted statistics

**Interactive:**
- `start_interactive_session()` - Start CLI-style interactive mode

### SimpleChat Methods

- `ask(question: str) -> str` - Ask question, get response
- `ask_stream(question: str)` - Ask question, stream response
- `clear_memory()` - Clear conversation memory
- `get_conversation() -> List[Dict]` - Get conversation history

### ChatSession Methods

- `create_session(id, config=None, **kwargs) -> ChatSDK` - Create new session
- `get_session(id) -> ChatSDK` - Get existing session
- `delete_session(id) -> bool` - Delete session
- `list_sessions() -> List[str]` - List all sessions
- `clear_all_sessions()` - Delete all sessions

### Utility Functions

- `quick_chat(message, system_prompt=None, model=None, assistant_name=None) -> str`
- `quick_chat_with_memory(messages, system_prompt=None, model=None, assistant_name=None) -> List[str]`

## Best Practices

1. **Choose the Right Interface**: `SimpleChat` for basic needs, `ChatSDK` for full features, `ChatSession` for multi-context apps
2. **Memory Management**: Configure `max_history_length` based on memory constraints
3. **Performance**: Enable `show_stats=True` during development
4. **Error Handling**: Wrap chat operations in try-catch blocks
5. **Resource Cleanup**: Clear sessions when done to free memory
6. **Assistant Naming**: Use meaningful names for distinct use cases
