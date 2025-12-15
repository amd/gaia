#!/usr/bin/env python3
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Gaia Chat SDK - Unified text chat integration with conversation history
"""

import json
import logging
from collections import deque
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from gaia.chat.prompts import Prompts
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME
from gaia.llm.llm_client import LLMClient
from gaia.logger import get_logger


@dataclass
class ChatConfig:
    """Configuration for ChatSDK."""

    model: str = DEFAULT_MODEL_NAME
    max_tokens: int = 512
    system_prompt: Optional[str] = None
    max_history_length: int = 4  # Number of conversation pairs to keep
    show_stats: bool = False
    logging_level: str = "INFO"
    use_claude: bool = False  # Use Claude API
    use_chatgpt: bool = False  # Use ChatGPT/OpenAI API
    use_local_llm: bool = (
        True  # Use local LLM (computed as not use_claude and not use_chatgpt if not explicitly set)
    )
    claude_model: str = "claude-sonnet-4-20250514"  # Claude model when use_claude=True
    base_url: str = "http://localhost:8000/api/v1"  # Lemonade server base URL
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

        # Validate that both providers aren't specified
        if self.config.use_claude and self.config.use_chatgpt:
            raise ValueError(
                "Cannot specify both use_claude and use_chatgpt. Please choose one."
            )

        # Initialize LLM client - it will compute use_local automatically
        self.llm_client = LLMClient(
            use_claude=self.config.use_claude,
            use_openai=self.config.use_chatgpt,
            claude_model=self.config.claude_model,
            base_url=self.config.base_url,
            system_prompt=None,  # We handle system prompts through Prompts class
        )

        # Store conversation history
        self.chat_history = deque(maxlen=self.config.max_history_length * 2)

        # RAG support
        self.rag = None
        self.rag_enabled = False

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

    def _normalize_message_content(self, content: Any) -> str:
        """
        Convert message content into a string for prompt construction, handling structured payloads.
        """
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for entry in content:
                if isinstance(entry, dict):
                    if entry.get("type") == "text":
                        parts.append(entry.get("text", ""))
                    else:
                        parts.append(json.dumps(entry))
                else:
                    parts.append(str(entry))
            return "\n".join(part for part in parts if part)
        if isinstance(content, dict):
            return json.dumps(content)
        return str(content)

    def send_messages(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
        **kwargs,
    ) -> ChatResponse:
        """
        Send a full conversation history and get a response.

        Args:
            messages: List of message dicts with 'role' and 'content' keys
            system_prompt: Optional system prompt to use (overrides config)
            **kwargs: Additional arguments for LLM generation

        Returns:
            ChatResponse with the complete response
        """
        try:
            # Convert messages to chat history format
            chat_history = []

            for msg in messages:
                role = msg.get("role", "")
                content = self._normalize_message_content(msg.get("content", ""))

                if role == "user":
                    chat_history.append(f"user: {content}")
                elif role == "assistant":
                    chat_history.append(f"assistant: {content}")
                elif role == "tool":
                    tool_name = msg.get("name", "tool")
                    chat_history.append(f"assistant: [tool:{tool_name}] {content}")
                # Skip system messages since they're passed separately

            # Use provided system prompt or fall back to config default
            effective_system_prompt = system_prompt or self.config.system_prompt

            # Format according to model type
            formatted_prompt = Prompts.format_chat_history(
                model=self.config.model,
                chat_history=chat_history,
                assistant_name="assistant",
                system_prompt=effective_system_prompt,
            )

            # Debug logging
            self.log.debug(f"Formatted prompt length: {len(formatted_prompt)} chars")
            self.log.debug(
                f"System prompt used: {effective_system_prompt[:100] if effective_system_prompt else 'None'}..."
            )

            # Set appropriate stop tokens based on model
            model_lower = self.config.model.lower() if self.config.model else ""
            if "qwen" in model_lower:
                kwargs.setdefault("stop", ["<|im_end|>", "<|im_start|>"])
            elif "llama" in model_lower:
                kwargs.setdefault("stop", ["<|eot_id|>", "<|start_header_id|>"])

            # Use generate with formatted prompt
            response = self.llm_client.generate(
                prompt=formatted_prompt,
                model=self.config.model,
                stream=False,
                **kwargs,
            )

            # Prepare response data
            stats = None
            if self.config.show_stats:
                stats = self.get_stats()

            return ChatResponse(text=response, stats=stats, is_complete=True)

        except ConnectionError as e:
            # Re-raise connection errors with additional context
            self.log.error(f"LLM connection error in send_messages: {e}")
            raise ConnectionError(f"Failed to connect to LLM server: {e}") from e
        except Exception as e:
            self.log.error(f"Error in send_messages: {e}")
            raise

    def send_messages_stream(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
        **kwargs,
    ):
        """
        Send a full conversation history and get a streaming response.

        Args:
            messages: List of message dicts with 'role' and 'content' keys
            system_prompt: Optional system prompt to use (overrides config)
            **kwargs: Additional arguments for LLM generation

        Yields:
            ChatResponse chunks as they arrive
        """
        try:
            # Convert messages to chat history format
            chat_history = []

            for msg in messages:
                role = msg.get("role", "")
                content = self._normalize_message_content(msg.get("content", ""))

                if role == "user":
                    chat_history.append(f"user: {content}")
                elif role == "assistant":
                    chat_history.append(f"assistant: {content}")
                elif role == "tool":
                    tool_name = msg.get("name", "tool")
                    chat_history.append(f"assistant: [tool:{tool_name}] {content}")
                # Skip system messages since they're passed separately

            # Use provided system prompt or fall back to config default
            effective_system_prompt = system_prompt or self.config.system_prompt

            # Format according to model type
            formatted_prompt = Prompts.format_chat_history(
                model=self.config.model,
                chat_history=chat_history,
                assistant_name="assistant",
                system_prompt=effective_system_prompt,
            )

            # Debug logging
            self.log.debug(f"Formatted prompt length: {len(formatted_prompt)} chars")
            self.log.debug(
                f"System prompt used: {effective_system_prompt[:100] if effective_system_prompt else 'None'}..."
            )

            # Set appropriate stop tokens based on model
            model_lower = self.config.model.lower() if self.config.model else ""
            if "qwen" in model_lower:
                kwargs.setdefault("stop", ["<|im_end|>", "<|im_start|>"])
            elif "llama" in model_lower:
                kwargs.setdefault("stop", ["<|eot_id|>", "<|start_header_id|>"])

            # Use generate with formatted prompt for streaming
            full_response = ""
            for chunk in self.llm_client.generate(
                prompt=formatted_prompt, model=self.config.model, stream=True, **kwargs
            ):
                full_response += chunk
                yield ChatResponse(text=chunk, is_complete=False)

            # Send final response with stats
            # Always get stats for token tracking (show_stats controls display, not collection)
            stats = self.get_stats()

            yield ChatResponse(text="", stats=stats, is_complete=True)

        except ConnectionError as e:
            # Re-raise connection errors with additional context
            self.log.error(f"LLM connection error in send_messages_stream: {e}")
            raise ConnectionError(
                f"Failed to connect to LLM server (streaming): {e}"
            ) from e
        except Exception as e:
            self.log.error(f"Error in send_messages_stream: {e}")
            raise

    def send(self, message: str, *, no_history: bool = False, **kwargs) -> ChatResponse:
        """
        Send a message and get a complete response with conversation history.

        Args:
            message: The message to send
            no_history: When True, bypass stored chat history and send only this prompt
            **kwargs: Additional arguments for LLM generation

        Returns:
            ChatResponse with the complete response and updated history
        """
        try:
            if not message.strip():
                raise ValueError("Message cannot be empty")

            # Enhance message with RAG context if enabled
            enhanced_message, _rag_metadata = self._enhance_with_rag(message.strip())

            if no_history:
                # Build a prompt using only the current enhanced message
                full_prompt = Prompts.format_chat_history(
                    model=self.config.model,
                    chat_history=[f"user: {enhanced_message}"],
                    assistant_name=self.config.assistant_name,
                    system_prompt=self.config.system_prompt,
                )
            else:
                # Add user message to history (use original message for history)
                self.chat_history.append(f"user: {message.strip()}")

                # Prepare prompt with conversation context (use enhanced message for LLM)
                # Temporarily replace the last message with enhanced version for formatting
                if self.rag_enabled and enhanced_message != message.strip():
                    # Save original and replace with enhanced version
                    original_last = self.chat_history.pop()
                    self.chat_history.append(f"user: {enhanced_message}")
                    full_prompt = self._format_history_for_context()
                    # Restore original for history
                    self.chat_history.pop()
                    self.chat_history.append(original_last)
                else:
                    full_prompt = self._format_history_for_context()

            # Generate response
            generate_kwargs = dict(kwargs)
            if "max_tokens" not in generate_kwargs:
                generate_kwargs["max_tokens"] = self.config.max_tokens

            # Note: Retry logic is now handled at the LLM client level
            response = self.llm_client.generate(
                full_prompt,
                model=self.config.model,
                **generate_kwargs,
            )

            # Add assistant message to history when tracking conversation
            if not no_history:
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

            # Enhance message with RAG context if enabled
            enhanced_message, _rag_metadata = self._enhance_with_rag(message.strip())

            # Add user message to history (use original message for history)
            self.chat_history.append(f"user: {message.strip()}")

            # Prepare prompt with conversation context (use enhanced message for LLM)
            # Temporarily replace the last message with enhanced version for formatting
            if self.rag_enabled and enhanced_message != message.strip():
                # Save original and replace with enhanced version
                original_last = self.chat_history.pop()
                self.chat_history.append(f"user: {enhanced_message}")
                full_prompt = self._format_history_for_context()
                # Restore original for history
                self.chat_history.pop()
                self.chat_history.append(original_last)
            else:
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

    def get_system_prompt(self) -> Optional[str]:
        """
        Get the current system prompt.

        Returns:
            Current system prompt or None if not set
        """
        return self.config.system_prompt

    def set_system_prompt(self, system_prompt: Optional[str]) -> None:
        """
        Set the system prompt for future conversations.

        Args:
            system_prompt: New system prompt to use, or None to clear it
        """
        self.config.system_prompt = system_prompt
        self.log.debug(
            f"System prompt updated: {system_prompt[:100] if system_prompt else 'None'}..."
        )

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

    def enable_rag(self, documents: Optional[List[str]] = None, **rag_kwargs):
        """
        Enable RAG (Retrieval-Augmented Generation) for document-based chat.

        Args:
            documents: List of PDF file paths to index
            **rag_kwargs: Additional RAG configuration options
        """
        try:
            from gaia.rag.sdk import RAGSDK, RAGConfig
        except ImportError:
            raise ImportError(
                "RAG dependencies not installed. Install with: pip install .[rag]"
            )

        # Create RAG config matching chat config
        rag_config = RAGConfig(
            model=self.config.model,
            show_stats=self.config.show_stats,
            use_local_llm=self.config.use_local_llm,
            **rag_kwargs,
        )

        self.rag = RAGSDK(rag_config)
        self.rag_enabled = True

        # Index documents if provided
        if documents:
            for doc_path in documents:
                self.log.info(f"Indexing document: {doc_path}")
                result = self.rag.index_document(doc_path)

                if result:
                    self.log.info(f"Successfully indexed: {doc_path}")
                else:
                    self.log.warning(f"Failed to index document: {doc_path}")

        self.log.info(
            f"RAG enabled with {len(documents) if documents else 0} documents"
        )

    def disable_rag(self):
        """Disable RAG functionality."""
        self.rag = None
        self.rag_enabled = False
        self.log.info("RAG disabled")

    def add_document(self, document_path: str) -> bool:
        """
        Add a document to the RAG index.

        Args:
            document_path: Path to PDF file to index

        Returns:
            True if indexing succeeded
        """
        if not self.rag_enabled or not self.rag:
            raise ValueError("RAG not enabled. Call enable_rag() first.")

        return self.rag.index_document(document_path)

    def _estimate_tokens(self, text: str) -> int:
        """
        Estimate the number of tokens in text.
        Uses rough approximation of 4 characters per token.

        Args:
            text: The text to estimate tokens for

        Returns:
            Estimated token count
        """
        # Rough approximation: ~4 characters per token for English text
        # This is conservative to avoid overrunning context
        return len(text) // 4

    def summarize_conversation_history(self, max_history_tokens: int) -> Optional[str]:
        """
        Summarize conversation history when it exceeds the token budget.

        Args:
            max_history_tokens: Maximum allowed tokens for stored history

        Returns:
            The generated summary (when summarization occurred) or None
        """
        if max_history_tokens <= 0:
            raise ValueError("max_history_tokens must be positive")

        history_entries = list(self.chat_history)
        if not history_entries:
            return None

        history_text = "\n".join(history_entries)
        history_tokens = self._estimate_tokens(history_text)

        if history_tokens <= max_history_tokens:
            print(
                "History tokens are less than max history tokens, so no summarization is needed"
            )
            return None

        print(
            "History tokens are greater than max history tokens, so summarization is needed"
        )

        self.log.info(
            "Conversation history (~%d tokens) exceeds budget (%d). Summarizing...",
            history_tokens,
            max_history_tokens,
        )

        summary_prompt = (
            "Summarize the following conversation between a user and the GAIA web "
            "development agent. Preserve:\n"
            "- The app requirements and inferred schema/data models\n"
            "- Key implementation details already completed\n"
            "- Outstanding issues, validation failures, or TODOs (quote error/warning text verbatim)\n"
            "- Any constraints or preferences the user emphasized\n\n"
            "Write the summary in under 400 tokens, using concise paragraphs, and include the exact text of any warnings/errors so future fixes have full context."
        )
        full_prompt = (
            f"{summary_prompt}\n\nConversation History:\n{history_text}\n\nSummary:"
        )

        try:
            summary = self.llm_client.generate(
                full_prompt,
                model=self.config.model,
                max_tokens=min(self.config.max_tokens, 2048),
                timeout=1200,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.log.error("Failed to summarize conversation history: %s", exc)
            return None

        summary = summary.strip()
        if not summary:
            self.log.warning("Summarization returned empty content; keeping history.")
            return None

        self.chat_history.clear()
        self.chat_history.append(
            f"{self.config.assistant_name}: Conversation summary so far:\n{summary}"
        )
        return summary

    def _truncate_rag_context(self, context: str, max_tokens: int) -> str:
        """
        Truncate RAG context to fit within token budget.

        Args:
            context: The RAG context to truncate
            max_tokens: Maximum tokens allowed

        Returns:
            Truncated context with ellipsis if needed
        """
        estimated_tokens = self._estimate_tokens(context)

        if estimated_tokens <= max_tokens:
            return context

        # Calculate how many characters we can keep
        target_chars = max_tokens * 4  # Using same 4:1 ratio

        # Truncate and add ellipsis
        truncated = context[: target_chars - 20]  # Leave room for ellipsis
        truncated += "\n... [context truncated for length]"

        self.log.warning(
            f"RAG context truncated from ~{estimated_tokens} to ~{max_tokens} tokens"
        )
        return truncated

    def _enhance_with_rag(self, message: str) -> tuple:
        """
        Enhance user message with relevant document context using RAG.

        Args:
            message: Original user message

        Returns:
            Tuple of (enhanced_message, metadata_dict)
        """
        if not self.rag_enabled or not self.rag:
            return message, None

        try:
            # Query RAG for relevant context with metadata
            rag_response = self.rag.query(message, include_metadata=True)

            if rag_response.chunks:
                # Build context with source information
                context_parts = []
                if rag_response.chunk_metadata:
                    for i, (chunk, metadata) in enumerate(
                        zip(rag_response.chunks, rag_response.chunk_metadata)
                    ):
                        context_parts.append(
                            f"Context {i+1} (from {metadata['source_file']}, relevance: {metadata['relevance_score']:.2f}):\n{chunk}"
                        )
                else:
                    context_parts = [
                        f"Context {i+1}:\n{chunk}"
                        for i, chunk in enumerate(rag_response.chunks)
                    ]

                context = "\n\n".join(context_parts)

                # Check token limits
                message_tokens = self._estimate_tokens(message)
                template_tokens = 150  # Template text overhead
                response_tokens = self.config.max_tokens
                history_tokens = self._estimate_tokens(str(self.chat_history))

                # Conservative context size for models
                model_context_size = 32768
                available_for_rag = (
                    model_context_size
                    - message_tokens
                    - template_tokens
                    - response_tokens
                    - history_tokens
                )

                # Ensure minimum RAG context
                if available_for_rag < 500:
                    self.log.warning(
                        f"Limited space for RAG context: {available_for_rag} tokens"
                    )
                    available_for_rag = 500

                # Truncate context if needed
                context = self._truncate_rag_context(context, available_for_rag)

                # Build enhanced message
                enhanced_message = f"""Based on the provided documents, please answer the following question. Use the context below to inform your response.

Context from documents:
{context}

User question: {message}

Note: When citing information, please mention which context number it came from."""

                # Prepare metadata for return
                metadata = {
                    "rag_used": True,
                    "chunks_retrieved": len(rag_response.chunks),
                    "estimated_context_tokens": self._estimate_tokens(context),
                    "available_tokens": available_for_rag,
                    "context_truncated": (
                        len(context) < sum(len(c) for c in rag_response.chunks)
                        if rag_response.chunks
                        else False
                    ),
                }

                # Add query metadata if available
                if rag_response.query_metadata:
                    metadata["query_metadata"] = rag_response.query_metadata

                self.log.debug(
                    f"Enhanced message with {len(rag_response.chunks)} chunks from "
                    f"{len(set(rag_response.source_files)) if rag_response.source_files else 0} documents, "
                    f"~{metadata['estimated_context_tokens']} context tokens"
                )
                return enhanced_message, metadata
            else:
                self.log.debug("No relevant document context found")
                return message, {"rag_used": True, "chunks_retrieved": 0}

        except Exception as e:
            self.log.warning(
                f"RAG enhancement failed: {e}, falling back to direct query"
            )
            return message, {"rag_used": False, "error": str(e)}


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
                use_claude=config_kwargs.get(
                    "use_claude", self.default_config.use_claude
                ),
                use_chatgpt=config_kwargs.get(
                    "use_chatgpt", self.default_config.use_chatgpt
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
