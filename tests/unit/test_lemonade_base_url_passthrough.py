# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A configured Lemonade URL must reach the request, not be rebuilt from parts.

Three entry points took `LEMONADE_BASE_URL` apart into host and port and put it
back together as `http://{host}:{port}/api/v1`. An `https://` endpoint was
contacted over plain http, a reverse-proxy path prefix was dropped, and a URL
naming no port acquired 13305 — so a documented remote or tunnelled server was
never actually reached (#3553, #3558).

The second half matters as much as the first: `create_lemonade_client` can
`auto_start`, and that path frees port 13305 by killing whatever is listening
there. Pointed at the wrong machine, "start the server" means "kill something on
the developer's own box".

These assert the URL that would go on the wire, over every shape a real
deployment uses. No network, no Lemonade server.
"""

from __future__ import annotations

import pytest

from gaia.llm.lemonade_client import (
    LemonadeClient,
    create_lemonade_client,
)
from gaia.llm.vlm_client import VLMClient

# (id, configured LEMONADE_BASE_URL, what must survive to the client)
DEPLOYMENTS = [
    (
        "https_tunnel",
        "https://abc123.ngrok.app/api/v1",
        "https://abc123.ngrok.app/api/v1",
    ),
    (
        "reverse_proxy_path_prefix",
        "https://gpu.internal/lemonade/api/v1",
        "https://gpu.internal/lemonade/api/v1",
    ),
    (
        "https_no_explicit_port",
        "https://lemonade.example.com/api/v1",
        "https://lemonade.example.com/api/v1",
    ),
    (
        "plain_host_and_port",
        "http://192.168.1.50:13305/api/v1",
        "http://192.168.1.50:13305/api/v1",
    ),
]

IDS = [case[0] for case in DEPLOYMENTS]
CASES = [(case[1], case[2]) for case in DEPLOYMENTS]


@pytest.fixture
def configured(monkeypatch):
    """Set LEMONADE_BASE_URL and clear the host/port vars that shadow it."""

    def _set(url):
        monkeypatch.setenv("LEMONADE_BASE_URL", url)
        monkeypatch.delenv("LEMONADE_HOST", raising=False)
        monkeypatch.delenv("LEMONADE_PORT", raising=False)
        monkeypatch.delenv("LEMONADE_API_KEY", raising=False)

    return _set


@pytest.mark.parametrize("url,expected", CASES, ids=IDS)
def test_the_client_itself_keeps_the_configured_url(configured, url, expected):
    """The baseline the other two entry points were supposed to match."""
    configured(url)
    assert LemonadeClient(verbose=False).base_url == expected


@pytest.mark.parametrize("url,expected", CASES, ids=IDS)
def test_the_public_factory_keeps_the_configured_url(configured, url, expected):
    configured(url)
    assert create_lemonade_client(verbose=False).base_url == expected


@pytest.mark.parametrize("url,expected", CASES, ids=IDS)
def test_the_vlm_client_keeps_the_configured_url(configured, url, expected):
    configured(url)
    assert VLMClient().client.base_url == expected


class TestWhatMustStillWork:
    """The rebuild-from-parts path is still right when parts are what you pass."""

    def test_an_explicit_host_overrides_the_environment(self, configured):
        configured("https://tunnel.example.com/api/v1")
        client = create_lemonade_client(host="10.0.0.7", verbose=False)
        assert client.base_url == "http://10.0.0.7:13305/api/v1"

    def test_an_explicit_port_overrides_the_environment(self, configured):
        configured("https://tunnel.example.com/api/v1")
        client = create_lemonade_client(port=9999, verbose=False)
        assert client.base_url.endswith(":9999/api/v1")

    def test_host_and_port_env_vars_still_work_without_a_base_url(self, monkeypatch):
        monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
        monkeypatch.setenv("LEMONADE_HOST", "10.1.1.1")
        monkeypatch.setenv("LEMONADE_PORT", "8080")
        assert (
            create_lemonade_client(verbose=False).base_url
            == "http://10.1.1.1:8080/api/v1"
        )


class TestTheVlmClientsUserFacingUrl:
    """`server_url` is quoted in "make sure Lemonade is running at …" messages."""

    def test_it_names_the_real_server_not_a_rebuilt_localhost(self, configured):
        configured("https://gpu.internal/lemonade/api/v1")
        assert VLMClient().server_url == "https://gpu.internal/lemonade"

    def test_it_drops_only_the_api_version_suffix(self, configured):
        configured("http://192.168.1.50:13305/api/v1")
        assert VLMClient().server_url == "http://192.168.1.50:13305"


# ============================================================================
# The embedded server: a port chosen at start time, not a constant
# ============================================================================


class TestGaiasOwnEmbeddedServerIsFound:
    """`gaia lemonade embedded` binds a dynamic port and records it.

    Passing ``host``/``port`` unconditionally pinned every caller to
    localhost:13305, so the embedded server was unreachable — and its
    generated API key, which is keyed to the recorded port, did not resolve
    either (#3558).
    """

    @pytest.fixture
    def embedded(self, monkeypatch):
        monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
        monkeypatch.delenv("LEMONADE_HOST", raising=False)
        monkeypatch.delenv("LEMONADE_PORT", raising=False)
        monkeypatch.setattr(
            "gaia.llm.lemonade_client._embedded_lemonade_url",
            lambda: "http://localhost:50725/api/v1",
        )

    def test_the_public_factory_finds_it(self, embedded):
        client = create_lemonade_client(verbose=False)
        assert client.base_url == "http://localhost:50725/api/v1"

    def test_the_vlm_client_finds_it(self, embedded):
        assert VLMClient().client.base_url == "http://localhost:50725/api/v1"

    def test_a_named_host_still_wins_over_it(self, embedded):
        client = create_lemonade_client(host="10.0.0.7", verbose=False)
        assert client.base_url == "http://10.0.0.7:13305/api/v1"


# ============================================================================
# Launching is a local act, so it cannot serve a remote client
# ============================================================================


class TestStartingAServerIsRefusedForARemoteTarget:
    """auto_start frees the local port and starts a local process.

    Aimed at a remote URL that happens to be unreachable, that killed
    whatever was on the developer's own port 13305 and started a server the
    client would never talk to (#3558).
    """

    @pytest.mark.parametrize(
        "url",
        [
            "https://gpu.internal/api/v1",
            "http://192.168.1.50:13305/api/v1",
            "https://abc123.ngrok.app/api/v1",
        ],
    )
    def test_a_remote_client_refuses_to_launch(self, configured, url):
        configured(url)
        client = LemonadeClient(verbose=False)

        with pytest.raises(Exception, match="not on this machine"):
            client.launch_server()

    def test_the_refusal_names_the_configured_server(self, configured):
        configured("https://gpu.internal/api/v1")
        client = LemonadeClient(verbose=False)

        with pytest.raises(Exception, match="gpu.internal"):
            client.launch_server()

    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:13305/api/v1",
            "http://127.0.0.1:13305/api/v1",
        ],
    )
    def test_a_local_client_is_not_blocked(self, configured, url):
        """It gets past the guard — the launch itself needs real tooling."""
        configured(url)
        client = LemonadeClient(verbose=False)

        assert client._targets_this_machine() is True
