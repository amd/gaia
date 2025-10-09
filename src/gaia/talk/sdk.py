#!/usr/bin/env python3
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Gaia Talk SDK - Unified voice and text chat integration
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, AsyncGenerator, Callable, Dict, Optional

from gaia.audio.audio_client import AudioClient
from gaia.chat.sdk import ChatConfig, ChatSDK
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME
from gaia.logger import get_logger


class TalkMode(Enum):
    """Talk mode options."""

    TEXT_ONLY = "text_only"
    VOICE_ONLY = "voice_only"
    VOICE_AND_TEXT = "voice_and_text"


@dataclass
class TalkConfig:
    """Configuration for TalkSDK."""

    # Voice-specific settings
    whisper_model_size: str = "base"
    audio_device_index: Optional[int] = None  # Use default input device
    silence_threshold: float = 0.5
    enable_tts: bool = True
    mode: TalkMode = TalkMode.VOICE_AND_TEXT

    # Chat settings (from ChatConfig)
    model: str = DEFAULT_MODEL_NAME
    max_tokens: int = 512
    system_prompt: Optional[str] = None
    max_history_length: int = 4  # Number of conversation pairs to keep
    assistant_name: str = "gaia"

    # General settings
    use_claude: bool = False  # Use Claude API
    use_chatgpt: bool = False  # Use ChatGPT/OpenAI API
    show_stats: bool = False
    logging_level: str = "INFO"


@dataclass
class TalkResponse:
    """Response from talk operations."""

    text: str
    stats: Optional[Dict[str, Any]] = None
    is_complete: bool = True


class TalkSDK:
    """
    Gaia Talk SDK - Unified voice and text chat integration.

    This SDK provides a simple interface for integrating Gaia's voice and text
    chat capabilities into applications.

    Example usage:
        ```python
        from gaia.talk.sdk import TalkSDK, TalkConfig

        # Create SDK instance
        config = TalkConfig(enable_tts=True, show_stats=True)
        talk = TalkSDK(config)

        # Text chat
        response = await talk.chat("Hello, how are you?")
        print(response.text)

        # Streaming chat
        async for chunk in talk.chat_stream("Tell me a story"):
            print(chunk.text, end="", flush=True)

        # Voice chat with callback
        def on_voice_input(text):
            print(f"User said: {text}")

        await talk.start_voice_session(on_voice_input)
        ```
    """

    def __init__(self, config: Optional[TalkConfig] = None):
        """
        Initialize the TalkSDK.

        Args:
            config: Configuration options. If None, uses defaults.
        """
        self.config = config or TalkConfig()
        self.log = get_logger(__name__)
        self.log.setLevel(getattr(logging, self.config.logging_level))

        # Initialize ChatSDK for text generation with conversation history
        chat_config = ChatConfig(
            model=self.config.model,
            max_tokens=self.config.max_tokens,
            system_prompt=self.config.system_prompt,
            max_history_length=self.config.max_history_length,
            assistant_name=self.config.assistant_name,
            show_stats=self.config.show_stats,
            logging_level=self.config.logging_level,
            use_claude=self.config.use_claude,
            use_chatgpt=self.config.use_chatgpt,
        )
        self.chat_sdk = ChatSDK(chat_config)

        # Initialize AudioClient with configuration (for voice features)
        self.audio_client = AudioClient(
            whisper_model_size=self.config.whisper_model_size,
            audio_device_index=self.config.audio_device_index,
            silence_threshold=self.config.silence_threshold,
            enable_tts=self.config.enable_tts,
            logging_level=self.config.logging_level,
            use_claude=self.config.use_claude,
            use_chatgpt=self.config.use_chatgpt,
            system_prompt=self.config.system_prompt,
        )

        self.show_stats = self.config.show_stats
        self._voice_session_active = False

        self.log.info("TalkSDK initialized with ChatSDK integration")

    async def chat(self, message: str) -> TalkResponse:
        """
        Send a text message and get a complete response.

        Args:
            message: The message to send

        Returns:
            TalkResponse with the complete response
        """
        try:
            # Use ChatSDK for text generation (with conversation history)
            chat_response = self.chat_sdk.send(message)

            stats = None
            if self.show_stats:
                stats = chat_response.stats or self.get_stats()

            return TalkResponse(text=chat_response.text, stats=stats, is_complete=True)

        except Exception as e:
            self.log.error(f"Error in chat: {e}")
            raise

    async def chat_stream(self, message: str) -> AsyncGenerator[TalkResponse, None]:
        """
        Send a text message and get a streaming response.

        Args:
            message: The message to send

        Yields:
            TalkResponse chunks as they arrive
        """
        try:
            # Use ChatSDK for streaming text generation (with conversation history)
            for chat_chunk in self.chat_sdk.send_stream(message):
                if not chat_chunk.is_complete:
                    yield TalkResponse(text=chat_chunk.text, is_complete=False)
                else:
                    # Final chunk with stats
                    stats = chat_chunk.stats if self.show_stats else None
                    yield TalkResponse(text="", stats=stats, is_complete=True)

        except Exception as e:
            self.log.error(f"Error in chat_stream: {e}")
            raise

    async def process_voice_input(self, text: str) -> TalkResponse:
        """
        Process voice input text through the complete voice pipeline.

        This includes TTS output if enabled.

        Args:
            text: The transcribed voice input

        Returns:
            TalkResponse with the processed response
        """
        try:
            # Use ChatSDK to generate response (with conversation history)
            chat_response = self.chat_sdk.send(text)

            # If TTS is enabled, speak the response
            if self.config.enable_tts and getattr(self.audio_client, "tts", None):
                await self.audio_client.speak_text(chat_response.text)

            stats = None
            if self.show_stats:
                stats = chat_response.stats or self.get_stats()

            return TalkResponse(text=chat_response.text, stats=stats, is_complete=True)

        except Exception as e:
            self.log.error(f"Error processing voice input: {e}")
            raise

    async def start_voice_session(
        self,
        on_voice_input: Optional[Callable[[str], None]] = None,
    ) -> None:
        """
        Start an interactive voice session.

        Args:
            on_voice_input: Optional callback called when voice input is detected
        """
        try:
            self._voice_session_active = True

            # Initialize TTS if enabled
            self.audio_client.initialize_tts()

            # Create voice processor that uses ChatSDK for responses
            async def voice_processor(text: str):
                # Call user callback if provided
                if on_voice_input:
                    on_voice_input(text)

                # Use ChatSDK to generate response (with conversation history)
                chat_response = self.chat_sdk.send(text)

                # If TTS is enabled, speak the response
                if self.config.enable_tts and getattr(self.audio_client, "tts", None):
                    await self.audio_client.speak_text(chat_response.text)

                # Print the response for user feedback
                print(f"{self.config.assistant_name.title()}: {chat_response.text}")

                # Show stats if enabled
                if self.show_stats and chat_response.stats:
                    print(f"Stats: {chat_response.stats}")

            # Start voice chat session with our processor
            await self.audio_client.start_voice_chat(voice_processor)

        except KeyboardInterrupt:
            self.log.info("Voice session interrupted by user")
        except Exception as e:
            self.log.error(f"Error in voice session: {e}")
            raise
        finally:
            self._voice_session_active = False
            self.log.info("Voice chat session ended")

    async def halt_generation(self) -> None:
        """Halt the current LLM generation."""
        try:
            await self.audio_client.halt_generation()
        except Exception as e:
            self.log.error(f"Error halting generation: {e}")
            raise

    def get_stats(self) -> Dict[str, Any]:
        """
        Get performance statistics.

        Returns:
            Dictionary of performance stats
        """
        try:
            # Get stats from ChatSDK instead of directly from LLMClient
            return self.chat_sdk.get_stats()
        except Exception as e:
            self.log.warning(f"Failed to get stats: {e}")
            return {}

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

        # Update show_stats
        if "show_stats" in kwargs:
            self.show_stats = kwargs["show_stats"]

        # Update AudioClient configuration
        if "silence_threshold" in kwargs:
            self.audio_client.silence_threshold = kwargs["silence_threshold"]

        # Update ChatSDK configuration
        chat_updates = {}
        if "system_prompt" in kwargs:
            chat_updates["system_prompt"] = kwargs["system_prompt"]
            # Also update AudioClient's system prompt for consistency
            self.audio_client.llm_client.system_prompt = kwargs["system_prompt"]
        if "max_tokens" in kwargs:
            chat_updates["max_tokens"] = kwargs["max_tokens"]
        if "max_history_length" in kwargs:
            chat_updates["max_history_length"] = kwargs["max_history_length"]
        if "assistant_name" in kwargs:
            chat_updates["assistant_name"] = kwargs["assistant_name"]

        if chat_updates:
            self.chat_sdk.update_config(**chat_updates)

    def clear_history(self) -> None:
        """Clear the conversation history."""
        self.chat_sdk.clear_history()
        self.log.debug("Conversation history cleared")

    def get_history(self) -> list:
        """Get the current conversation history."""
        return self.chat_sdk.get_history()

    def get_formatted_history(self) -> list:
        """Get the conversation history in structured format."""
        return self.chat_sdk.get_formatted_history()

    @property
    def is_voice_session_active(self) -> bool:
        """Check if a voice session is currently active."""
        return self._voice_session_active

    @property
    def audio_devices(self) -> list:
        """Get list of available audio input devices."""
        try:
            from gaia.audio.audio_recorder import AudioRecorder

            recorder = AudioRecorder()
            return recorder.list_audio_devices()
        except Exception as e:
            self.log.error(f"Error listing audio devices: {e}")
            return []


class SimpleTalk:
    """
    Ultra-simple interface for quick integration.

    Example usage:
        ```python
        from gaia.talk.sdk import SimpleTalk

        talk = SimpleTalk()

        # Simple text chat
        response = await talk.ask("What's the weather like?")
        print(response)

        # Simple voice chat
        await talk.voice_chat()  # Starts interactive session
        ```
    """

    def __init__(
        self,
        system_prompt: Optional[str] = None,
        enable_tts: bool = True,
        assistant_name: str = "gaia",
    ):
        """
        Initialize SimpleTalk with minimal configuration.

        Args:
            system_prompt: Optional system prompt for the AI
            enable_tts: Whether to enable text-to-speech
            assistant_name: Name to use for the assistant
        """
        config = TalkConfig(
            system_prompt=system_prompt,
            enable_tts=enable_tts,
            assistant_name=assistant_name,
            show_stats=False,
            logging_level="WARNING",  # Minimal logging
        )
        self._sdk = TalkSDK(config)

    async def ask(self, question: str) -> str:
        """
        Ask a question and get a text response.

        Args:
            question: The question to ask

        Returns:
            The AI's response as a string
        """
        response = await self._sdk.chat(question)
        return response.text

    async def ask_stream(self, question: str):
        """
        Ask a question and get a streaming response.

        Args:
            question: The question to ask

        Yields:
            Response chunks as they arrive
        """
        async for chunk in self._sdk.chat_stream(question):
            if not chunk.is_complete:
                yield chunk.text

    async def voice_chat(self) -> None:
        """Start an interactive voice chat session."""
        print("Starting voice chat... Say 'stop' to quit or press Ctrl+C")

        def on_voice_input(text: str):
            print(f"You: {text}")

        await self._sdk.start_voice_session(on_voice_input)

    def clear_memory(self) -> None:
        """Clear the conversation memory."""
        self._sdk.clear_history()

    def get_conversation(self) -> list:
        """Get the conversation history in a readable format."""
        return self._sdk.get_formatted_history()


# Convenience functions for one-off usage
async def quick_chat(
    message: str, system_prompt: Optional[str] = None, assistant_name: str = "gaia"
) -> str:
    """
    Quick one-off text chat with conversation memory.

    Args:
        message: Message to send
        system_prompt: Optional system prompt
        assistant_name: Name to use for the assistant

    Returns:
        AI response
    """
    config = TalkConfig(
        system_prompt=system_prompt,
        assistant_name=assistant_name,
        enable_tts=False,
        logging_level="WARNING",
        max_history_length=2,  # Small history for quick chat
    )
    sdk = TalkSDK(config)
    response = await sdk.chat(message)
    return response.text


async def quick_voice_chat(
    system_prompt: Optional[str] = None, assistant_name: str = "gaia"
) -> None:
    """
    Quick one-off voice chat session with conversation memory.

    Args:
        system_prompt: Optional system prompt
        assistant_name: Name to use for the assistant
    """
    simple = SimpleTalk(system_prompt=system_prompt, assistant_name=assistant_name)
    await simple.voice_chat()
