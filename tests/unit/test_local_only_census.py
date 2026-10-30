# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Census: every file that can reach a cloud AI service answers to Local only.

A file that builds an Anthropic or OpenAI client, launches the ``claude`` CLI,
or calls Perplexity must use ``gaia.llm.local_only`` — or be listed below with
the reason it can't leak. A new outbound AI path therefore fails here instead
of quietly bypassing the setting.

The check is per file: one guard anywhere in a file clears every call site in
it, so a second outbound call added to an already-guarded file needs review.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ROOTS = [REPO / "src" / "gaia", *sorted((REPO / "hub" / "agents").glob("*/python"))]

OUTBOUND = {
    "Anthropic client": re.compile(r"\b(?:\w+\.)?(?:Async)?Anthropic\("),
    "OpenAI client": re.compile(r"\b(?:Async)?OpenAI\("),
    "claude CLI": re.compile(
        r"""which\(\s*["']claude["']\s*\)|["']claude["']\s*,\s*["']-p["']"""
        r"""|GAIA_CLAUDE_BIN"""
    ),
    "Perplexity": re.compile(r"perplexity_ask|api\.perplexity\.ai"),
    "chat endpoint": re.compile(
        r"""["'][^"'\n]*/(?:chat/completions|v1/messages)["']"""
    ),
}

#: Files that match but can't send conversation content to a cloud model.
EXEMPT = {
    # Inbound: GAIA's own OpenAI-compatible servers declare these routes.
    "src/gaia/api/app.py": "serves /v1/chat/completions; makes no outbound call",
    "src/gaia/api/openai_server.py": "serves /v1/chat/completions; makes no outbound call",
    "src/gaia/agents/base/server.py": "serves /v1/chat/completions; makes no outbound call",
    "src/gaia/mcp/mcp_bridge.py": "documents GAIA's own API endpoint; no outbound AI call",
    "src/gaia/api/__init__.py": "the OpenAI client appears only in a docstring example",
}

GUARD = re.compile(r"\blocal_only\b|_refuse_unless_local\(")


def _python_files():
    for root in ROOTS:
        for path in root.rglob("*.py"):
            if "tests" in path.parts or path.name.startswith("test_"):
                continue
            yield path


def _matches(text):
    return [name for name, pattern in OUTBOUND.items() if pattern.search(text)]


def test_every_outbound_ai_path_is_guarded_or_exempt():
    unguarded = []
    for path in _python_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        hits = _matches(text)
        if not hits:
            continue
        rel = path.relative_to(REPO).as_posix()
        if rel in EXEMPT or GUARD.search(text):
            continue
        unguarded.append(f"{rel}: {', '.join(hits)}")
    assert not unguarded, (
        "These files can reach a cloud AI service but never consult Local only "
        "(gaia.llm.local_only):\n  " + "\n  ".join(unguarded) + "\nGuard the call "
        "(assert_provider_permitted / assert_model_permitted / guard_inference), "
        "or add the file to EXEMPT in this test with the reason it can't leak."
    )


def test_every_exemption_still_matches():
    """A stale exemption would let a real outbound path in under its name."""
    for rel in EXEMPT:
        path = REPO / rel
        assert path.exists(), f"{rel} is exempt but no longer exists; drop it"
        assert _matches(
            path.read_text(encoding="utf-8", errors="replace")
        ), f"{rel} is exempt but no longer matches any outbound pattern; drop it"


@pytest.mark.parametrize(
    "snippet",
    [
        "client = anthropic.Anthropic(api_key=k)",
        "self._client = sdk.Anthropic(**kw)",
        "client = AsyncOpenAI(base_url=u)",
        'exe = shutil.which("claude")',
        'cmd = ["claude", "-p", prompt]',
        'candidate = os.environ.get("GAIA_CLAUDE_BIN") or "claude"',
        'self.call_tool("perplexity_ask", args)',
        'requests.post(f"{base}/v1/messages", json=body)',
    ],
)
def test_the_census_recognises_each_outbound_shape(snippet):
    assert _matches(snippet), snippet
