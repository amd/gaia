#!/usr/bin/env python
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Manual diagnostic script for issue #2763 (email agent context-overflow bug).

NOT a pytest test suite -- this constructs a real ``EmailTriageAgent`` (with a
minimal in-memory backend, no live Lemonade/Gmail needed) and a
``FakeGmailBackend`` seeded with long-body messages from one sender, then
measures the actual composed system_prompt, the actual OpenAI tool-calling
schema (``_openai_tools``), and the actual ``search_messages`` envelope --
using this repo's own ``context_budget.py`` estimator functions, so the
printed numbers are directly comparable to what the production code itself
computes when it decides whether to shrink a tool result.

Kept here (not thrown away after the investigation) because the same
question -- "does the fixed per-turn overhead / envelope size assumption
still match reality" -- will recur as the email agent's tool registry grows.
Re-run this after any change to the tool registry, the system prompt, or
``context_budget.py``'s constants.

Usage:
    python scripts/email_context_budget_measurement_2763.py
    python scripts/email_context_budget_measurement_2763.py --dump-dir /tmp/payloads
        # also writes the exact payload strings (system_prompt, the
        # _openai_tools JSON, the search_messages envelope JSON) to
        # <dump-dir>, e.g. for a real-tokenizer cross-check:
        #   llama-tokenize -m <gguf> -f <dump-dir>/openai_tools.json --show-count

Requires: gaia-agent-email installed editable (``uv pip install -e
hub/agents/email/python``) in the active environment. No live Lemonade or
Gmail connection needed -- this is fully hermetic.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

# Add project root to path (mirrors scripts/jira_smoke.py's convention).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from gaia_agent_email.context_budget import (  # noqa: E402
    envelope_budget_tokens,
    estimate_tokens,
    estimate_tokens_json,
)
from gaia_agent_email.tools.read_tools import (  # noqa: E402
    DEFAULT_BODY_LIMIT_CHARS,
    search_messages_impl,
)

from gaia.llm.lemonade_client import (  # noqa: E402
    GPU_CTX_SIZE,
    NPU_CTX_SIZE,
    is_tool_calling_model,
)
from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _msg_with_body(
    msg_id: str, body_text: str, subject: str, sender: str, **overrides: Any
) -> Dict[str, Any]:
    msg: Dict[str, Any] = {
        "id": msg_id,
        "threadId": msg_id,
        "labelIds": ["INBOX"],
        "snippet": body_text[:200],
        "internalDate": "1750000000000",
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
                {"name": "To", "value": "user@example.com"},
                {"name": "Date", "value": "Mon, 1 Jan 2026 00:00:00 +0000"},
            ],
            "body": {
                "data": _b64url(body_text),
                "size": len(body_text.encode("utf-8")),
            },
        },
        "sizeEstimate": len(body_text),
    }
    msg.update(overrides)
    return msg


def build_long_body_sender_inbox(
    n: int = 15, raw_body_chars: int = 12000
) -> FakeGmailBackend:
    """N messages from one sender, each with a body far longer than
    DEFAULT_BODY_LIMIT_CHARS -- issue #2763's stated repro shape ("a sender
    whose messages have long bodies"), modeled after the real probe
    (``from:Every newer_than:14d``, true count 15).
    """
    gmail = FakeGmailBackend(user_email="user@example.com")
    base_date = 1_800_000_000_000
    paragraph = (
        "Every is a media company that publishes essays on technology, "
        "startups, and the future of work. This edition covers several "
        "topics in depth, with extended analysis and multiple sections. "
    )
    for i in range(n):
        body = (paragraph * (raw_body_chars // len(paragraph) + 1))[:raw_body_chars]
        msg = _msg_with_body(
            f"every{i}",
            body,
            subject=f"Every: Issue #{100 + i}",
            sender="Every <newsletter@every.to>",
            threadId=f"every{i}",
            internalDate=str(base_date - i),
        )
        gmail.add_message(msg)
    return gmail


def build_agent(model_id: str = "Gemma-4-E4B-it-GGUF"):
    """Instantiate a real EmailTriageAgent with a minimal in-memory backend
    (no live Lemonade/Gmail) and return it, so ``agent.system_prompt`` and
    ``agent._openai_tools`` reflect the REAL, currently-registered tool set.
    """
    from gaia_agent_email.agent import EmailTriageAgent
    from gaia_agent_email.config import EmailAgentConfig

    class _MinimalMailBackend:
        pass

    class _MinimalCalendarBackend:
        pass

    tmp = tempfile.mkdtemp()
    cfg = EmailAgentConfig(
        gmail_backend=_MinimalMailBackend(),
        calendar_backend=_MinimalCalendarBackend(),
        db_path=str(Path(tmp) / "state.db"),
        memory_db_path=str(Path(tmp) / "memory.db"),
        silent_mode=True,
        debug=False,
        model_id=model_id,
    )
    with patch("gaia.agents.base.agent.AgentSDK") as mock_sdk:
        mock_sdk.return_value = MagicMock()
        return EmailTriageAgent(config=cfg)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dump-dir",
        type=Path,
        default=None,
        help="Write the exact payload strings here for a real-tokenizer cross-check "
        "(e.g. llama-tokenize against the actual GGUF).",
    )
    args = parser.parse_args()

    print("=" * 78)
    print("GPU profile (Gemma-4-E4B-it-GGUF, tool_calling=True)")
    print("=" * 78)
    gpu_agent = build_agent("Gemma-4-E4B-it-GGUF")
    gpu_system_prompt = gpu_agent.system_prompt
    print(
        f"model_id: {gpu_agent.model_id}  tool_calling: {is_tool_calling_model(gpu_agent.model_id)}"
    )
    print(
        f"system_prompt: {len(gpu_system_prompt)} chars, {estimate_tokens(gpu_system_prompt)} est tokens"
    )
    openai_tools = gpu_agent._openai_tools
    openai_tools_json = json.dumps(openai_tools, default=str) if openai_tools else ""
    if openai_tools:
        print(
            f"_openai_tools: {len(openai_tools)} tool schemas, "
            f"{len(openai_tools_json)} chars, "
            f"{estimate_tokens_json(openai_tools_json)} est tokens"
        )
    else:
        print("_openai_tools: None (no separate tools= payload)")

    gpu_budget = envelope_budget_tokens(ctx_size=GPU_CTX_SIZE)
    npu_budget = envelope_budget_tokens(ctx_size=NPU_CTX_SIZE)
    print()
    print(f"envelope_budget_tokens(GPU, ctx={GPU_CTX_SIZE}) = {gpu_budget}")
    print(f"envelope_budget_tokens(NPU, ctx={NPU_CTX_SIZE}) = {npu_budget}")

    print()
    print("=" * 78)
    print("NPU profile (gemma4-it-e2b-FLM, tool_calling=False -- embedded-JSON path)")
    print("=" * 78)
    npu_agent = build_agent("gemma4-it-e2b-FLM")
    npu_system_prompt = npu_agent.system_prompt
    print(
        f"model_id: {npu_agent.model_id}  tool_calling: {is_tool_calling_model(npu_agent.model_id)}"
    )
    print(
        f"system_prompt: {len(npu_system_prompt)} chars, {estimate_tokens(npu_system_prompt)} est tokens"
    )
    print(f"_openai_tools: {npu_agent._openai_tools!r}")

    print()
    print("=" * 78)
    print("search_messages_impl on 15 long-body messages from one sender")
    print("(mirrors the failing probe: from:Every newer_than:14d, true count 15)")
    print("=" * 78)
    for max_results in (25, 50, 100):
        gmail = build_long_body_sender_inbox(n=15, raw_body_chars=12000)
        result = search_messages_impl(
            gmail,
            query="from:every",
            max_results=max_results,
            debug=False,
            operator_retry=False,
            budget_tokens=None,  # production default: active_profile_ctx_size()
        )
        messages = result["messages"]
        serialized = json.dumps({"messages": messages}, default=str)
        env_tokens = estimate_tokens_json(serialized)
        dropped = sorted({m["body_chars_dropped"] for m in messages})
        default_cap_drop_only = 12000 - DEFAULT_BODY_LIMIT_CHARS
        shrink_fired = any(d > default_cap_drop_only for d in dropped)
        print(
            f"max_results={max_results}: {len(messages)} messages, "
            f"{len(serialized)} chars, {env_tokens} est tokens, "
            f"shrink_fired={shrink_fired}, fits_gpu_budget={env_tokens <= gpu_budget}"
        )

    if args.dump_dir:
        args.dump_dir.mkdir(parents=True, exist_ok=True)
        gmail = build_long_body_sender_inbox(n=15, raw_body_chars=12000)
        result = search_messages_impl(
            gmail,
            query="from:every",
            max_results=100,
            debug=False,
            operator_retry=False,
            budget_tokens=None,
        )
        tool_result_json = json.dumps(
            {
                "ok": True,
                "data": {
                    "messages": result["messages"],
                    "count": len(result["messages"]),
                    "truncated": False,
                },
            },
            default=str,
        )
        (args.dump_dir / "system_prompt.txt").write_text(gpu_system_prompt)
        (args.dump_dir / "openai_tools.json").write_text(openai_tools_json)
        (args.dump_dir / "tool_result.json").write_text(tool_result_json)
        (args.dump_dir / "npu_system_prompt.txt").write_text(npu_system_prompt)
        print()
        print(
            f"Wrote payload files to {args.dump_dir} for a real-tokenizer cross-check."
        )


if __name__ == "__main__":
    main()
