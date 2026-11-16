# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A chat after GAIA's model server failed to start ends in seconds.

With GAIA's own ``lemond`` missing, the chat used to fall back to the default
port, poll it for free memory for about 50 s, and then say the model "may be
loading or briefly busy". Now the missing binary is a failed start of GAIA's
own server, a failed start is remembered so the next model call fails at once
with the setup fix, and a free-memory read nobody answers is "server down".
"""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests

from gaia.agents.base import memory
from gaia.agents.base.memory import MemoryMixin
from gaia.daemon.errors import LemonadeStartFailedError
from gaia.daemon.lemonade import EmbeddedLemonadeOwner, LemonadeNotManaged
from gaia.llm import lemonade_client as lc
from gaia.llm import lemonade_launcher
from gaia.llm.lemonade_embedded import EmbeddedLemonade, EmbeddedLemonadeError
from gaia.llm.lemonade_manager import LemonadeManager
from gaia.llm.lemonade_service import LemonadeStartError
from gaia.llm.providers.lemonade import (
    LemonadeEngineStartError,
    LemonadeServerUnreachableError,
    classify_lemonade_exception,
)
from gaia.llm.surface_hints import TUI_SETUP_HINT
from gaia.logger import get_logger

_RETRY_WORDING = ("briefly busy", "try sending the message again", "Try again")
_REASON = "GAIA's Lemonade Server install is incomplete: lemond.exe is missing"


def _incomplete_install(tmp_path) -> EmbeddedLemonade:
    embedded = EmbeddedLemonade(home=tmp_path)
    embedded.dist_dir.mkdir(parents=True)
    return embedded


class TestAMissingLemondIsStillGaiasServer:
    def test_an_unpacked_version_without_lemond_is_incomplete(self, tmp_path):
        embedded = _incomplete_install(tmp_path)
        assert not embedded.is_installed()
        assert embedded.is_incomplete()

    def test_a_machine_never_set_up_is_not_incomplete(self, tmp_path):
        assert not EmbeddedLemonade(home=tmp_path).is_incomplete()

    def test_gaia_still_runs_the_server_so_no_default_port_fallback(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
        embedded = _incomplete_install(tmp_path)
        with patch(
            "gaia.llm.lemonade_embedded.EmbeddedLemonade", return_value=embedded
        ):
            assert lemonade_launcher.gaia_runs_lemonade() is True

    def test_the_daemon_reports_a_failed_start_not_not_installed(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
        embedded = _incomplete_install(tmp_path)
        owner = EmbeddedLemonadeOwner(factory=lambda: embedded)
        with pytest.raises(EmbeddedLemonadeError, match="incomplete") as caught:
            owner.ensure()
        assert not isinstance(caught.value, LemonadeNotManaged)
        assert str(embedded.daemon_path) in str(caught.value)


def _client(*, follows: bool = False, accepts: bool = False) -> lc.LemonadeClient:
    client = lc.LemonadeClient.__new__(lc.LemonadeClient)
    client.log = get_logger(__name__)
    client.base_url = "http://localhost:13305/api/v1"
    client.follows_gaia_lemonade = follows
    client._gaia_daemon_dir = None
    client._server_accepts_connections = lambda: accepts
    client.cloud_model_provider = lambda _model: None
    client._is_cloud_model = lambda _model: False
    return client


def _daemon_fails(client: lc.LemonadeClient) -> MagicMock:
    """Make *client*'s daemon fail the start the way it does for a missing lemond."""
    failed = LemonadeStartFailedError("start failed", reason=_REASON)
    ensure = MagicMock(side_effect=failed)
    client._point_at_gaia_lemonade = MagicMock()
    return ensure


class TestARememberedFailedStartFailsTheCallAtOnce:
    def test_the_next_model_call_fails_before_touching_the_server(self):
        lc.remember_engine_start_failure(_REASON)
        client = _client(follows=True)
        client._ensure_model_loaded_locked = MagicMock()
        ensure = _daemon_fails(client)
        with patch("gaia.daemon.client.ensure_lemonade", ensure):
            with pytest.raises(lc.GaiaLemonadeStartError) as caught:
                client._ensure_model_loaded(lc.DEFAULT_MODEL_NAME)
        ensure.assert_called_once()
        client._ensure_model_loaded_locked.assert_not_called()

        typed = classify_lemonade_exception(caught.value)
        assert isinstance(typed, LemonadeEngineStartError)
        assert typed.reason == _REASON
        assert TUI_SETUP_HINT in typed.user_message
        assert not any(w in typed.user_message for w in _RETRY_WORDING)

    def test_another_server_is_never_told_to_repair_gaia(self):
        lc.remember_engine_start_failure(_REASON)
        client = _client(follows=False)
        client._fail_fast_if_engine_cannot_start()
        assert lc.engine_start_failure() == _REASON

    def test_a_server_that_is_back_clears_it(self):
        lc.remember_engine_start_failure(_REASON)
        client = _client(follows=True, accepts=True)
        client._ensure_model_loaded_locked = MagicMock()
        with patch.object(lc.LemonadeClient, "_model_slot_lease", MagicMock()):
            client._ensure_model_loaded(lc.DEFAULT_MODEL_NAME)
        client._ensure_model_loaded_locked.assert_called_once()
        assert lc.engine_start_failure() is None

    def test_nothing_remembered_changes_nothing(self):
        client = _client(follows=True)
        client._server_accepts_connections = MagicMock()
        client._fail_fast_if_engine_cannot_start()
        client._server_accepts_connections.assert_not_called()


class TestFailedStartsAreRemembered:
    def test_the_daemons_failed_start(self):
        client = _client()
        failed = LemonadeStartFailedError("start failed", reason=_REASON)
        with patch("gaia.daemon.client.ensure_lemonade", side_effect=failed):
            with pytest.raises(lc.GaiaLemonadeStartError):
                client._start_gaia_lemonade()
        assert lc.engine_start_failure() == _REASON

    def test_a_start_that_works_clears_it(self):
        lc.remember_engine_start_failure(_REASON)
        client = _client()
        client._point_at_gaia_lemonade = MagicMock()
        served = {"base_url": "http://127.0.0.1:60001/api/v1"}
        with patch("gaia.daemon.client.ensure_lemonade", return_value=served):
            client._start_gaia_lemonade()
        assert lc.engine_start_failure() is None

    def test_a_server_gaia_does_not_run_is_not_remembered(self):
        failure = LemonadeStartError("GAIA could not find one to start.")
        with patch(
            "gaia.llm.lemonade_service.ensure_lemonade_running", side_effect=failure
        ):
            assert not LemonadeManager._autostart(_client(), 4096, True, MagicMock())
        assert lc.engine_start_failure() is None

    def test_ensure_ready_remembers_the_daemons_failed_start(self, monkeypatch):
        failed = LemonadeStartFailedError("start failed", reason=_REASON)
        monkeypatch.setattr(LemonadeManager, "_initialized", False, raising=False)
        with (
            patch("gaia.llm.lemonade_manager.gaia_runs_lemonade", return_value=True),
            patch.object(
                LemonadeManager, "start_embedded_if_stopped", side_effect=failed
            ),
            patch("gaia.llm.lemonade_manager.LemonadeClient"),
        ):
            assert LemonadeManager.ensure_ready(quiet=True) is False
        assert lc.engine_start_failure() == _REASON


def _refused() -> lc.LemonadeClientError:
    try:
        raise requests.exceptions.ConnectionError(
            "[WinError 10061] No connection could be made because the target "
            "machine actively refused it"
        )
    except requests.exceptions.ConnectionError as e:
        try:
            raise lc.LemonadeClientError(f"Request failed: {e}") from e
        except lc.LemonadeClientError as wrapped:
            return wrapped


def _free_memory_error(accepts: bool) -> BaseException:
    client = _client(accepts=accepts)
    with patch.object(lc, "free_memory_verdict", side_effect=_refused()):
        try:
            client._check_free_memory("gemma4-it-e4b-FLM", 65536)
        except lc.LemonadeClientError as e:
            return e
    raise AssertionError("the free-memory check did not fail")


class TestAFreeMemoryReadNobodyAnswersIsServerDown:
    def test_it_is_the_server_down_not_busy(self):
        error = _free_memory_error(accepts=False)
        assert isinstance(error, lc.LemonadeServerDownError)
        typed = classify_lemonade_exception(error)
        assert isinstance(typed, LemonadeServerUnreachableError)
        assert typed.retryable is False
        assert "briefly busy" not in typed.user_message

    def test_flattened_by_the_load_it_is_still_server_down(self):
        error = _free_memory_error(accepts=False)
        flat = RuntimeError(
            "Failed to load model 'gemma4-it-e4b-FLM' on "
            f"http://localhost:13305/api/v1: {error}"
        )
        typed = classify_lemonade_exception(flat)
        assert isinstance(typed, LemonadeServerUnreachableError)
        assert typed.retryable is False

    def test_a_server_that_accepts_connections_keeps_the_old_error(self):
        error = _free_memory_error(accepts=True)
        assert not isinstance(error, lc.LemonadeServerDownError)
        assert "Cannot read free memory" in str(error)


def test_the_memory_notice_names_no_dead_command(caplog):
    def _refuse():
        raise ConnectionError("Connection refused")

    host = SimpleNamespace(
        _probe_embedder=_refuse,
        _embedding_model="nomic-embed-text-v2-moe-GGUF",
        _memory_store=object(),
    )
    with caplog.at_level(logging.WARNING, logger=memory.logger.name):
        assert MemoryMixin._start_memory_embedder(host) is False
    text = caplog.text
    assert "memory v2 disabled" in text
    assert "lemonade-server" not in text
