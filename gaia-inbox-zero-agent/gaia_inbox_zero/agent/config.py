"""
Centralized Configuration for GAIA Inbox Zero Agent

All configuration constants, environment variable reads, and defaults
are defined here. Import from this module rather than hardcoding values.
"""

import os
from pathlib import Path
from typing import List, Dict

from gaia_inbox_zero.data.email_loader import DEFAULT_MBOX_PATH

# Re-export from data layer
__all__ = ["DEFAULT_MBOX_PATH"]

MBOX_PATH = os.environ.get("MBOX_PATH", DEFAULT_MBOX_PATH)

# ── LLM Provider: Lemonade (OpenAI-compatible) ─────────────────────────────

LEMONADE_BASE_URL = os.environ.get(
    "LEMONADE_BASE_URL",
    "http://localhost:8000/api/v1",
)

LEMONADE_URL = os.environ.get(
    "LEMONADE_URL",
    "http://localhost:8001/v1/chat/completions",
)

# ── LLM Provider: Anthropic ────────────────────────────────────────────────

ANTHROPIC_URL = os.environ.get(
    "ANTHROPIC_URL",
    "https://api.anthropic.com/v1/messages",
)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ── LLM Gateway (optional) ─────────────────────────────────────────────────

LLM_GATEWAY_URL = os.environ.get("LLM_GATEWAY_URL", "")
LLM_GATEWAY_KEY = os.environ.get("LLM_GATEWAY_KEY", "")

# ── Model Selection ────────────────────────────────────────────────────────

GAIA_MODEL = os.environ.get("GAIA_MODEL", "Qwen3.5-35B-A3B-GGUF")

# ── Classification Categories ──────────────────────────────────────────────

CATEGORIES: List[str] = ["URGENT", "NEEDS_RESPONSE", "FYI", "PROMOTIONAL", "PERSONAL"]

# ── Default Models ─────────────────────────────────────────────────────────

DEFAULT_MODELS: Dict[str, str] = {
    "lemonade": "Qwen3.5-4B-GGUF",
    "anthropic": "claude-opus-4-6-20260507",
}

# ── Classification Prompt ──────────────────────────────────────────────────

CLASSIFICATION_PROMPT = """You are an email triage assistant. Classify this email into EXACTLY ONE category:

- URGENT: Time-sensitive, needs response within hours
- NEEDS_RESPONSE: Requires action but not urgent
- FYI: Informational, no action needed
- PROMOTIONAL: Marketing, newsletters, deals
- PERSONAL: Friends, family, non-work

Respond with ONLY the category name, nothing else.

Email:
From: {sender}
Subject: {subject}
Body preview: {body_preview}
"""

# ── Batch Classifier Defaults ──────────────────────────────────────────────

DEFAULT_LIMIT = 100
DEFAULT_BATCH_SIZE = 20

# ── Results Directory ──────────────────────────────────────────────────────

RESULTS_DIR = Path(__file__).resolve().parents[2] / "results"
OUTPUT_FILE = RESULTS_DIR / "latest-inbox-zero-results.json"
