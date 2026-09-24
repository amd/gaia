# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Every "Lemonade is not reachable" message tells the user how to start it.

The start instruction must come from ``describe_start_hint()``, which resolves
the host's actual install. The legacy ``lemonade-server serve`` CLI was removed
in Lemonade 10.7/10.8, so a hard-coded copy of it sends users to a command
their machine does not have.
"""

from __future__ import annotations

import pytest

pytest.importorskip("gaia_agent")

from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent import caller_auth  # noqa: E402
from gaia_agent import server as server_mod  # noqa: E402
from gaia_agent import stdio as stdio_mod  # noqa: E402

from gaia.llm.lemonade_launcher import StartHint  # noqa: E402

_REMOVED_CLI = "lemonade-server serve"
_INSTRUCTION = "Run: lemond --port 13305"


@pytest.fixture
def start_hint(monkeypatch):
    monkeypatch.setattr(
        stdio_mod,
        "describe_start_hint",
        lambda *a, **k: StartHint(instruction=_INSTRUCTION, command="lemond"),
    )


def _init_body(monkeypatch):
    caller_auth.reset()
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    monkeypatch.delenv(caller_auth.TOKEN_ENV_VAR, raising=False)
    monkeypatch.setattr(
        server_mod,
        "_probe_lemonade",
        lambda: {
            "base_url": "http://127.0.0.1:13305/api/v1",
            "reachable": False,
            "version": None,
            "present": False,
            "ctx_size": None,
            "model_id": "Gemma-4-E4B-it-GGUF",
        },
    )
    try:
        client = TestClient(server_mod.build_app(), base_url="http://127.0.0.1:8141")
        response = client.get("/v1/gaia/init")
    finally:
        caller_auth.reset()
    assert response.status_code == 503
    return response.json()


def test_init_hint_names_the_host_start_instruction(monkeypatch, start_hint):
    hint = _init_body(monkeypatch)["hint"]

    assert _REMOVED_CLI not in hint
    assert "http://127.0.0.1:13305/api/v1" in hint
    assert f"{_INSTRUCTION}." in hint
    assert "LEMONADE_BASE_URL" in hint
    assert server_mod._DOCS_URL in hint


def test_init_hint_never_names_the_removed_cli_unpatched(monkeypatch):
    """Whatever this host has installed, the hint is not the hard-coded CLI."""
    hint = _init_body(monkeypatch)["hint"]

    assert hint.startswith("Local Lemonade Server is not reachable")
    assert "Start it with `lemonade-server serve`" not in hint


def test_server_run_error_names_the_host_start_instruction(start_hint):
    detail = server_mod._terminal_error_detail(
        ConnectionError("Max retries exceeded ... Connection refused")
    )

    assert _REMOVED_CLI not in detail
    assert f"{_INSTRUCTION}. See {server_mod._DOCS_URL}" in detail
    assert "Connection refused" in detail


def test_instruction_is_punctuated_once(monkeypatch):
    monkeypatch.setattr(
        stdio_mod,
        "describe_start_hint",
        lambda *a, **k: StartHint(instruction="Start the app, then retry."),
    )

    assert stdio_mod.lemonade_start_instruction() == "Start the app, then retry."
