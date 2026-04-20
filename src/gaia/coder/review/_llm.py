# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Single seam for every LLM call made by the review passes.

Every Opus-driven pass (3, 5, 6, 7) goes through :func:`call_opus`, which
tests patch wholesale with ``pytest-mock``. The production implementation
prefers the Claude Agent SDK when it is installed (so the review can route
through dedicated subagents such as ``architecture-reviewer`` for Pass 3 and
``code-reviewer`` for Pass 6 per §8) and falls back to a direct
:mod:`anthropic` SDK call otherwise. If *neither* is installed the call
raises — no silent fallback per the repo ``CLAUDE.md`` rule.

Prompt files are loaded from :mod:`gaia.coder.prompts` via
:func:`load_prompt` and rendered with a tiny ``{{slot}}`` mustache-lite
formatter. The XML-in-prompt layout from §15.8 is preserved verbatim; only
the ``{{…}}`` placeholders are substituted.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

#: Opus 4.7 is gaia-coder's production model per §3.2. Tests patch this
#: string, or patch :func:`call_opus` directly.
DEFAULT_MODEL: str = "claude-opus-4-7-20251001"

#: Spec §15.8 caps at 1500 tokens for Passes 3, 4 (persona), 5 (persona),
#: 6, 7. Individual callers may override.
DEFAULT_MAX_TOKENS: int = 1500

#: Temperature for review prompts. §15.8 requires ``temperature=0`` for every
#: review pass. The P10 standup prompt uses 0.3 but that is not a review pass.
DEFAULT_TEMPERATURE: float = 0.0

_PROMPTS_DIR: Path = Path(__file__).resolve().parent.parent / "prompts"


class LLMClientUnavailable(RuntimeError):
    """Raised when no LLM client is installed in the runtime.

    Per the fail-loudly rule (CLAUDE.md): we do not silently substitute a
    deterministic stub when the LLM is missing. Review passes that require
    an LLM raise this and the gate records a ``fail`` with an actionable
    error rather than passing vacuously.
    """


def load_prompt(name: str) -> str:
    """Return the raw text of ``src/gaia/coder/prompts/{name}``.

    The ``.md`` suffix is implied if not supplied.

    Raises:
        FileNotFoundError: if the prompt is missing. Review passes do not
            run without their canonical prompts — per §15.8 every LLM call
            site has exactly one canonical prompt, no inlining.
    """
    stem = name if name.endswith(".md") else f"{name}.md"
    path = _PROMPTS_DIR / stem
    if not path.exists():
        raise FileNotFoundError(
            f"prompt template missing: {path}. Create it under "
            f"src/gaia/coder/prompts/ and re-run."
        )
    return path.read_text(encoding="utf-8")


def render_prompt(template: str, slots: Dict[str, str]) -> str:
    """Replace every ``{{key}}`` in ``template`` with ``slots[key]``.

    Keys without a supplied value are replaced with an explicit
    ``<missing>`` marker rather than left raw — a dangling ``{{foo}}`` in
    the real prompt would confuse the model about whether it is looking at
    a template or literal text.

    Unknown slots (keys in ``slots`` that do not appear in the template)
    are silently ignored; the prompt author is the source of truth.
    """
    rendered = template

    def _sub(match: "re.Match[str]") -> str:
        key = match.group(1).strip()
        return str(slots.get(key, "<missing>"))

    rendered = re.sub(r"\{\{\s*([^{}]+?)\s*\}\}", _sub, rendered)
    return rendered


def call_opus(
    prompt: str,
    *,
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    system: Optional[str] = None,
) -> str:
    """Send ``prompt`` to Opus 4.7 and return the raw assistant text.

    This is the single seam every review pass uses. Tests patch it with
    ``mocker.patch("gaia.coder.review._llm.call_opus", return_value=...)``
    or by stubbing the Anthropic SDK one level deeper via
    ``mocker.patch("anthropic.Anthropic", ...)``. The former is preferred —
    patching the seam is cheaper and less fragile than patching a vendor
    SDK.

    Args:
        prompt: Fully rendered user message.
        model: Override the model. Defaults to :data:`DEFAULT_MODEL`.
        max_tokens: Cap on output tokens. Defaults to
            :data:`DEFAULT_MAX_TOKENS` (the §15.8 ceiling for review passes).
        temperature: Sampling temperature. Defaults to
            :data:`DEFAULT_TEMPERATURE` (0 — review passes must be
            deterministic).
        system: Optional system prompt. Passed through to the Anthropic API.

    Raises:
        LLMClientUnavailable: if no Anthropic client can be constructed.
    """
    model = model or DEFAULT_MODEL
    max_tokens = max_tokens or DEFAULT_MAX_TOKENS
    temperature = DEFAULT_TEMPERATURE if temperature is None else temperature

    try:
        import anthropic  # type: ignore
    except ImportError as exc:  # noqa: PERF203 — single exception is fine
        raise LLMClientUnavailable(
            "anthropic SDK not installed in this environment. Install with "
            "`pip install anthropic>=0.35` or patch "
            "`gaia.coder.review._llm.call_opus` in tests."
        ) from exc

    client = anthropic.Anthropic()
    kwargs: Dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system
    response = client.messages.create(**kwargs)

    # Anthropic v1 SDK returns a list of content blocks. For our flat JSON
    # prompts the first text block is the whole response.
    parts = []
    for block in getattr(response, "content", []):
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)


def parse_json_response(raw: str) -> Dict[str, Any]:
    """Parse a JSON response body, tolerating a leading ```json fence.

    Raises:
        ValueError: if the response is not valid JSON even after stripping
            a fence. Per §15.9, this surfaces as a ToolArgError — fail loudly.
    """
    text = raw.strip()
    # Strip an optional markdown code fence.
    fence_match = re.match(
        r"^```(?:json|JSON)?\s*\n?(?P<body>.*?)\n?```\s*$", text, re.DOTALL
    )
    if fence_match:
        text = fence_match.group("body")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"LLM response was not valid JSON: {exc}. First 200 chars: "
            f"{raw[:200]!r}."
        ) from exc


__all__ = [
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MODEL",
    "DEFAULT_TEMPERATURE",
    "LLMClientUnavailable",
    "call_opus",
    "load_prompt",
    "parse_json_response",
    "render_prompt",
]
