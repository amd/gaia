#!/usr/bin/env python3
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Gaia Chat SDK Demo Application

This demonstrates various ways to use the ChatSDK for integrating
text chat capabilities with conversation history into your applications.
"""

import argparse
import asyncio
import sys

from gaia.chat.sdk import (
    ChatConfig,
    ChatSDK,
    ChatSession,
    SimpleChat,
    quick_chat,
    quick_chat_with_memory,
)
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME


async def demo_basic_chat():
    """Demo basic chat functionality with conversation memory."""
    print("=== Basic Chat Demo ===")

    config = ChatConfig(
        model=DEFAULT_MODEL_NAME,
        assistant_name="Gaia",  # Custom assistant name
        show_stats=True,
        max_history_length=3,  # Keep 3 conversation pairs
        logging_level="INFO",
    )
    chat = ChatSDK(config)

    # Multi-turn conversation
    messages = [
        "Hello! My name is Alex and I'm a software developer.",
        "What's my name?",
        "What's my profession?",
        "Can you write a simple Python function?",
    ]

    for i, message in enumerate(messages, 1):
        print(f"\nTurn {i}")
        print(f"User: {message}")
        response = chat.send(message)
        print(f"Gaia: {response.text}")

        if response.stats and i == len(messages):  # Show stats on last message
            print(f"Stats: {response.stats}")

    print(f"\nConversation pairs in memory: {chat.conversation_pairs}")


async def demo_streaming_chat():
    """Demo streaming chat functionality."""
    print("\n=== Streaming Chat Demo ===")

    config = ChatConfig(
        model=DEFAULT_MODEL_NAME,
        assistant_name="StreamBot",
        show_stats=True,
    )
    chat = ChatSDK(config)

    # First establish context
    chat.send("I'm learning about AI and machine learning.")

    print(
        "User: Can you explain neural networks in simple terms, based on what you know about my interests?"
    )
    print("StreamBot: ", end="", flush=True)

    for chunk in chat.send_stream(
        "Can you explain neural networks in simple terms, based on what you know about my interests?"
    ):
        if not chunk.is_complete:
            print(chunk.text, end="", flush=True)
        else:
            print()  # New line at the end
            if chunk.stats:
                print(f"Stats: {chunk.stats}")


async def demo_simple_chat():
    """Demo the SimpleChat API."""
    print("\n=== Simple Chat Demo ===")

    # Show both default and custom assistant names
    print("--- Default Assistant Name ---")
    chat1 = SimpleChat(system_prompt="You are a helpful programming assistant.")
    print("User: I'm working on a Python project")
    response1 = chat1.ask("I'm working on a Python project")
    print(f"Assistant: {response1}")

    print("\n--- Custom Assistant Name ---")
    chat2 = SimpleChat(
        system_prompt="You are a helpful programming assistant.",
        assistant_name="CodeHelper",
    )
    print("User: Can you help me with error handling?")
    response2 = chat2.ask("Can you help me with error handling?")
    print(f"CodeHelper: {response2}")

    # Show conversation history
    conversation = chat2.get_conversation()
    print(f"\nConversation history: {len(conversation)} entries")
    if conversation:
        print("Sample entry:", conversation[0])


async def demo_chat_sessions():
    """Demo session-based chat management."""
    print("\n=== Chat Sessions Demo ===")

    # Create session manager
    sessions = ChatSession()

    # Create different themed sessions with custom assistant names
    work_chat = sessions.create_session(
        "work",
        system_prompt="You are a professional business assistant.",
        assistant_name="WorkBot",
        max_history_length=2,
    )

    casual_chat = sessions.create_session(
        "casual",
        system_prompt="You are a friendly, casual conversation partner.",
        assistant_name="Buddy",
        max_history_length=2,
    )

    # Chat in work context
    print("=== Work Session ===")
    print("User: I need to write a project proposal")
    work_response = work_chat.send("I need to write a project proposal")
    print(f"WorkBot: {work_response.text}")

    # Chat in casual context
    print("\n=== Casual Session ===")
    print("User: I need to write a project proposal")
    casual_response = casual_chat.send("I need to write a project proposal")
    print(f"Buddy: {casual_response.text}")

    print(f"\nActive sessions: {sessions.list_sessions()}")


async def demo_quick_functions():
    """Demo convenience functions."""
    print("\n=== Quick Functions Demo ===")

    # Single message
    print("User: What's the capital of France?")
    response = quick_chat("What's the capital of France?")
    print(f"AI: {response}")

    # Multi-turn with memory and custom assistant name
    print("\n=== Multi-turn Quick Chat ===")
    messages = [
        "I have a pet dog named Max",
        "What's my pet's name?",
        "What kind of animal is Max?",
    ]

    responses = quick_chat_with_memory(messages, assistant_name="QuickBot")
    for msg, resp in zip(messages, responses):
        print(f"User: {msg}")
        print(f"QuickBot: {resp}")


async def demo_configuration():
    """Demo configuration options."""
    print("\n=== Configuration Demo ===")

    # Create chat with custom config
    config = ChatConfig(
        model=DEFAULT_MODEL_NAME,
        system_prompt="You are a helpful assistant that always responds enthusiastically!",
        max_history_length=2,
        show_stats=True,
    )
    chat = ChatSDK(config)

    print("User: How are you today?")
    response1 = chat.send("How are you today?")
    print(f"AI: {response1.text}")

    # Update configuration dynamically including assistant name
    chat.update_config(
        system_prompt="You are now a serious, professional assistant.",
        assistant_name="Professional",
        max_history_length=1,
    )

    print("\nUser: How are you today? (after config change)")
    response2 = chat.send("How are you today?")
    print(f"Professional: {response2.text}")

    print(f"\nHistory length after config change: {chat.history_length}")
    print(f"Current assistant name: {chat.config.assistant_name}")


def print_integration_examples():
    """Print example code for integration."""
    print("\n" + "=" * 60)
    print("INTEGRATION EXAMPLES")
    print("=" * 60)

    print(
        """
Basic Integration:
```python
from gaia.chat.sdk import ChatSDK, ChatConfig

# Create SDK instance
config = ChatConfig(model=DEFAULT_MODEL_NAME, show_stats=True)
chat = ChatSDK(config)

# Send message with conversation memory
response = chat.send("Hello!")
print(response.text)

# Streaming chat
for chunk in chat.send_stream("Tell me a story"):
    print(chunk.text, end="", flush=True)
```

Assistant Naming:
```python
from gaia.chat.sdk import ChatSDK, ChatConfig

# Create SDK with custom assistant name
config = ChatConfig(
    model=DEFAULT_MODEL_NAME,
    assistant_name="Gaia"
)
chat = ChatSDK(config)

response = chat.send("What's your name?")
print(f"Gaia: {response.text}")

# Interactive session will show "Gaia:" instead of "Assistant:"
await chat.start_interactive_session()
```

Simple Integration:
```python
from gaia.chat.sdk import SimpleChat

# Default assistant name
chat = SimpleChat()
response = chat.ask("What's the weather?")
print(response)

# Custom assistant name
chat = SimpleChat(assistant_name="Helper")
response = chat.ask("What's the weather?")
print(response)
```

Session Management:
```python
from gaia.chat.sdk import ChatSession

sessions = ChatSession()
work_chat = sessions.create_session(
    "work", 
    system_prompt="Professional assistant",
    assistant_name="WorkBot"
)
personal_chat = sessions.create_session(
    "personal", 
    system_prompt="Friendly companion",
    assistant_name="Buddy"
)

work_response = work_chat.send("Draft an email")
personal_response = personal_chat.send("What's for dinner?")
```

Quick One-off Usage:
```python
from gaia.chat.sdk import quick_chat, quick_chat_with_memory

# Single message with custom assistant name
response = quick_chat("Hello!", assistant_name="Gaia")

# Multi-turn conversation with custom assistant name
responses = quick_chat_with_memory([
    "My name is John",
    "What's my name?"
], assistant_name="Gaia")
```
"""
    )


async def main():
    """Main entry point for the Chat SDK demo application."""
    parser = argparse.ArgumentParser(
        description="Gaia Chat SDK Demo - Examples of text chat with conversation history",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Demo Types:
  basic     - Basic chat with conversation memory
  stream    - Streaming chat demo
  simple    - SimpleChat API demo
  sessions  - Session management demo
  quick     - Quick functions demo
  config    - Configuration options demo
  all       - Run all demos sequentially
  examples  - Show integration code examples only
        """,
    )

    parser.add_argument(
        "demo",
        nargs="?",
        default="basic",
        choices=[
            "basic",
            "stream",
            "simple",
            "sessions",
            "quick",
            "config",
            "all",
            "examples",
        ],
        help="Type of demo to run (default: basic)",
    )

    # Configuration options for demos
    parser.add_argument(
        "--model", default=DEFAULT_MODEL_NAME, help="Model to use for demos"
    )
    parser.add_argument("--system-prompt", help="Custom system prompt for the AI")
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose logging"
    )

    args = parser.parse_args()

    # Show just examples
    if args.demo == "examples":
        print_integration_examples()
        return

    print("💬 Gaia Chat SDK Demo")
    print("=" * 50)

    try:
        if args.demo == "all":
            # Run all demos
            await demo_basic_chat()
            await demo_streaming_chat()
            await demo_simple_chat()
            await demo_chat_sessions()
            await demo_quick_functions()
            await demo_configuration()
            print_integration_examples()

        elif args.demo == "basic":
            await demo_basic_chat()
        elif args.demo == "stream":
            await demo_streaming_chat()
        elif args.demo == "simple":
            await demo_simple_chat()
        elif args.demo == "sessions":
            await demo_chat_sessions()
        elif args.demo == "quick":
            await demo_quick_functions()
        elif args.demo == "config":
            await demo_configuration()

        # Always show integration examples at the end
        if args.demo != "all":
            print_integration_examples()

    except KeyboardInterrupt:
        print("\n\nDemo interrupted. Goodbye!")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        if args.verbose:
            import traceback

            traceback.print_exc()
        sys.exit(1)

    print("\n✅ Demo completed successfully!")
    print("\nTo integrate ChatSDK into your app:")
    print("  from gaia.chat.sdk import ChatSDK, ChatConfig")
    print("\nFor more examples, run: python app.py examples")


# Keep the original main function for backward compatibility with CLI
def cli_main(
    message: str = None,
    model: str = None,
    max_tokens: int = 512,
    system_prompt: str = None,
    interactive: bool = False,
) -> str:
    """Main function to run the Chat app (backward compatibility)."""
    if interactive:
        print("Interactive mode not available in demo app.")
        print("Use: python app.py basic")
        return None
    elif message:
        # Use SimpleChat for backward compatibility
        config = ChatConfig(
            model=model or DEFAULT_MODEL_NAME,
            max_tokens=max_tokens,
            system_prompt=system_prompt,
        )
        chat = ChatSDK(config)
        response = chat.send(message)
        return response.text
    else:
        raise ValueError("Either message or interactive mode is required")


if __name__ == "__main__":
    asyncio.run(main())
