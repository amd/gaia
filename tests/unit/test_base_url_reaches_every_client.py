# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A ``--base-url`` reaches every Lemonade client the command creates.

`gaia talk --index ... --base-url X` indexed on the default-port server
(reloading its embedder), and `gaia api start --base-url X` reported that
server's health instead of X's. `gaia chat` and `gaia -p` sent their hardware,
health and embedder calls there too.
"""

import asyncio
import os
import sys
from argparse import Namespace
from unittest.mock import Mock, patch

import pytest

from gaia.chat.sdk import AgentConfig, AgentSDK

DEAD = "http://127.0.0.1:1/api/v1"


def test_chat_enable_rag_hands_its_base_url_to_rag(monkeypatch):
    import gaia.rag.sdk

    seen = {}

    class _FakeRAG:
        def __init__(self, config):
            seen["config"] = config

    monkeypatch.setattr(gaia.rag.sdk, "RAGSDK", _FakeRAG)
    sdk = AgentSDK(AgentConfig(base_url=DEAD))

    sdk.enable_rag()

    assert seen["config"].base_url == DEAD


def test_enable_rag_keeps_an_explicit_rag_base_url(monkeypatch):
    import gaia.rag.sdk

    seen = {}

    class _FakeRAG:
        def __init__(self, config):
            seen["config"] = config

    monkeypatch.setattr(gaia.rag.sdk, "RAGSDK", _FakeRAG)
    sdk = AgentSDK(AgentConfig(base_url=DEAD))
    other = "http://127.0.0.1:2/api/v1"

    sdk.enable_rag(base_url=other)

    assert seen["config"].base_url == other


def test_rag_llm_chunking_client_uses_the_configured_base_url(tmp_path):
    pytest.importorskip("faiss", reason="RAGSDK requires faiss-cpu")
    from gaia.rag.sdk import RAGSDK, RAGConfig

    with patch("gaia.rag.sdk.AgentSDK"):
        sdk = RAGSDK(
            RAGConfig(cache_dir=str(tmp_path), base_url=DEAD, use_llm_chunking=True)
        )
    sdk.llm_client = None

    with (
        patch("gaia.llm.lemonade_client.LemonadeClient", return_value=Mock()) as ctor,
        patch.object(sdk, "_llm_based_chunking", return_value=["chunk"]),
        patch.object(sdk, "_fit_chunks_to_embedder", side_effect=lambda c: c),
    ):
        assert sdk._split_text_into_chunks("some text") == ["chunk"]
    assert ctor.call_args.kwargs.get("base_url") == DEAD


def test_rag_embedder_and_chat_use_the_configured_base_url(tmp_path):
    pytest.importorskip("faiss", reason="RAGSDK requires faiss-cpu")
    from gaia.rag.sdk import RAGSDK, RAGConfig

    with patch("gaia.rag.sdk.AgentSDK") as chat_sdk:
        sdk = RAGSDK(RAGConfig(cache_dir=str(tmp_path), base_url=DEAD))
    assert chat_sdk.call_args.args[0].base_url == DEAD

    with (
        patch("gaia.llm.lemonade_client.LemonadeClient", return_value=Mock()) as ctor,
        patch("gaia.daemon.broker_client.model_lease"),
    ):
        sdk._load_embedder()
    assert ctor.call_args.kwargs.get("base_url") == DEAD


def test_api_start_health_reports_the_base_url_server(monkeypatch):
    from gaia import cli

    monkeypatch.setenv("LEMONADE_BASE_URL", "unset-after-test")
    monkeypatch.delenv("LEMONADE_BASE_URL")
    seen = {}

    def fake_run(app, **_kwargs):
        from gaia.api import openai_server

        seen["llm"] = asyncio.run(openai_server._lemonade_health())

    monkeypatch.setitem(sys.modules, "uvicorn", Mock(run=fake_run))
    cli.handle_api_command(
        Namespace(
            subcommand="start",
            host="127.0.0.1",
            port=0,
            no_lemonade_check=True,
            base_url=DEAD,
        )
    )

    assert seen["llm"]["url"] == DEAD
    assert seen["llm"]["status"] == "unavailable"


def test_print_mode_points_every_client_at_the_base_url(monkeypatch):
    from gaia import cli, print_mode

    # The environment names one server; the flag names another.
    monkeypatch.setenv("LEMONADE_BASE_URL", "http://127.0.0.1:2/api/v1")
    seen = {}

    def fake_run(_prompt, **kwargs):
        seen.update(env=os.environ.get("LEMONADE_BASE_URL"), arg=kwargs["base_url"])
        return 0

    monkeypatch.setattr(print_mode, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["gaia", "-p", "hi", "--base-url", DEAD])
    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 0
    assert seen == {"env": DEAD, "arg": DEAD}


def test_chat_points_every_client_at_the_base_url(monkeypatch):
    from gaia import cli

    monkeypatch.setenv("LEMONADE_BASE_URL", "http://127.0.0.1:2/api/v1")
    seen = {}

    def fake_run_cli(action, **kwargs):
        seen.update(action=action, env=os.environ.get("LEMONADE_BASE_URL"))
        return 0

    monkeypatch.setattr(cli, "run_cli", fake_run_cli)
    monkeypatch.setattr(sys, "argv", ["gaia", "chat", "--base-url", DEAD])
    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 0
    assert seen == {"action": "chat", "env": DEAD}
