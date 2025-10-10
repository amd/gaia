#!/usr/bin/env python3
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Gaia Talk SDK Demo Application

This demonstrates various ways to use the TalkSDK for integrating
voice and text chat capabilities into your applications.
"""

import argparse
import asyncio
import sys

from gaia.talk.sdk import SimpleTalk, TalkConfig, TalkSDK, quick_chat


async def demo_text_chat():
    """Demo basic text chat functionality."""
    print("=== Text Chat Demo ===")

    config = TalkConfig(
        enable_tts=False,  # Disable TTS for text-only demo
        show_stats=True,
        logging_level="INFO",
    )
    talk = TalkSDK(config)

    # Simple chat
    print("User: Hello! Can you tell me a short joke?")
    response = await talk.chat("Hello! Can you tell me a short joke?")
    print(f"AI: {response.text}")
    if response.stats:
        print(f"Stats: {response.stats}")


async def demo_streaming_chat():
    """Demo streaming text chat functionality."""
    print("\n=== Streaming Chat Demo ===")

    config = TalkConfig(enable_tts=False, show_stats=True, logging_level="INFO")
    talk = TalkSDK(config)

    # Streaming chat
    print("User: Tell me a very short story")
    print("AI: ", end="", flush=True)
    async for chunk in talk.chat_stream("Tell me a very short story"):
        if not chunk.is_complete:
            print(chunk.text, end="", flush=True)
        else:
            print()  # New line at the end
            if chunk.stats:
                print(f"Stats: {chunk.stats}")


async def demo_voice_chat():
    """Demo voice chat functionality."""
    print("\n=== Voice Chat Demo ===")

    config = TalkConfig(enable_tts=True, show_stats=True, logging_level="INFO")
    talk = TalkSDK(config)

    def on_voice_input(text: str):
        print(f"You said: {text}")

    print("Starting voice chat... Say 'stop' to quit or press Ctrl+C")
    await talk.start_voice_session(on_voice_input)


async def demo_simple_api():
    """Demo the SimpleTalk API."""
    print("\n=== Simple API Demo ===")

    talk = SimpleTalk(enable_tts=False)

    # Simple question
    print("User: What's 2+2?")
    response = await talk.ask("What's 2+2?")
    print(f"AI: {response}")

    # Streaming response
    print("User: Count from 1 to 5")
    print("AI: ", end="", flush=True)
    async for chunk in talk.ask_stream("Count from 1 to 5"):
        print(chunk, end="", flush=True)
    print()  # New line


async def demo_quick_functions():
    """Demo convenience functions."""
    print("\n=== Quick Functions Demo ===")

    print("User: Hello! How are you?")
    response = await quick_chat("Hello! How are you?")
    print(f"AI: {response}")


async def demo_configuration():
    """Demo configuration options."""
    print("\n=== Configuration Demo ===")

    # Custom system prompt
    config = TalkConfig(
        system_prompt="You are a helpful assistant that always responds in a friendly, enthusiastic way!",
        enable_tts=False,
        show_stats=True,
    )
    talk = TalkSDK(config)

    print("User: How are you today?")
    response = await talk.chat("How are you today?")
    print(f"AI: {response.text}")

    # Update configuration dynamically
    talk.update_config(system_prompt="You are now a serious, professional assistant.")

    print("User: How are you today? (after config change)")
    response = await talk.chat("How are you today?")
    print(f"AI: {response.text}")


def print_integration_examples():
    """Print example code for integration."""
    print("\n" + "=" * 60)
    print("INTEGRATION EXAMPLES")
    print("=" * 60)

    print(
        """
Basic Integration:
```python
from gaia.talk.sdk import TalkSDK, TalkConfig

# Create SDK instance
config = TalkConfig(enable_tts=True, show_stats=True)
talk = TalkSDK(config)

# Text chat
response = await talk.chat("Hello!")
print(response.text)

# Streaming chat
async for chunk in talk.chat_stream("Tell me a story"):
    print(chunk.text, end="", flush=True)
```

Simple Integration:
```python
from gaia.talk.sdk import SimpleTalk

talk = SimpleTalk()
response = await talk.ask("What's the weather?")
print(response)
```

Voice Chat Integration:
```python
from gaia.talk.sdk import TalkSDK, TalkConfig

config = TalkConfig(enable_tts=True)
talk = TalkSDK(config)

def on_voice_input(text):
    print(f"User: {text}")

await talk.start_voice_session(on_voice_input)
```

Quick One-off Usage:
```python
from gaia.talk.sdk import quick_chat

response = await quick_chat("Hello!")
print(response)
```
"""
    )


async def main():
    """Main entry point for the Talk SDK demo application."""
    parser = argparse.ArgumentParser(
        description="Gaia Talk SDK Demo - Examples of voice and text chat integration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Demo Types:
  text      - Basic text chat demo
  stream    - Streaming text chat demo
  voice     - Voice chat demo with TTS/ASR
  simple    - SimpleTalk API demo
  quick     - Quick functions demo
  config    - Configuration options demo
  all       - Run all demos sequentially
  examples  - Show integration code examples only
        """,
    )

    parser.add_argument(
        "demo",
        nargs="?",
        default="text",
        choices=[
            "text",
            "stream",
            "voice",
            "simple",
            "quick",
            "config",
            "all",
            "examples",
        ],
        help="Type of demo to run (default: text)",
    )

    # Configuration options for demos
    parser.add_argument("--system-prompt", help="Custom system prompt for the AI")
    parser.add_argument(
        "--no-tts", action="store_true", help="Disable text-to-speech (for voice demo)"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose logging"
    )

    args = parser.parse_args()

    # Show just examples
    if args.demo == "examples":
        print_integration_examples()
        return

    print("🤖 Gaia Talk SDK Demo")
    print("=" * 50)

    try:
        if args.demo == "all":
            # Run all demos
            await demo_text_chat()
            await demo_streaming_chat()
            await demo_simple_api()
            await demo_quick_functions()
            await demo_configuration()
            print_integration_examples()

            # Ask user if they want to try voice demo
            try:
                choice = input(
                    "\nWould you like to try the voice chat demo? (y/N): "
                ).lower()
                if choice.startswith("y"):
                    await demo_voice_chat()
            except KeyboardInterrupt:
                print("\nSkipping voice demo...")

        elif args.demo == "text":
            await demo_text_chat()
        elif args.demo == "stream":
            await demo_streaming_chat()
        elif args.demo == "voice":
            await demo_voice_chat()
        elif args.demo == "simple":
            await demo_simple_api()
        elif args.demo == "quick":
            await demo_quick_functions()
        elif args.demo == "config":
            await demo_configuration()

        # Always show integration examples at the end
        if args.demo != "voice":  # Voice demo already shows examples
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
    print("\nTo integrate TalkSDK into your app:")
    print("  from gaia.talk.sdk import TalkSDK, TalkConfig")
    print("\nFor more examples, run: python app.py examples")


if __name__ == "__main__":
    asyncio.run(main())
