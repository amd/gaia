# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A stored ``chat``/``doc``/``file`` session reopens on the flagship (#6099).

ChatAgent folded into the flagship package, so its old ids are registry
aliases of ``gaia``. On a binary install the flagship runs as a daemon
sidecar, so the alias must reach the relay too: a legacy session that stayed
in-process would hit the sidecar stand-in's factory, which refuses to build.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.daemon.sidecars.errors import SidecarError
from gaia.ui import _chat_helpers as helpers
from gaia.ui.email_sidecar.profiles import (
    EMAIL_PROFILE,
    GAIA_PROFILE,
    profile_for,
)

LEGACY_IDS = ["chat", "doc", "file", "chat-lite", "doc-lite", "file-lite", "gaia-lite"]


class _Registry:
    """Just the lookups dispatch makes, with the real alias semantics."""

    def __init__(self, regs):
        from gaia.agents.registry import AgentRegistry

        self._regs = regs
        # pylint: disable-next=protected-access
        self._aliases = AgentRegistry._LEGACY_ID_ALIASES

    def get(self, agent_id):
        if agent_id in self._regs:
            return self._regs[agent_id]
        return self._regs.get(self._aliases.get(agent_id, agent_id))

    def get_load_error(self, _agent_id):
        return None


@pytest.mark.parametrize("legacy", LEGACY_IDS)
def test_a_legacy_id_gets_the_flagship_relay_profile(legacy):
    assert profile_for(legacy) is GAIA_PROFILE


@pytest.mark.parametrize("other", ["chat2", "Chat", "", "data", "chat-lite-x"])
def test_ids_that_only_look_legacy_get_no_profile(other):
    assert profile_for(other) is None


def test_real_profiles_are_unchanged():
    assert profile_for("gaia") is GAIA_PROFILE
    assert profile_for("email") is EMAIL_PROFILE


@pytest.mark.parametrize("legacy", LEGACY_IDS)
def test_a_legacy_session_relays_when_the_flagship_is_a_sidecar(legacy):
    registry = _Registry({"gaia": SimpleNamespace(is_sidecar=True)})
    assert helpers._should_relay_to_sidecar(legacy, registry) is True


@pytest.mark.parametrize("legacy", LEGACY_IDS)
def test_a_legacy_session_stays_in_process_with_the_flagship_wheel(legacy):
    registry = _Registry({"gaia": SimpleNamespace(is_sidecar=False)})
    assert helpers._should_relay_to_sidecar(legacy, registry) is False


def test_an_agent_registered_under_a_legacy_id_is_not_relayed():
    """A real ``doc`` registration wins over the alias; it runs in-process
    even when the flagship itself is a sidecar."""
    registry = _Registry(
        {
            "gaia": SimpleNamespace(is_sidecar=True),
            "doc": SimpleNamespace(is_sidecar=False),
        }
    )
    assert helpers._should_relay_to_sidecar("doc", registry) is False


@pytest.mark.parametrize("legacy", ["chat", "doc-lite"])
def test_the_relay_asks_the_daemon_for_the_flagship_not_the_legacy_id(legacy):
    asked = []

    def acquire(agent_id):
        asked.append(agent_id)
        raise SidecarError("not running")

    handler = MagicMock()
    with patch("gaia.ui.email_sidecar.daemon_client.acquire_handle", acquire):
        helpers._dispatch_sidecar_query(
            handler,
            SimpleNamespace(message="hi", session_id="s1"),
            [],
            None,
            legacy,
            session_id="s1",
        )

    assert asked == ["gaia"]
    (event,), _ = handler._emit.call_args
    assert event["type"] == "agent_error"
