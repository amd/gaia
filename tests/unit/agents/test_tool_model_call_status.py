# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A tool's own model calls say where they are and when the server restarts.

Page-by-page extraction ran for minutes with nothing on the live line, and a
model server that stopped under it showed no restart status: the live status
the agent loop sends never reached calls a tool makes itself.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from gaia.agents.base.agent import (
    SERVER_RESTARTING_MESSAGE,
    Agent,
)
from gaia.agents.base.extraction import PAGE_CHARS, ExtractionLedger, extract_pages
from gaia.agents.base.tools import _TOOL_REGISTRY


def _reply(*items):
    return json.dumps(
        {"complete": True, "items": [{"text": x, "quote": x} for x in items]}
    )


def _ask(system, prompt):
    return _reply()


def test_extract_pages_reports_each_page_with_the_page_count():
    source = ("word " * (PAGE_CHARS // 5)) * 3
    seen = []
    _, pages = extract_pages(
        source,
        "List items",
        _ask,
        lambda: None,
        progress=lambda n, t: seen.append((n, t)),
    )
    assert pages == 3
    assert sorted(set(seen)) == [(1, 3), (2, 3), (3, 3)]
    assert seen[0] == (1, 3)


def test_ledger_run_passes_progress_through(tmp_path):
    (tmp_path / "manual.md").write_text("Code A1 is a fault.")
    ledger = ExtractionLedger("List every code in manual.md", str(tmp_path))
    seen = []
    result = ledger.run(
        "manual.md",
        lambda path: "Code A1 is a fault.",
        _ask,
        lambda: None,
        progress=lambda n, t: seen.append((n, t)),
    )
    assert result["status"] == "success"
    assert seen and seen[0] == (1, 1)


class _ExtractAgent(Agent):
    def _get_system_prompt(self):
        return "test"

    def _register_tools(self):
        pass


@pytest.fixture
def agent(tmp_path, monkeypatch):
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    monkeypatch.chdir(tmp_path)
    with patch("gaia.agents.base.agent.AgentSDK"):
        instance = _ExtractAgent(silent_mode=True, skip_lemonade=True)
    instance.console = MagicMock()
    instance.console.cancelled = None
    instance._read_validator = lambda: SimpleNamespace(
        validate_read=lambda path, prompt_user=False: (True, "")
    )
    yield instance
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


def _extraction_tool(agent, tmp_path, send):
    (tmp_path / "plant_manual.md").write_text("Code A1 is a fault.")
    backend = SimpleNamespace(model_load_listener=None)
    chat = MagicMock()
    chat.llm_client = SimpleNamespace(_backend=backend)
    chat.send_messages.side_effect = lambda **kw: send(backend, **kw)
    agent._make_extraction_chat = lambda: chat
    agent._extraction_ledger = ExtractionLedger(
        "List every code in plant_manual.md", str(tmp_path)
    )
    agent._tool_reported_usage = []
    agent._register_extraction_tool()
    return agent._tools_registry["extract_document_items"]["function"]


def _phases(agent):
    return [c.args for c in agent.console.report_phase.call_args_list]


def test_extraction_shows_page_progress_on_the_live_line(agent, tmp_path):
    function = _extraction_tool(
        agent,
        tmp_path,
        lambda backend, **kw: MagicMock(text=_reply(), usage={}, stats={}),
    )
    assert function("plant_manual.md")["status"] == "success"
    assert ("reading", "Reading page 1 of 1…") in _phases(agent)


def test_server_stop_under_an_extraction_call_shows_the_restart(agent, tmp_path):
    def send(backend, **kw):
        if not getattr(send, "stopped", False):
            send.stopped = True
            # What the client does while it waits for the daemon to bring it back.
            backend.model_load_listener("Gemma-4-E4B-it-GGUF", "reconnecting")
            backend.model_load_listener("Gemma-4-E4B-it-GGUF", "starting_server")
            backend.model_load_listener("Gemma-4-E4B-it-GGUF", "loaded")
        return MagicMock(text=_reply(), usage={}, stats={})

    function = _extraction_tool(agent, tmp_path, send)
    assert function("plant_manual.md")["status"] == "success"
    phases = _phases(agent)
    restart = ("model_restart", SERVER_RESTARTING_MESSAGE)
    assert restart in phases
    assert ("reconnecting",) not in [p[:1] for p in phases]
    # Once the model is back, the line returns to the page being read.
    after = phases[phases.index(restart) + 1 :]
    assert ("reading", "Reading page 1 of 1…") in after
    assert agent.console.report_phase.call_args_list[-1] == call(
        "reading", "Reading page 1 of 1…"
    )
