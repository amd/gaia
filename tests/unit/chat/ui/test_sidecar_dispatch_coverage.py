# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Every daemon-supervised sidecar must be dispatchable from Agent UI chat.

The regression this guards (#4161): the flagship shipped as a frozen binary
sidecar, the installer bridge registered it in ``AgentRegistry`` with a factory
that raises by design, ``/api/agents`` listed it — and chat dispatch still knew
only about ``email``, so every turn fell through to ``registry.create_agent()``
and surfaced that RuntimeError to the user.

Three sets have to agree, and nothing used to check that they did:

  daemon can supervise it   -> gaia.daemon.sidecars.spec.builtin_specs()
  UI can relay to it        -> gaia.ui.email_sidecar.profiles.SIDECAR_AGENT_IDS
  chat dispatch admits it   -> gaia.ui._chat_helpers._SIDECAR_AGENT_TYPES
"""

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from gaia.agents.registry import AgentRegistration
from gaia.daemon.sidecars.spec import builtin_specs
from gaia.ui._chat_helpers import (
    _SIDECAR_AGENT_TYPES,
    _agent_type_unknown,
    _should_relay_to_sidecar,
)
from gaia.ui.email_sidecar.profiles import SIDECAR_AGENT_IDS, profile_for


class _Registry:
    def __init__(self, reg=None):
        self._reg = reg
        self.load_error = None
        self.agents = []

    def get(self, agent_id):
        return self._reg

    def get_load_error(self, agent_id):
        return self.load_error

    def list(self):
        return self.agents


def _registration(agent_id, *, is_sidecar):
    return AgentRegistration(
        id=agent_id,
        name=agent_id,
        description="",
        source="installed",
        conversation_starters=[],
        factory=lambda **kw: None,
        agent_dir=None,
        models=[],
        is_sidecar=is_sidecar,
    )


@pytest.mark.parametrize("agent_id", sorted(builtin_specs()))
def test_every_supervised_sidecar_has_a_relay_profile(agent_id):
    """A spec the daemon can supervise but the UI cannot relay is listed in
    the agent picker and then fails on the first message."""
    assert profile_for(agent_id) is not None, (
        f"'{agent_id}' is in builtin_specs() but has no RelayProfile, so "
        f"Agent UI chat cannot relay it. Add one in "
        f"gaia/ui/email_sidecar/profiles.py."
    )


@pytest.mark.parametrize("agent_id", sorted(builtin_specs()))
def test_every_supervised_sidecar_is_admitted_by_chat_dispatch(agent_id):
    assert agent_id in _SIDECAR_AGENT_TYPES
    # An installed one is never sent down the in-process registry path, whose
    # factory for a binary install raises by design.
    registry = _Registry(_registration(agent_id, is_sidecar=True))
    assert _agent_type_unknown(agent_id, registry) is False
    assert _should_relay_to_sidecar(agent_id, registry) is True


def test_the_unknown_agent_message_does_not_contradict_itself():
    """An uninstalled flagship is rejected as unknown, so it must not also be
    listed among the ids that resolve."""
    from gaia.ui.routers.sessions import _reject_unknown_agent_type

    registry = _Registry(None)
    registry.agents = []
    with patch("gaia.ui.routers.sessions.get_agent_registry", return_value=registry):
        with pytest.raises(HTTPException) as exc:
            _reject_unknown_agent_type("gaia")

    detail = exc.value.detail
    assert "Unknown agent_type 'gaia'" in detail
    listed = detail.split("Registered agent ids: ")[1].split(".")[0]
    assert "gaia" not in listed, f"named as unknown and registered at once: {listed}"
    # Email still belongs there — its relay needs no registration.
    assert "email" in listed


def test_uninstalled_flagship_says_so_rather_than_failing_at_the_daemon():
    """ "Install it from the Hub" beats whatever the daemon says about being
    asked to start a sidecar that was never installed."""
    assert _agent_type_unknown("gaia", _Registry(None)) is True


def test_email_stays_dispatchable_without_a_registration():
    """Its relay never needed one, and #2109 left it that way."""
    assert _agent_type_unknown("email", _Registry(None)) is False


def test_dispatch_set_is_the_profile_registry():
    """Derived, not a second hand-maintained copy — a parallel literal is how
    the two drifted in the first place."""
    assert _SIDECAR_AGENT_TYPES is SIDECAR_AGENT_IDS


@pytest.mark.parametrize("agent_id", sorted(SIDECAR_AGENT_IDS))
def test_no_profile_without_a_spec(agent_id):
    """The inverse drift: a profile for an agent the daemon cannot start would
    relay to a sidecar that never comes up."""
    assert agent_id in builtin_specs()


# ── Relay vs in-process is an INSTALL-KIND question, not an id question ─────


class TestRelayDecision:
    def test_binary_install_of_the_flagship_relays(self):
        """The stub's factory raises by design — relaying is the only path."""
        registry = _Registry(_registration("gaia", is_sidecar=True))
        assert _should_relay_to_sidecar("gaia", registry) is True

    def test_wheel_install_of_the_flagship_runs_in_process(self):
        """A real factory exists; forcing it through the daemon would break
        the dev/source path and `gaia eval agent`."""
        registry = _Registry(_registration("gaia", is_sidecar=False))
        assert _should_relay_to_sidecar("gaia", registry) is False

    # The two below pin defence in depth, not a reachable path:
    # _agent_type_unknown rejects an unresolvable flagship before dispatch
    # (see test_uninstalled_flagship_says_so_rather_than_failing_at_the_daemon).
    # They keep this function correct on its own terms if that order changes.
    def test_flagship_with_no_registration_would_relay(self):
        # The daemon can fetch and start a sidecar the registry never saw.
        assert _should_relay_to_sidecar("gaia", _Registry(None)) is True

    def test_a_wheel_that_failed_to_import_would_report_that_instead(self):
        """The registry knows WHY it is missing. Relaying would swap that
        answer for whatever the daemon says about a sidecar nobody installed."""
        registry = _Registry(None)
        registry.load_error = "ImportError: No module named 'faiss'"
        assert _should_relay_to_sidecar("gaia", registry) is False

    def test_email_always_relays_even_with_a_real_registration(self):
        """#2109 retired email's in-process loop; the wheel being importable
        must not resurrect it."""
        registry = _Registry(_registration("email", is_sidecar=False))
        assert _should_relay_to_sidecar("email", registry) is True

    def test_non_sidecar_agent_never_relays(self):
        assert _should_relay_to_sidecar("builder", _Registry(None)) is False

    def test_no_registry_at_all_still_relays_a_sidecar(self):
        assert _should_relay_to_sidecar("gaia", None) is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
