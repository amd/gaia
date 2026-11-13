# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A metadata host the web client blocks stays blocked for every tool that turn.

When ``fetch_page`` refuses a cloud-metadata or link-local address, the
refusal is recorded as a NETWORK effect. A private LAN address it refuses is
not carried over: the shell may still reach a NAS or a dev box. A shell ``curl``/``wget``/
``Invoke-WebRequest`` or a Python ``urllib`` call to that address — in any
spelling of it — is then refused, even under full access. No request is made
here: the web client and the shell/Python tools are doubles.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.denied_effects import (
    NETWORK,
    DeniedEffects,
    Effect,
    canonical_host,
    effects_of_call,
    is_cloud_metadata_host,
)
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.agents.tools.browser_tools import BrowserToolsMixin
from gaia.web.client import BlockedHostError, _assert_ip_allowed

METADATA = "169.254.169.254"


@pytest.mark.parametrize(
    "spelling",
    [
        "169.254.169.254",
        "2852039166",
        "0xA9FEA9FE",
        "0xa9fea9fe",
        "0251.0376.0251.0376",
        "0xa9.0xfe.0xa9.0xfe",
        "169.254.43518",
        "169.16689662",
        "[::ffff:169.254.169.254]",
        "[::FFFF:A9FE:A9FE]",
        "::ffff:a9fe:a9fe",
        "169.254.169.254.",
    ],
)
def test_every_spelling_of_an_address_is_one_host(spelling):
    assert canonical_host(spelling) == METADATA


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Example.COM.", "example.com"),
        ("metadata.google.internal", "metadata.google.internal"),
        ("[FE80::1]", "fe80::1"),
        ("08.1.1.1", "08.1.1.1"),
        ("1.2.3.4.5", "1.2.3.4.5"),
        ("256.1.1.1", "256.1.1.1"),
    ],
)
def test_names_and_non_addresses_keep_their_spelling(name, expected):
    assert canonical_host(name) == expected


def test_the_web_client_says_which_host_it_blocked():
    with pytest.raises(BlockedHostError) as caught:
        _assert_ip_allowed(METADATA, "metadata.example")

    assert caught.value.host == "metadata.example"
    assert caught.value.address == METADATA
    assert isinstance(caught.value, ValueError)


def _ledger_after_block(tmp_path, host=METADATA, address=METADATA):
    ledger = DeniedEffects(str(tmp_path))
    ledger.record(
        "fetch_page",
        {"url": f"http://{host}/latest/meta-data/"},
        "Blocked",
        kinds={NETWORK},
        refused=True,
        hosts=[host, address],
    )
    return ledger


SAME_HOST_CALLS = [
    ("run_shell_command", {"command": "curl http://169.254.169.254/latest/"}),
    ("run_shell_command", {"command": "curl -s http://2852039166/latest/"}),
    ("run_shell_command", {"command": "curl 0xA9FEA9FE/latest/meta-data"}),
    ("run_shell_command", {"command": "curl 169.254.169.254"}),
    ("run_shell_command", {"command": "wget -qO- http://[::ffff:169.254.169.254]/"}),
    ("run_shell_command", {"command": "wget http://0251.0376.0251.0376/x"}),
    ("run_shell_command", {"command": "iwr http://169.254.169.254/ -UseBasicParsing"}),
    (
        "run_shell_command",
        {"command": 'powershell -Command "Invoke-WebRequest -Uri http://2852039166/"'},
    ),
    (
        "run_shell_command",
        {"command": "Invoke-RestMethod -Uri HTTP://0XA9FEA9FE/latest"},
    ),
    (
        "run_python",
        {
            "code": "import urllib.request\n"
            "print(urllib.request.urlopen('http://2852039166/latest').read())"
        },
    ),
    (
        "run_python",
        {"code": "import requests\nrequests.get('http://[::ffff:a9fe:a9fe]/')"},
    ),
    ("fetch_page", {"url": "http://0xa9fea9fe/"}),
]


@pytest.mark.parametrize("tool_name, args", SAME_HOST_CALLS)
def test_a_blocked_host_is_refused_through_every_client(tmp_path, tool_name, args):
    ledger = _ledger_after_block(tmp_path)

    found = ledger.conflict(tool_name, args)

    assert found is not None, args
    denial, _ = found
    assert denial.refused is True


@pytest.mark.parametrize(
    "command",
    [
        "curl https://example.com/",
        "curl -o out.json https://example.com/data",
        "curl --retry 5 https://example.com/",
        "wget -O page.html https://example.com",
        "git status",
        "ls 169",
    ],
)
def test_other_hosts_are_untouched(tmp_path, command):
    ledger = _ledger_after_block(tmp_path)

    assert ledger.conflict("run_shell_command", {"command": command}) is None


def test_a_redirect_refuses_where_it_led_not_where_it_started(tmp_path):
    ledger = DeniedEffects(str(tmp_path))
    ledger.record(
        "fetch_page",
        {"url": "https://public.example/redirect"},
        "Blocked",
        kinds={NETWORK},
        refused=True,
        hosts=["metadata.internal", METADATA],
    )

    assert (
        ledger.conflict(
            "run_shell_command", {"command": "curl https://public.example/"}
        )
        is None
    )
    assert ledger.conflict(
        "run_shell_command", {"command": "curl metadata.internal/computeMetadata"}
    )
    assert ledger.conflict("run_shell_command", {"command": f"curl {METADATA}"})


def test_bare_words_are_not_read_as_hosts():
    effects = effects_of_call("run_shell_command", {"command": "curl -o out.json x"})

    assert Effect(NETWORK, "out.json") not in effects
    assert Effect(NETWORK, "x") not in effects


# --------------------------------------------------------------------------- loop


class _BlockingWebClient:
    """A web client whose every fetch is blocked, as the SSRF guard does."""

    def __init__(self, host, address):
        self.host, self.address = host, address

    def get(self, url):
        raise BlockedHostError(
            f"Blocked: {self.host} resolves to private/reserved IP {self.address}.",
            self.host,
            self.address,
        )


class _WebAgent(Agent, BrowserToolsMixin):
    ran: list = []

    def _get_system_prompt(self):
        return "probe"

    def _register_tools(self):
        self.register_browser_tools()
        ran = self.ran

        @tool
        def run_shell_command(command: str) -> dict:
            """Record the command; nothing is run."""
            ran.append(command)
            return {"status": "success", "stdout": ""}

        @tool
        def run_python(code: str) -> dict:
            """Record the code; nothing is run."""
            ran.append(code)
            return {"status": "success", "stdout": ""}


@pytest.fixture
def web_agent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    saved = dict(_TOOL_REGISTRY)
    _WebAgent.ran = []

    def build(host=METADATA, address=METADATA):
        with patch("gaia.agents.base.agent.AgentSDK"):
            agent = _WebAgent(silent_mode=True, skip_lemonade=True)
        # Full access: every gate open, nobody asked.
        agent.console.full_access = True
        agent.console.auto_approve_gated_tools = True
        agent._web_client = _BlockingWebClient(host, address)
        agent._denied_effects = DeniedEffects(str(tmp_path))
        return agent

    try:
        yield build
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


@pytest.mark.parametrize(
    "tool_name, args",
    [
        ("run_shell_command", {"command": "curl http://2852039166/latest/"}),
        ("run_shell_command", {"command": "wget http://[::ffff:169.254.169.254]/"}),
        (
            "run_python",
            {
                "code": "import urllib.request\n"
                "urllib.request.urlopen('http://0xA9FEA9FE/')"
            },
        ),
    ],
)
def test_full_access_cannot_reach_a_host_fetch_page_blocked(web_agent, tool_name, args):
    agent = web_agent()

    blocked = agent._execute_tool("fetch_page", {"url": f"http://{METADATA}/"})
    rerouted = agent._execute_tool(tool_name, args)

    assert "Blocked" in str(blocked)
    assert rerouted["executed"] is False, rerouted
    assert rerouted["status"] == "denied"
    assert agent.ran == []


def test_an_unrelated_call_still_runs_after_a_block(web_agent):
    agent = web_agent()

    agent._execute_tool("fetch_page", {"url": f"http://{METADATA}/"})
    result = agent._execute_tool(
        "run_shell_command", {"command": "curl https://example.com/"}
    )

    assert result["status"] == "success"
    assert agent.ran == ["curl https://example.com/"]


def test_a_loopback_block_does_not_refuse_the_shell(web_agent):
    agent = web_agent(host="localhost", address="127.0.0.1")

    agent._execute_tool("fetch_page", {"url": "http://localhost:8000/"})
    result = agent._execute_tool(
        "run_shell_command", {"command": "curl http://localhost:8000/health"}
    )

    assert result["status"] == "success"


@pytest.mark.parametrize(
    "host",
    [
        "169.254.169.254",
        "2852039166",
        "0xA9FEA9FE",
        "0251.0376.0251.0376",
        "[::ffff:169.254.169.254]",
        "::ffff:a9fe:a9fe",
        "64:ff9b::a9fe:a9fe",
        "169.254.170.2",
        "fe80::1",
        "[FE80::a9fe:a9fe]",
        "fe80::1%eth0",
        "fd00:ec2::254",
        "[fd00:ec2::254]",
        "metadata.google.internal",
        "METADATA.GOOGLE.INTERNAL.",
        "metadata",
        "100.100.100.200",
    ],
)
def test_metadata_and_link_local_hosts_are_recognised(host):
    assert is_cloud_metadata_host(host) is True


@pytest.mark.parametrize(
    "host",
    [
        "192.168.1.10",
        "10.0.0.5",
        "172.16.4.2",
        "3232235786",
        "[::ffff:192.168.1.10]",
        "fd12:3456::1",
        "127.0.0.1",
        "::1",
        "localhost",
        "nas.local",
        "intranet.corp",
        "example.com",
        "",
    ],
)
def test_lan_and_public_hosts_are_not_metadata(host):
    assert is_cloud_metadata_host(host) is False


@pytest.mark.parametrize(
    "host, address",
    [
        ("nas.local", "192.168.1.10"),
        ("192.168.1.10", "192.168.1.10"),
        ("devbox", "10.0.0.5"),
        ("intranet.corp", "172.16.4.2"),
        ("localhost", "127.0.0.1"),
    ],
)
def test_a_lan_block_does_not_refuse_the_shell(web_agent, host, address):
    agent = web_agent(host=host, address=address)

    blocked = agent._execute_tool("fetch_page", {"url": f"http://{host}/"})
    result = agent._execute_tool(
        "run_shell_command", {"command": f"curl http://{host}/status"}
    )

    assert "Blocked" in str(blocked)
    assert result["status"] == "success", result
    assert agent.ran == [f"curl http://{host}/status"]


@pytest.mark.parametrize(
    "host, address",
    [
        ("metadata.google.internal", "169.254.169.254"),
        ("public.example", "169.254.169.254"),
        ("fd00:ec2::254", "fd00:ec2::254"),
        ("link.example", "fe80::1"),
    ],
)
def test_a_metadata_block_refuses_the_shell(web_agent, host, address):
    agent = web_agent(host=host, address=address)

    agent._execute_tool("fetch_page", {"url": "http://public.example/"})
    target = f"[{address}]" if ":" in address else address
    result = agent._execute_tool(
        "run_shell_command", {"command": f"curl http://{target}/latest/"}
    )

    assert result["executed"] is False, result
    assert agent.ran == []


def test_fetch_page_still_refuses_a_lan_address():
    with pytest.raises(BlockedHostError):
        _assert_ip_allowed("192.168.1.10", "nas.local")
