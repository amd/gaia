# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Reproduce a Hub BINARY install of the flagship, end to end at the seam (#4161).

The reported break, exactly: install gaia v0.24.1, install the GAIA agent from
the Hub, start a chat, and every prompt answers

    Agent error: 'gaia' runs out-of-process as a daemon sidecar; it is not
    instantiated in-process by the AgentRegistry. ...

A binary install ships no importable wheel, so the agent's own registration
never loads; ``register_installed_sidecars`` registers a stand-in whose factory
raises by design; chat dispatch knew only ``email`` and therefore called that
factory. The card was wrong too — no description, no starter prompts, so the UI
offered four generic suggestions that had nothing to do with the agent.

This builds that state from a real ``.installed`` sentinel on disk and asserts
what the user would see, without a wheel anywhere in the picture.
"""

import json

import pytest

from gaia.agents.registry import AgentRegistry
from gaia.daemon.sidecars.spec import builtin_specs
from gaia.hub.installer import register_installed_sidecars
from gaia.ui._chat_helpers import _should_relay_to_sidecar


@pytest.fixture
def binary_installed_registry(tmp_path, monkeypatch):
    """An empty registry after a binary install of the flagship — no wheel."""
    root = tmp_path / "agents"
    install_dir = root / "gaia"
    install_dir.mkdir(parents=True)
    (install_dir / ".installed").write_text(
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
    registry._agents.clear()  # no built-ins, and above all no gaia wheel
    register_installed_sidecars(registry)
    return registry


def test_the_flagship_is_registered_at_all(binary_installed_registry):
    assert binary_installed_registry.get("gaia") is not None


def test_a_chat_turn_relays_instead_of_instantiating(binary_installed_registry):
    """The regression itself. False here is the RuntimeError the user saw."""
    assert _should_relay_to_sidecar("gaia", binary_installed_registry) is True


def test_calling_the_factory_is_still_refused(binary_installed_registry):
    """The stub stays fail-loud — the fix is routing around it, not softening
    it. A caller that reaches here has a routing bug and should be told."""
    with pytest.raises(RuntimeError, match="out-of-process as a daemon sidecar"):
        binary_installed_registry.create_agent("gaia")


def test_the_card_shows_the_agent_not_a_placeholder(binary_installed_registry):
    reg = binary_installed_registry.get("gaia")
    assert reg.name == "GAIA"
    assert reg.description != "GAIA agent", "generated placeholder, not the real line"
    assert reg.description == builtin_specs()["gaia"].description
    assert reg.icon == "sparkles"
    assert reg.category == "general"


def test_the_card_offers_the_agents_own_starters(binary_installed_registry):
    """Empty starters are why the user was shown four generic prompts."""
    starters = binary_installed_registry.get("gaia").conversation_starters
    assert len(starters) == 4
    assert "What can you do?" in starters


def test_it_is_marked_as_a_sidecar(binary_installed_registry):
    assert binary_installed_registry.get("gaia").is_sidecar is True


def test_a_wheel_install_is_not_clobbered_into_a_sidecar(tmp_path, monkeypatch):
    """The idempotence guard: a real registration must survive the bridge, or
    the dev/source path would be forced through the daemon."""
    root = tmp_path / "agents"
    (root / "gaia").mkdir(parents=True)
    (root / "gaia" / ".installed").write_text(
        json.dumps({"id": "gaia", "version": "0.2.0", "artifact_kind": "wheel"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "gaia.hub.installer.default_install_root", lambda: root, raising=False
    )

    registry = AgentRegistry()
    registry._agents.clear()
    from gaia.agents.registry import AgentRegistration

    registry._register(
        AgentRegistration(
            id="gaia",
            name="GAIA",
            description="real one",
            source="installed",
            conversation_starters=["hi"],
            factory=lambda **kw: "an actual agent",
            agent_dir=None,
            models=[],
        )
    )
    register_installed_sidecars(registry)

    assert registry.get("gaia").description == "real one"
    assert registry.get("gaia").is_sidecar is False
    assert _should_relay_to_sidecar("gaia", registry) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
