#!/usr/bin/env python3
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Gaia Chat SDK - Unified text chat integration with conversation history
"""

import logging
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from collections import deque

from gaia.logger import get_logger
from gaia.llm.llm_client import LLMClient
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME
from gaia.chat.prompts import Prompts


@dataclass
class ChatConfig:
    """Configuration for ChatSDK."""

    model: str = DEFAULT_MODEL_NAME
    max_tokens: int = 512
    system_prompt: Optional[str] = None
    max_history_length: int = 4  # Number of conversation pairs to keep
    show_stats: bool = False
    logging_level: str = "INFO"
    use_local_llm: bool = True
    assistant_name: str = "gaia"  # Name to use for the assistant in conversations


@dataclass
class ChatResponse:
    """Response from chat operations."""

    text: str
    history: Optional[List[str]] = None
    stats: Optional[Dict[str, Any]] = None
    is_complete: bool = True


class ChatSDK:
    """
    Gaia Chat SDK - Unified text chat integration with conversation history.

    This SDK provides a simple interface for integrating Gaia's text chat
    capabilities with conversation memory into applications.

    Example usage:
        ```python
        from gaia.chat.sdk import ChatSDK, ChatConfig

        # Create SDK instance
        config = ChatConfig(model=DEFAULT_MODEL_NAME, show_stats=True)
        chat = ChatSDK(config)

        # Single message
        response = await chat.send("Hello, how are you?")
        print(response.text)

        # Streaming chat
        async for chunk in chat.send_stream("Tell me a story"):
            print(chunk.text, end="", flush=True)

        # Get conversation history
        history = chat.get_history()
        ```
    """

    def __init__(self, config: Optional[ChatConfig] = None):
        """
        Initialize the ChatSDK.

        Args:
            config: Configuration options. If None, uses defaults.
        """
        self.config = config or ChatConfig()
        self.log = get_logger(__name__)
        self.log.setLevel(getattr(logging, self.config.logging_level))

        # Initialize LLM client
        self.llm_client = LLMClient(
            use_local=self.config.use_local_llm,
            system_prompt=None,  # We handle system prompts through Prompts class
        )

        # Store conversation history
        self.chat_history = deque(maxlen=self.config.max_history_length * 2)

        self.log.debug("ChatSDK initialized")

    def _format_history_for_context(self) -> str:
        """Format chat history for inclusion in LLM context using model-specific formatting."""
        history_list = list(self.chat_history)
        return Prompts.format_chat_history(
            self.config.model,
            history_list,
            self.config.assistant_name,
            self.config.system_prompt,
        )

    def send(self, message: str, **kwargs) -> ChatResponse:
        """
        Send a message and get a complete response with conversation history.

        Args:
            message: The message to send
            **kwargs: Additional arguments for LLM generation

        Returns:
            ChatResponse with the complete response and updated history
        """
        try:
            if not message.strip():
                raise ValueError("Message cannot be empty")

            # Add user message to history
            self.chat_history.append(f"user: {message.strip()}")

            # Prepare prompt with conversation context
            full_prompt = self._format_history_for_context()

            # Generate response
            generate_kwargs = dict(kwargs)
            if "max_tokens" not in generate_kwargs:
                generate_kwargs["max_tokens"] = self.config.max_tokens

            response = self.llm_client.generate(
                full_prompt, model=self.config.model, **generate_kwargs
            )

            # Add assistant message to history
            self.chat_history.append(f"{self.config.assistant_name}: {response}")

            # Prepare response data
            stats = None
            if self.config.show_stats:
                stats = self.get_stats()

            history = (
                list(self.chat_history)
                if kwargs.get("include_history", False)
                else None
            )

            return ChatResponse(
                text=response, history=history, stats=stats, is_complete=True
            )

        except Exception as e:
            self.log.error(f"Error in send: {e}")
            raise

    def send_stream(self, message: str, **kwargs):
        """
        Send a message and get a streaming response with conversation history.

        Args:
            message: The message to send
            **kwargs: Additional arguments for LLM generation

        Yields:
            ChatResponse chunks as they arrive
        """
        try:
            if not message.strip():
                raise ValueError("Message cannot be empty")

            # Add user message to history
            self.chat_history.append(f"user: {message.strip()}")

            # Prepare prompt with conversation context
            full_prompt = self._format_history_for_context()

            # Generate streaming response
            generate_kwargs = dict(kwargs)
            if "max_tokens" not in generate_kwargs:
                generate_kwargs["max_tokens"] = self.config.max_tokens

            full_response = ""
            for chunk in self.llm_client.generate(
                full_prompt, model=self.config.model, stream=True, **generate_kwargs
            ):
                full_response += chunk
                yield ChatResponse(text=chunk, is_complete=False)

            # Add complete assistant message to history
            self.chat_history.append(f"{self.config.assistant_name}: {full_response}")

            # Send final response with stats and history if requested
            stats = None
            if self.config.show_stats:
                stats = self.get_stats()

            history = (
                list(self.chat_history)
                if kwargs.get("include_history", False)
                else None
            )

            yield ChatResponse(text="", history=history, stats=stats, is_complete=True)

        except Exception as e:
            self.log.error(f"Error in send_stream: {e}")
            raise

    def get_history(self) -> List[str]:
        """
        Get the current conversation history.

        Returns:
            List of conversation entries in "role: message" format
        """
        return list(self.chat_history)

    def clear_history(self) -> None:
        """Clear the conversation history."""
        self.chat_history.clear()
        self.log.debug("Chat history cleared")

    def get_formatted_history(self) -> List[Dict[str, str]]:
        """
        Get conversation history in structured format.

        Returns:
            List of dictionaries with 'role' and 'message' keys
        """
        formatted = []
        assistant_prefix = f"{self.config.assistant_name}: "

        for entry in self.chat_history:
            if entry.startswith("user: "):
                role, message = "user", entry[6:]
                formatted.append({"role": role, "message": message})
            elif entry.startswith(assistant_prefix):
                role, message = (
                    self.config.assistant_name,
                    entry[len(assistant_prefix) :],
                )
                formatted.append({"role": role, "message": message})
            elif ": " in entry:
                # Fallback for any other format
                role, message = entry.split(": ", 1)
                formatted.append({"role": role, "message": message})
            else:
                formatted.append({"role": "unknown", "message": entry})
        return formatted

    def get_stats(self) -> Dict[str, Any]:
        """
        Get performance statistics.

        Returns:
            Dictionary of performance stats
        """
        try:
            return self.llm_client.get_performance_stats() or {}
        except Exception as e:
            self.log.warning(f"Failed to get stats: {e}")
            return {}

    def display_stats(self, stats: Optional[Dict[str, Any]] = None) -> None:
        """
        Display performance statistics in a formatted way.

        Args:
            stats: Optional stats dictionary. If None, gets current stats.
        """
        if stats is None:
            stats = self.get_stats()

        if stats:
            print("\n" + "=" * 30)
            print("Performance Statistics:")
            print("=" * 30)
            for key, value in stats.items():
                if isinstance(value, float):
                    if "time" in key.lower():
                        print(f"  {key}: {value:.3f}s")
                    elif "tokens_per_second" in key.lower():
                        print(f"  {key}: {value:.2f} tokens/s")
                    else:
                        print(f"  {key}: {value:.4f}")
                elif isinstance(value, int):
                    if "tokens" in key.lower():
                        print(f"  {key}: {value:,} tokens")
                    else:
                        print(f"  {key}: {value}")
                else:
                    print(f"  {key}: {value}")
            print("=" * 30)
        else:
            print("No statistics available.")

    async def start_interactive_session(self) -> None:
        """
        Start an interactive chat session with conversation history.

        This provides a full CLI-style interactive experience with commands
        for managing conversation history and viewing statistics.
        """
        print("=" * 50)
        print("Interactive Chat Session Started")
        print(f"Using model: {self.config.model}")
        print("Type 'quit', 'exit', or 'bye' to end the conversation")
        print("Commands:")
        print("  /clear    - clear conversation history")
        print("  /history  - show conversation history")
        print("  /stats    - show performance statistics")
        print("  /help     - show this help message")
        print("=" * 50)

        while True:
            try:
                user_input = input("\nYou: ").strip()

                if user_input.lower() in ["quit", "exit", "bye"]:
                    print("\nGoodbye!")
                    break
                elif user_input.lower() == "/clear":
                    self.clear_history()
                    print("Conversation history cleared.")
                    continue
                elif user_input.lower() == "/history":
                    history = self.get_formatted_history()
                    if not history:
                        print("No conversation history.")
                    else:
                        print("\n" + "=" * 30)
                        print("Conversation History:")
                        print("=" * 30)
                        for entry in history:
                            print(f"{entry['role'].title()}: {entry['message']}")
                        print("=" * 30)
                    continue
                elif user_input.lower() == "/stats":
                    self.display_stats()
                    continue
                elif user_input.lower() == "/help":
                    print("\n" + "=" * 40)
                    print("Available Commands:")
                    print("=" * 40)
                    print("  /clear    - clear conversation history")
                    print("  /history  - show conversation history")
                    print("  /stats    - show performance statistics")
                    print("  /help     - show this help message")
                    print("\nTo exit: type 'quit', 'exit', or 'bye'")
                    print("=" * 40)
                    continue
                elif not user_input:
                    print("Please enter a message.")
                    continue

                print(f"\n{self.config.assistant_name.title()}: ", end="", flush=True)

                # Generate and stream response
                for chunk in self.send_stream(user_input):
                    if not chunk.is_complete:
                        print(chunk.text, end="", flush=True)
                    else:
                        # Show stats if configured and available
                        if self.config.show_stats and chunk.stats:
                            self.display_stats(chunk.stats)
                print()  # Add newline after response

            except KeyboardInterrupt:
                print("\n\nChat interrupted. Goodbye!")
                break
            except Exception as e:
                print(f"\nError: {e}")
                raise

    def update_config(self, **kwargs) -> None:
        """
        Update configuration dynamically.

        Args:
            **kwargs: Configuration parameters to update
        """
        # Update our config
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)

        # Handle special cases
        if "max_history_length" in kwargs:
            # Create new deque with updated maxlen
            old_history = list(self.chat_history)
            new_maxlen = kwargs["max_history_length"] * 2
            self.chat_history = deque(old_history, maxlen=new_maxlen)

        if "system_prompt" in kwargs:
            # System prompt is handled through Prompts class, not directly
            pass

        if "assistant_name" in kwargs:
            # Assistant name change affects history display but not underlying storage
            # since we dynamically parse the history based on current assistant_name
            pass

    @property
    def history_length(self) -> int:
        """Get the current number of conversation entries."""
        return len(self.chat_history)

    @property
    def conversation_pairs(self) -> int:
        """Get the number of conversation pairs (user + assistant)."""
        return len(self.chat_history) // 2


class SimpleChat:
    """
    Ultra-simple interface for quick chat integration.

    Example usage:
        ```python
        from gaia.chat.sdk import SimpleChat

        chat = SimpleChat()

        # Simple question-answer
        response = await chat.ask("What's the weather like?")
        print(response)

        # Chat with memory
        response1 = await chat.ask("My name is John")
        response2 = await chat.ask("What's my name?")  # Remembers previous context
        ```
    """

    def __init__(
        self,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        assistant_name: Optional[str] = None,
    ):
        """
        Initialize SimpleChat with minimal configuration.

        Args:
            system_prompt: Optional system prompt for the AI
            model: Model to use (defaults to DEFAULT_MODEL_NAME)
            assistant_name: Name to use for the assistant (defaults to "assistant")
        """
        config = ChatConfig(
            model=model or DEFAULT_MODEL_NAME,
            system_prompt=system_prompt,
            assistant_name=assistant_name or "gaia",
            show_stats=False,
            logging_level="WARNING",  # Minimal logging
        )
        self._sdk = ChatSDK(config)

    def ask(self, question: str) -> str:
        """
        Ask a question and get a text response with conversation memory.

        Args:
            question: The question to ask

        Returns:
            The AI's response as a string
        """
        response = self._sdk.send(question)
        return response.text

    def ask_stream(self, question: str):
        """
        Ask a question and get a streaming response with conversation memory.

        Args:
            question: The question to ask

        Yields:
            Response chunks as they arrive
        """
        for chunk in self._sdk.send_stream(question):
            if not chunk.is_complete:
                yield chunk.text

    def clear_memory(self) -> None:
        """Clear the conversation memory."""
        self._sdk.clear_history()

    def get_conversation(self) -> List[Dict[str, str]]:
        """Get the conversation history in a readable format."""
        return self._sdk.get_formatted_history()


class ChatSession:
    """
    Session-based chat interface for managing multiple separate conversations.

    Example usage:
        ```python
        from gaia.chat.sdk import ChatSession

        # Create session manager
        sessions = ChatSession()

        # Create different chat sessions
        work_chat = sessions.create_session("work", system_prompt="You are a professional assistant")
        personal_chat = sessions.create_session("personal", system_prompt="You are a friendly companion")

        # Chat in different contexts
        work_response = await work_chat.ask("Draft an email to my team")
        personal_response = await personal_chat.ask("What's a good recipe for dinner?")
        ```
    """

    def __init__(self, default_config: Optional[ChatConfig] = None):
        """Initialize the session manager."""
        self.default_config = default_config or ChatConfig()
        self.sessions: Dict[str, ChatSDK] = {}
        self.log = get_logger(__name__)

    def create_session(
        self, session_id: str, config: Optional[ChatConfig] = None, **config_kwargs
    ) -> ChatSDK:
        """
        Create a new chat session.

        Args:
            session_id: Unique identifier for the session
            config: Optional configuration (uses default if not provided)
            **config_kwargs: Configuration parameters to override

        Returns:
            ChatSDK instance for the session
        """
        if config is None:
            # Create config from defaults with overrides
            config = ChatConfig(
                model=config_kwargs.get("model", self.default_config.model),
                max_tokens=config_kwargs.get(
                    "max_tokens", self.default_config.max_tokens
                ),
                system_prompt=config_kwargs.get(
                    "system_prompt", self.default_config.system_prompt
                ),
                max_history_length=config_kwargs.get(
                    "max_history_length", self.default_config.max_history_length
                ),
                show_stats=config_kwargs.get(
                    "show_stats", self.default_config.show_stats
                ),
                logging_level=config_kwargs.get(
                    "logging_level", self.default_config.logging_level
                ),
                use_local_llm=config_kwargs.get(
                    "use_local_llm", self.default_config.use_local_llm
                ),
                assistant_name=config_kwargs.get(
                    "assistant_name", self.default_config.assistant_name
                ),
            )

        session = ChatSDK(config)
        self.sessions[session_id] = session
        self.log.debug(f"Created chat session: {session_id}")
        return session

    def get_session(self, session_id: str) -> Optional[ChatSDK]:
        """Get an existing session by ID."""
        return self.sessions.get(session_id)

    def delete_session(self, session_id: str) -> bool:
        """Delete a session."""
        if session_id in self.sessions:
            del self.sessions[session_id]
            self.log.debug(f"Deleted chat session: {session_id}")
            return True
        return False

    def list_sessions(self) -> List[str]:
        """List all active session IDs."""
        return list(self.sessions.keys())

    def clear_all_sessions(self) -> None:
        """Clear all sessions."""
        self.sessions.clear()
        self.log.debug("Cleared all chat sessions")


# Convenience functions for one-off usage
def quick_chat(
    message: str,
    system_prompt: Optional[str] = None,
    model: Optional[str] = None,
    assistant_name: Optional[str] = None,
) -> str:
    """
    Quick one-off text chat without conversation memory.

    Args:
        message: Message to send
        system_prompt: Optional system prompt
        model: Optional model to use
        assistant_name: Name to use for the assistant

    Returns:
        AI response
    """
    config = ChatConfig(
        model=model or DEFAULT_MODEL_NAME,
        system_prompt=system_prompt,
        assistant_name=assistant_name or "gaia",
        show_stats=False,
        logging_level="WARNING",
        max_history_length=2,  # Small history for quick chat
    )
    sdk = ChatSDK(config)
    response = sdk.send(message)
    return response.text


def quick_chat_with_memory(
    messages: List[str],
    system_prompt: Optional[str] = None,
    model: Optional[str] = None,
    assistant_name: Optional[str] = None,
) -> List[str]:
    """
    Quick multi-turn chat with conversation memory.

    Args:
        messages: List of messages to send sequentially
        system_prompt: Optional system prompt
        model: Optional model to use
        assistant_name: Name to use for the assistant

    Returns:
        List of AI responses
    """
    config = ChatConfig(
        model=model or DEFAULT_MODEL_NAME,
        system_prompt=system_prompt,
        assistant_name=assistant_name or "gaia",
        show_stats=False,
        logging_level="WARNING",
    )
    sdk = ChatSDK(config)

    responses = []
    for message in messages:
        response = sdk.send(message)
        responses.append(response.text)

    return responses
