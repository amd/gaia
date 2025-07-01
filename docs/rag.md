# Running RAG Agent

## Overview

The RAG (Retrieval-Augmented Generation) agent is a specialized AI assistant that can answer questions based on specific documents or content. It uses a combination of document indexing and language models to provide accurate, context-aware responses.

## Setup and Running using CLI tool

### 1. Start the RAG Agent Server

First, start the RAG agent server with an input file:

```bash
gaia-cli start --agent-name Rag --input-file ./data/html/blender/introduction.html
```

Additional options you can use:
- `--model`: Specify the model to use (default: "llama3.2:1b")
- `--max-new-tokens`: Maximum response length (default: 512)
- `--background`: Launch mode ["terminal", "silent", "none"] (default: "silent")
- `--stats`: Show performance statistics after generation
- `--logging-level`: Set logging verbosity ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] (default: "INFO")

Example with additional options:
```bash
gaia-cli start --agent-name Rag \
    --input-file ./data/html/blender/introduction.html \
    --model llama3.2:1b \
    --max-new-tokens 512 \
    --background silent \
    --stats \
    --logging-level INFO
```

Note: The RAG agent uses an extended timeout (30 minutes) for index building compared to other agents (2 minutes).

### 2. Interact with the RAG Agent

You can interact with the RAG agent in two ways:

#### Text-based Chat
```bash
gaia-cli chat
```
This opens an interactive chat interface where you can:
- Type your questions and press Enter to send
- Type `stop` to quit the chat session
- Type `restart` to clear the chat history

Example interaction:
```
Starting chat with Rag. Type 'stop' to quit or 'restart' to clear chat history.
You: What is the main purpose of the top bar?
Rag: The top bar serves as the main navigation and control interface in Blender, providing access to essential tools and functions for 3D modeling and animation.
You: stop
Chat session ended.
```

#### Voice-based Chat
```bash
gaia-cli talk
```
This enables voice interaction where you can:
- Speak your questions naturally
- Say "stop" to quit the application
- Say "restart" to clear the chat history
- Press Enter key to stop during audio playback

Voice chat options:
- `--no-tts`: Disable text-to-speech in voice chat mode
- `--audio-device-index`: Index of the audio input device to use (default: 1)
- `--whisper-model-size`: Size of the Whisper model ["tiny", "base", "small", "medium", "large"] (default: "base")

### 3. Stop the RAG Agent

When you're done, stop the RAG agent server:
```bash
gaia-cli stop
```

## How the RAG Agent Works

1. **Document Processing**:
   - The agent loads and processes the input document(s)
   - Creates a vector index for efficient retrieval
   - Uses the BAAI/bge-base-en-v1.5 model for embeddings

2. **Query Processing**:
   - Questions are processed against the indexed content
   - Uses similarity search to find relevant context
   - Generates responses based on the retrieved context

3. **Response Generation**:
   - Uses a local LLM for response generation
   - Maintains context awareness through the conversation
   - Provides streaming responses for better interaction

## Best Practices

1. **Input Documents**:
   - Use well-structured documents for better indexing
   - Support for both single files and directories
   - HTML, text, and other common document formats are supported

2. **Querying**:
   - Ask specific, focused questions
   - Questions should be related to the content in your input documents
   - The agent will indicate if it cannot answer from the provided context

3. **Performance**:
   - Larger documents may take longer to index
   - Consider using appropriate model sizes for your use case
   - Monitor system resources during index building

## Troubleshooting

1. **Index Building Issues**:
   - Check if the input file exists and is accessible
   - Verify file permissions
   - Monitor the logs for specific error messages

2. **Response Quality**:
   - Ensure input documents are well-formatted
   - Try rephrasing questions if responses are unclear
   - Use the `restart` command to clear context if needed

3. **Server Issues**:
   - Check if required ports (8000, 8001) are available
   - Use `gaia-cli kill --port PORT` to clear stuck processes
   - Review logs in `gaia.cli.log` for detailed error information
