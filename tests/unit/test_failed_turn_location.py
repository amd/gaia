# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A turn that failed before any reply records where its own model ran.

The Agent UI offers the local model server's log only for a local turn. It reads
the turn's saved location, so switching models afterwards cannot change which
log a cloud failure points at.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

import gaia.ui._chat_helpers as helpers
from gaia.agents.registry import AgentRegistry
from gaia.hub.installer import register_installed_sidecars
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest

NOT_CONNECTED = "deepseek-v4p1-flash isn't connected, so GAIA can't answer."


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """The registry after a binary Hub install of the flagship: its turns are relayed."""
    root = tmp_path / "agents"
    (root / "gaia").mkdir(parents=True)
    (root / "gaia" / ".installed").write_text(
        json.dumps(
            {
                "id": "gaia",
                "version": "0.2.0",
                "language": "python",
                "installed_at": "2026-09-23T00:00:00Z",
                "artifact_sha256": "0" * 64,
                "artifact_kind": "binary",
                "executable": "gaia-agent-gaia",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "gaia.hub.installer.default_install_root", lambda: root, raising=False
    )
    registry = AgentRegistry()
    registry._agents.clear()
    register_installed_sidecars(registry)
    return registry


def _saved_location(monkeypatch, registry, model: str, route: dict | None = None):
    """The location recorded on a relayed turn that failed before replying."""

    def fail_before_replying(sse_handler, *_args, **_kwargs):
        if route is not None:
            sse_handler.relayed_route = route
        sse_handler.print_error(NOT_CONNECTED)

    async def no_title(**_kwargs):
        return None

    monkeypatch.setattr(helpers, "_agent_registry", registry)
    monkeypatch.setattr(helpers, "_dispatch_sidecar_query", fail_before_replying)
    monkeypatch.setattr(helpers, "_maybe_update_session_title", no_title)
    db = ChatDatabase(":memory:")
    session = db.create_session(model=model, agent_type="gaia")
    request = ChatRequest(
        session_id=session["id"], message="Hello", stream=True, agent_type="gaia"
    )

    async def drain():
        async for _ in helpers._stream_chat_impl(
            SimpleNamespace(handler=None), db, session, request
        ):
            pass

    asyncio.run(drain())
    saved = db.get_messages(session["id"])[-1]
    db.close()
    assert saved["failed"], saved
    return saved["turn_summary"].get("model")


def test_a_cloud_turn_that_failed_is_saved_as_remote(monkeypatch, registry):
    where = _saved_location(monkeypatch, registry, "fireworks.deepseek-v4p1-flash")
    assert where is not None and where["remote"] is True


def test_a_local_turn_that_failed_is_saved_as_local(monkeypatch, registry):
    where = _saved_location(monkeypatch, registry, "Gemma-4-E4B-it-GGUF")
    assert where is not None and where["remote"] is False


def test_a_hybrid_turn_that_routed_to_the_cloud_and_failed_is_saved_as_remote(
    monkeypatch, registry
):
    import gaia.llm.hybrid as hybrid

    real_start = hybrid.start_model
    monkeypatch.setattr(
        hybrid,
        "start_model",
        lambda m, config=None: (
            "Gemma-4-E4B-it-GGUF" if hybrid.is_hybrid_model(m) else real_start(m)
        ),
    )
    where = _saved_location(
        monkeypatch,
        registry,
        "hybrid",
        route={"model": "fireworks.deepseek-v4p1-flash"},
    )
    assert where is not None and where["remote"] is True
