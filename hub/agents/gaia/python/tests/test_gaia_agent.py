# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Contract tests for the flagship ``gaia`` agent.

The load-bearing one is :func:`test_registers_every_capability_the_starter_skills_need`.
``tools_required`` in a ``SKILL.md`` is advisory — the loader logs at INFO when a
declared tool is missing and loads the skill anyway — so a capability gap here
does not fail at load, it fails mid-run as a broken answer. These assertions are
the only thing standing between a config regression and that failure mode.

Measured, not assumed: ``prompt_profile="doc"`` with ``enable_scratchpad`` and
``enable_browser`` both True registers 38 tools and ZERO of either, because
``ChatAgent._register_tools`` keys off ``ProfileSpec.tool_groups`` and never reads
those flags. That is why the profile is ``"full"``.

Determinism
-----------
``MemoryMixin`` disables itself when the embedder is unreachable, so a plain
construction registers five fewer tools on a machine without Lemonade — an
environment-dependent count that made the drift guard pass on a dev box and fail
in CI, where there is no server. Both surfaces are therefore pinned here rather
than observed: memory init is forced off (``GAIA_MEMORY_DISABLED``), then
registration is re-run with a store present. Cold and warm now agree, so the
only thing that can move these counts is a real config change.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest
import yaml
from gaia_agent import build_gaia
from gaia_agent.agent import GaiaAgent, GaiaAgentConfig

# The canonical name set for the memory surface — hand-copying it here is
# exactly the drift these tests exist to catch.
from gaia.agents.base.memory import _MEMORY_TOOLS
from gaia.agents.base.tools import _TOOL_REGISTRY

MANIFEST = Path(__file__).resolve().parent.parent / "gaia-agent.yaml"


@contextlib.contextmanager
def _isolated_registry():
    """@tool writes into a process-global dict shared with every other agent."""
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    try:
        yield
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


@pytest.fixture(scope="module")
def tool_surfaces():
    """``(core, full)`` tool names — memory off, then memory on."""
    with _isolated_registry(), pytest.MonkeyPatch.context() as mp:
        mp.setenv("GAIA_MEMORY_DISABLED", "1")
        agent = GaiaAgent(config=GaiaAgentConfig(silent_mode=True))
        agent._register_tools()
        core = sorted(agent._tools_registry.keys())

        # A non-None store is the whole of what the registrar gates on, so this
        # exercises the real wiring without an embedder behind it.
        agent._memory_store = object()
        agent._register_tools()
        full = sorted(agent._tools_registry.keys())
    return core, full


@pytest.fixture(scope="module")
def core_tools(tool_surfaces):
    """The surface every install gets, memory available or not."""
    return tool_surfaces[0]


@pytest.fixture(scope="module")
def registered_tools(tool_surfaces):
    """The default construction's full surface, memory included."""
    return tool_surfaces[1]


@pytest.fixture(scope="module")
def manifest():
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Capability breadth — the reason this agent exists
# --------------------------------------------------------------------------

#: Capability -> tool-name substrings, one row per starter-pack consumer.
#: These are checked against the CORE surface: they must survive a config
#: regression on a machine where memory is unavailable.
CAPABILITIES = {
    "rag (document-brief)": ("query_documents", "index_document"),
    "scratchpad (data-explore)": ("create_table", "list_tables"),
    "browser (research-report)": ("fetch_page", "fetch_webpage"),
    "file io (research-report)": ("read_file", "write_file"),
}


@pytest.mark.parametrize("capability,needles", list(CAPABILITIES.items()))
def test_registers_every_capability_the_starter_skills_need(
    capability, needles, core_tools
):
    missing = [n for n in needles if not any(n in t for t in core_tools)]
    assert not missing, (
        f"{capability}: no registered tool matches {missing}. A skill declaring "
        f"these in tools_required would still LOAD (tools_required is advisory) "
        f"and then fail mid-run when the model calls one. Check "
        f"GaiaAgentConfig.prompt_profile is still 'full' — 'doc' silently drops "
        f"scratchpad and browser."
    )


def test_memory_capability_registers_when_memory_is_available(registered_tools):
    """The check-in skill's tools — conditional on memory, so not in CAPABILITIES.

    ``MemoryMixin`` skips registration outright when the embedder is
    unreachable, so ``remember``/``recall`` are not part of the guaranteed
    surface the way RAG or the browser are. What must hold unconditionally is
    the wiring: give this agent a store and the memory tools appear.
    """
    missing = [n for n in ("remember", "recall") if n not in registered_tools]
    assert not missing, (
        f"memory (check-in): {missing} absent even with a live store — "
        f"GaiaAgent's registration path no longer reaches "
        f"MemoryMixin.register_memory_tools, so the check-in skill would load "
        f"(tools_required is advisory) and then fail mid-run."
    )


def test_memory_contributes_exactly_the_memory_tools(core_tools, registered_tools):
    """The 5-tool gap between the two surfaces is memory and nothing else."""
    assert set(registered_tools) - set(core_tools) == set(_MEMORY_TOOLS)


def test_profile_is_full_not_doc():
    """Pin the profile: 'doc' looks right and silently costs 17 tools."""
    assert GaiaAgentConfig().prompt_profile == "full"


# --------------------------------------------------------------------------
# Manifest honesty
# --------------------------------------------------------------------------


def test_manifest_tools_count_matches_real_registry(manifest, registered_tools):
    """The published number describes the agent with memory on.

    That is the state of any install that can actually run it — memory is a
    headline feature and it is on by default. ``core_tools`` (5 fewer) is the
    degraded-embedder floor, not what the hub page should advertise.
    """
    assert manifest["tools_count"] == len(registered_tools), (
        f"gaia-agent.yaml tools_count={manifest['tools_count']} but the real "
        f"registry has {len(registered_tools)}. Hand-typed drift — the hub page "
        f"would over- or under-claim what this agent can do."
    )


def test_registration_tools_count_matches_manifest(manifest):
    assert build_gaia().tools_count == manifest["tools_count"]


def test_registration_identity_matches_manifest(manifest):
    reg = build_gaia()
    assert reg.id == manifest["id"] == "gaia"
    assert reg.category == manifest["category"]
    assert reg.icon == manifest["icon"]


def test_manifest_declares_a_security_tier(manifest):
    """An omitted tier defaults to `experimental`, which makes install refuse
    without an explicit --trust opt-in."""
    assert manifest.get("security_tier") in {"verified", "community", "experimental"}


# --------------------------------------------------------------------------
# Skills wiring
# --------------------------------------------------------------------------


def test_skill_dirs_point_at_the_bundled_library():
    assert GaiaAgent.SKILL_DIRS, "no bundled skill root — SKILL_DIRS is empty"
    assert Path(GaiaAgent.SKILL_DIRS[0]).name == "skills"


def test_skill_manifest_resolves():
    assert GaiaAgent.SKILL_MANIFEST is not None
    assert Path(GaiaAgent.SKILL_MANIFEST).is_file()


def test_no_skill_set_loads_by_default(monkeypatch):
    """#2848 precedent: skill bodies cost prompt tokens and shrink the result
    envelope, so nothing loads until an eval measures the trade."""
    monkeypatch.delenv("GAIA_SKILL_SET", raising=False)
    agent = GaiaAgent.__new__(GaiaAgent)
    agent.config = GaiaAgentConfig()
    assert agent.select_skill_set() is None


def test_env_selects_a_skill_set(monkeypatch):
    monkeypatch.setenv("GAIA_SKILL_SET", "research")
    agent = GaiaAgent.__new__(GaiaAgent)
    agent.config = GaiaAgentConfig()
    assert agent.select_skill_set() == "research"


def test_explicit_config_beats_env(monkeypatch):
    monkeypatch.setenv("GAIA_SKILL_SET", "research")
    agent = GaiaAgent.__new__(GaiaAgent)
    agent.config = GaiaAgentConfig(skill_set="documents")
    assert agent.select_skill_set() == "documents"


def test_manifest_ships_default_skill_set_disabled(manifest):
    """The manifest may declare sets, but none may be active out of the box."""
    assert not manifest.get("default_skill_set"), (
        "default_skill_set is live — skills would load for every user before an "
        "eval has measured their prompt-token cost (see #2848)."
    )


# ---------------------------------------------------------------------------
# Construction contract
# ---------------------------------------------------------------------------


def test_passing_both_a_config_and_kwargs_is_refused():
    """``config or GaiaAgentConfig(**kwargs)`` dropped the kwargs on the floor,
    so a caller that set a field this way got an agent silently ignoring it."""
    with pytest.raises(TypeError) as exc:
        GaiaAgent(config=GaiaAgentConfig(), model_id="some-model")

    message = str(exc.value)
    assert "model_id" in message  # names what would have been dropped
    assert "not both" in message


def test_the_readiness_probe_takes_no_request_parameters():
    """``init(response: Any = None)`` was unused, and FastAPI published it as a
    real query parameter — an argument callers could pass that does nothing."""
    from gaia_agent.server import build_app

    schema = build_app().openapi()
    operation = schema["paths"]["/v1/gaia/init"]["get"]
    assert operation.get("parameters", []) == []


# ---------------------------------------------------------------------------
# Mailbox consent contract
# ---------------------------------------------------------------------------


def _requirement(connector_id):
    return next(
        (cr for cr in GaiaAgent.REQUIRED_CONNECTORS if cr.connector_id == connector_id),
        None,
    )


def test_required_connectors_declare_google_read_only():
    """The consent screen renders this. A first Gmail user sees read, only read."""
    from gaia.agents.tools._email.scopes import (
        SCOPE_GMAIL_MODIFY,
        SCOPE_GMAIL_READONLY,
    )

    google = _requirement("google")
    assert google is not None, "the flagship declares no Google mailbox requirement"
    assert list(google.scopes) == [SCOPE_GMAIL_READONLY]
    assert SCOPE_GMAIL_MODIFY not in google.scopes
    assert "https://mail.google.com/" not in google.scopes


def test_both_mailbox_requirements_say_which_mailbox_they_are_for():
    """`reason` is the sentence shown in the OAuth consent dialog."""
    for connector_id, mailbox in (("google", "gmail"), ("microsoft", "outlook")):
        requirement = _requirement(connector_id)
        assert requirement is not None
        assert mailbox in requirement.reason.lower()


def test_every_declared_scope_has_a_consent_description():
    """A scope with no description renders as a raw URL to the user."""
    from gaia.connectors.providers.google import (
        SCOPE_DESCRIPTIONS as GOOGLE_DESCRIPTIONS,
    )
    from gaia.connectors.providers.microsoft import (
        SCOPE_DESCRIPTIONS as MICROSOFT_DESCRIPTIONS,
    )

    descriptions = {
        "google": GOOGLE_DESCRIPTIONS,
        "microsoft": MICROSOFT_DESCRIPTIONS,
    }
    for requirement in GaiaAgent.REQUIRED_CONNECTORS:
        known = descriptions.get(requirement.connector_id)
        if known is None:
            continue
        missing = [s for s in requirement.scopes if s not in known]
        assert not missing, (
            f"{requirement.connector_id} declares {missing} with no entry in that "
            "provider's SCOPE_DESCRIPTIONS, so the consent dialog would show the "
            "raw scope URL"
        )


def _triples(requirements):
    return sorted((cr.connector_id, tuple(cr.scopes), cr.reason) for cr in requirements)


def test_the_published_registration_declares_both_mailbox_requirements():
    """The registration is what the connectors CLI and the UI read. Publishing
    an empty list made `--grant-agent installed:gaia` refuse to run."""
    published = {cr.connector_id: cr for cr in build_gaia().required_connections}

    from gaia.agents.tools._email.scopes import DECLARED_SCOPES

    assert set(published) == {"google", "microsoft"}
    assert tuple(published["google"].scopes) == DECLARED_SCOPES["google"]
    assert tuple(published["microsoft"].scopes) == DECLARED_SCOPES["microsoft"]


def test_the_registration_and_the_class_cannot_drift():
    """Two hand-maintained copies is how the agent came to ask for one thing and
    publish another; this fails the moment they disagree."""
    assert _triples(build_gaia().required_connections) == _triples(
        GaiaAgent.REQUIRED_CONNECTORS
    )


def test_the_callable_entry_point_publishes_the_requirements_to_the_registry():
    """Through real entry-point discovery, because a stub registry is exactly
    what hid this: the callable form never consulted the agent class."""
    import importlib.metadata

    pytest.importorskip("gaia_agent")
    installed = {ep.name for ep in importlib.metadata.entry_points(group="gaia.agent")}
    if "gaia" not in installed:
        pytest.fail(
            "the 'gaia' entry point is not installed, so this test cannot "
            f"exercise real discovery (group 'gaia.agent' has: {sorted(installed)}). "
            "Install the hub package: pip install -e hub/agents/gaia/python"
        )

    from gaia.agents.registry import AgentRegistry
    from gaia.connectors.api import resolve_declared_scopes

    registry = AgentRegistry()
    registry.discover_installed_agents()

    assert resolve_declared_scopes(registry, "google", ["installed:gaia"]) == {
        "installed:gaia": ["https://www.googleapis.com/auth/gmail.readonly"]
    }
    assert resolve_declared_scopes(registry, "microsoft", ["installed:gaia"]) == {
        "installed:gaia": ["https://graph.microsoft.com/Mail.ReadWrite"]
    }


def test_building_the_registration_does_not_import_the_heavy_agent_module():
    """Discovery runs on every CLI invocation that lists agents, so reading the
    requirements off the agent class would put its whole import cost there."""
    import os
    import subprocess
    import sys

    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p))
    probe = (
        "import sys\n"
        "from gaia_agent import build_gaia\n"
        "build_gaia()\n"
        "print('gaia_agent.agent' in sys.modules)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    assert result.stdout.strip() == "False", result.stderr
