# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The "Lemonade is not running" remedies point at commands that work.

`gaia init` installs GAIA's own Lemonade, so a missing server is fixed by
`gaia init`, never by a trip to lemonade-server.ai. These messages print after
auto-start already failed, so they must not promise it will happen.
"""

from unittest.mock import MagicMock, patch

import pytest

from gaia.llm.lemonade_client import LemonadeClient
from gaia.llm.lemonade_launcher import StartHint
from gaia.llm.lemonade_manager import LemonadeManager

START = "GAIA starts its Lemonade Server when it needs it. To start it now, run: x"


def _assert_no_dead_remedy(text: str) -> None:
    assert "lemonade-server.ai" not in text
    assert "lemonade-server" not in text
    assert "automatically start" not in text


@pytest.fixture
def start_hint():
    with patch(
        "gaia.llm.lemonade_manager.describe_start_hint",
        return_value=StartHint(instruction=START),
    ):
        yield


def test_not_installed_points_at_gaia_init(capsys, start_hint):
    with patch.object(LemonadeManager, "is_lemonade_installed", return_value=False):
        LemonadeManager.print_server_error()

    err = capsys.readouterr().err
    assert "gaia init" in err
    _assert_no_dead_remedy(err)


def test_installed_but_stopped_prints_the_resolved_start_hint(capsys, start_hint):
    with patch.object(LemonadeManager, "is_lemonade_installed", return_value=True):
        LemonadeManager.print_server_error()

    err = capsys.readouterr().err
    assert START in err
    _assert_no_dead_remedy(err)


def test_gaias_own_server_counts_as_installed(monkeypatch):
    monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
    with (
        patch(
            "gaia.llm.lemonade_embedded.EmbeddedLemonade.is_installed",
            return_value=True,
        ),
        patch("gaia.llm.lemonade_manager.LemonadeClient") as client_cls,
    ):
        assert LemonadeManager.is_lemonade_installed() is True
    client_cls.assert_not_called()


@pytest.fixture
def no_configured_url(monkeypatch):
    monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
    monkeypatch.delenv("GAIA_LEMONADE_EMBEDDED", raising=False)


def _down_client():
    """A client whose server is not answering, on a host with no system Lemonade."""
    client = LemonadeClient(verbose=False)
    client.health_check = MagicMock(side_effect=ConnectionError("down"))
    no_tooling = patch(
        "gaia.llm.lemonade_client.resolve_lemonade",
        return_value=MagicMock(found=False),
    )
    return client, no_tooling


def test_client_counts_gaias_own_server_as_installed(no_configured_url):
    client, no_tooling = _down_client()
    with (
        no_tooling,
        patch(
            "gaia.llm.lemonade_embedded.EmbeddedLemonade.is_installed",
            return_value=True,
        ),
    ):
        assert client._check_lemonade_installed() is True


def test_client_ignores_gaias_own_server_when_another_is_configured(monkeypatch):
    monkeypatch.delenv("GAIA_LEMONADE_EMBEDDED", raising=False)
    monkeypatch.setenv("LEMONADE_BASE_URL", "http://10.0.0.5:8000/api/v1")
    client, no_tooling = _down_client()
    with (
        no_tooling,
        patch(
            "gaia.llm.lemonade_embedded.EmbeddedLemonade.is_installed",
            return_value=True,
        ),
    ):
        assert client._check_lemonade_installed() is False


def test_client_initialize_unreachable_remote_is_not_called_uninstalled(
    capsys, monkeypatch
):
    monkeypatch.delenv("GAIA_LEMONADE_EMBEDDED", raising=False)
    monkeypatch.setenv("LEMONADE_BASE_URL", "http://10.0.0.5:8000/api/v1")
    client = LemonadeClient(verbose=False)
    with patch.object(client, "_check_lemonade_installed", return_value=False):
        status = client.initialize(agent="chat")

    out = capsys.readouterr().out
    assert status.running is False
    assert status.error == (
        "Lemonade Server at http://10.0.0.5:8000/api/v1 not reachable"
    )
    assert "not installed" not in out
    assert "gaia init" not in out
    _assert_no_dead_remedy(out)


def test_client_initialize_not_installed_points_at_gaia_init(capsys, no_configured_url):
    client = LemonadeClient(verbose=False)
    with patch.object(client, "_check_lemonade_installed", return_value=False):
        status = client.initialize(agent="chat")

    out = capsys.readouterr().out
    assert status.running is False
    assert "gaia init" in out
    _assert_no_dead_remedy(out)
