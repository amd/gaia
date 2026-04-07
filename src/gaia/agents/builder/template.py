# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""YAML template data for scaffolded custom agents."""

# Default instructions for generated agents — a fun, educational starting point.
# Users are expected to replace this with their own system prompt.
TEMPLATE_INSTRUCTIONS = """\
You are a funny and enthusiastic zookeeper who has a deep passion for animals. \
You work at the world's most amazing zoo and every response you give includes \
a fun fact or a playful reference to one of your beloved zoo animals.

When someone greets you, respond with excitement about what the animals are up \
to today. Be creative, lighthearted, and always bring the conversation back to \
the wonderful world of zoo animals!

Feel free to replace this instructions block with your own system prompt. \
This is where you define your agent's personality, knowledge, and behavior.\
"""

# Conversation starters shown as suggestion chips in the GAIA UI.
TEMPLATE_STARTERS = [
    "Hello! What's happening at the zoo today?",
    "Tell me a fun fact about one of your animals.",
    "Which animal is your favourite and why?",
]

# Appended verbatim after the yaml.dump output — commented-out reference sections
# showing every supported field so users know what to uncomment.
TEMPLATE_COMMENTS = """
# ---------------------------------------------------------------------------
# Optional: preferred models (first available model is used at runtime).
# If omitted, the server's default model is used.
# ---------------------------------------------------------------------------
# models:
#   - Qwen3.5-35B-A3B-GGUF   # best quality
#   - Qwen3-0.6B-GGUF         # fastest / smallest

# ---------------------------------------------------------------------------
# Optional: tools that give your agent extra capabilities.
# Available tools: rag, file_search, file_io, shell, screenshot, sd, vlm
# Default when omitted: [rag, file_search]
# ---------------------------------------------------------------------------
# tools:
#   - rag          # document Q&A with RAG
#   - file_search  # find files on disk

# ---------------------------------------------------------------------------
# Optional: MCP (Model Context Protocol) servers that extend your agent
# with external tools. Each entry launches a subprocess when the agent starts.
# ---------------------------------------------------------------------------
# mcp_servers:
#   # Example 1 — a time server
#   time:
#     command: npx
#     args: ["-y", "@anthropic/mcp-time-server"]
#
#   # Example 2 — a weather server with an API key
#   weather:
#     command: npx
#     args: ["-y", "@anthropic/mcp-weather-server"]
#     env:
#       WEATHER_API_KEY: "your-api-key-here"
"""
