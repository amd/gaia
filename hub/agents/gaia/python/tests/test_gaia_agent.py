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
# The fast conversational path (#4103)
# ---------------------------------------------------------------------------

#: The acceptance bar from #4103: a conversational turn on the flagship must
#: cost no more than ~2x what the retired ``chat`` agent charged for one, so
#: dropping the ``chat`` agent id costs users no speed. ``chat`` measures 1,484
#: tiktoken (cl100k) tokens of fixed prefill on this same construction.
#:
#: This is a ceiling, not a pin. A prompt edit that moves the number by fifty
#: tokens is fine; one that moves it by a thousand has given fast mode a tool
#: surface or a capability section back, which is the regression.
FAST_PREFILL_CEILING = 2500


@contextlib.contextmanager
def _agent(env=None, **overrides):
    """A real agent, with memory pinned off and the tool registry isolated.

    Memory off is the determinism pin the module docstring explains: a
    reachable embedder adds five tools and a memory prompt block, so a count
    taken on a dev box would not match CI. ``chat``'s 1,484-token reference was
    measured the same way, so the ratio is like-for-like.

    Yields inside the isolated registry rather than returning, because
    ``ChatAgent`` does not snapshot tools on the bare conversational profile —
    ``_tools_registry`` reads the process-global dict, which is restored the
    moment the context exits.
    """
    with _isolated_registry(), pytest.MonkeyPatch.context() as mp:
        mp.setenv("GAIA_MEMORY_DISABLED", "1")
        mp.delenv("GAIA_FAST", raising=False)
        for key, value in (env or {}).items():
            mp.setenv(key, value)
        yield GaiaAgent(config=GaiaAgentConfig(silent_mode=True, **overrides))


def _fixed_prefill(agent):
    """Tokens re-read on every LLM call of a turn: system prompt + tool schemas.

    cl100k_base is a tokenizer-agnostic proxy, not Gemma's tokenizer — the
    ratio between configurations is what this measures, not an exact count.
    """
    import json as _json

    tiktoken = pytest.importorskip("tiktoken")
    enc = tiktoken.get_encoding("cl100k_base")
    return len(enc.encode(agent._compose_system_prompt())) + len(
        enc.encode(_json.dumps(agent._openai_tools or []))
    )


def test_default_construction_is_not_fast():
    """Fast mode is opt-in. A default flagship keeps its whole surface."""
    assert GaiaAgentConfig().fast is False
    assert GaiaAgentConfig().prompt_profile == "full"


def test_fast_mode_registers_only_the_conversational_surface():
    """The whole saving is here: the full surface collapses to a handful.

    ``prompt_profile="chat"`` alone used to leave 19 registered, because this
    agent's own extras — skill library, skill learning, code index, email —
    ran before ``super()._register_tools()`` and never read the profile. That
    absence is the invariant; the exact list below is ChatAgent's bare profile
    and moves when ChatAgent does, which is a pin worth updating, not a bug.
    """
    with _agent(fast=True) as agent:
        registered = sorted(agent._tools_registry)
    assert registered == [
        "read_tool_output",
        "run_shell_command",
        "search_documentation",
    ]
    # The 17 this agent adds on every other profile. Any one of them back means
    # _profile_registers_tools stopped gating and the prefill budget is gone.
    for extra in (
        "list_skills",
        "remember_skill_lesson",
        "index_codebase",
        "list_inbox",
    ):
        assert extra not in registered


def test_fast_mode_meets_the_conversational_prefill_budget():
    """#4103's acceptance criterion, measured rather than argued."""
    with _agent(fast=True) as agent:
        prefill = _fixed_prefill(agent)
    assert prefill <= FAST_PREFILL_CEILING, (
        f"fast mode costs {prefill} tiktoken tokens of fixed prefill, over the "
        f"{FAST_PREFILL_CEILING} budget from #4103. Something gave it a tool "
        f"surface or a capability prompt section back — check that "
        f"_apply_fast_mode still clears the enable_* flags and that "
        f"_profile_registers_tools still gates this agent's own registrations."
    )


def test_fast_mode_costs_a_fraction_of_the_default():
    """Guards the claim, not just the absolute number."""
    with _agent(fast=True) as agent:
        fast = _fixed_prefill(agent)
    with _agent() as agent:
        default = _fixed_prefill(agent)
    assert fast * 4 < default, (
        f"fast={fast} vs default={default} — fast mode is supposed to be the "
        f"reason a user would accept losing documents, files and the web for a "
        f"session. At this margin it is not worth the trade."
    )


def test_fast_mode_keeps_the_voice():
    """The persona is the one thing fast mode must NOT trade away.

    ``gaia-voice`` is loaded from the manifest's always-on ``skills:`` list and
    is what stops the agent claiming work it did not do. Per-turn skill
    selection is off in fast mode precisely so a greeting — the turn a semantic
    chooser scores lowest — cannot drop it.
    """
    with _agent(fast=True) as agent:
        assert "gaia-voice" in (agent.loaded_skills or [])
        assert "gaia-voice" in agent._compose_system_prompt()


def test_fast_mode_does_not_advertise_tools_it_removed():
    """Prompt text and tool registration move together, or the model is lied to.

    The ``enable_*`` flags add prompt sections and register nothing, so leaving
    them on would describe filesystem, scratchpad and browser tooling that fast
    mode does not carry — tokens spent inviting a call that fails.

    Two base mixins (file editing, vision) still render their prompt block
    unconditionally, so they are not asserted here. That predates this change
    and the ``chat`` agent carries both today; fixing it edits a prompt every
    shipped agent sees, which needs an eval run this change did not have.
    """
    with _agent(fast=True) as agent:
        prompt = agent._compose_system_prompt()
    for absent in ("create_table", "fetch_page", "query_documents"):
        assert absent not in prompt, (
            f"fast-mode prompt still names {absent!r}, which is not registered. "
            f"The model will call it and get an unknown-tool error."
        )


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_env_turns_fast_mode_on(value):
    with _agent(env={"GAIA_FAST": value}) as agent:
        assert agent.config.fast is True
        assert agent.config.prompt_profile == "chat"


def test_env_turns_fast_mode_off_again():
    """``GAIA_FAST`` wins in both directions, like ``GAIA_DYNAMIC_TOOLS``.

    A launcher that hard-codes ``fast=True`` must stay overridable from the
    shell, or a user who needs their documents back has to edit code.
    """
    with _agent(env={"GAIA_FAST": "0"}, fast=True) as agent:
        assert agent.config.fast is False
        assert "query_documents" in agent._tools_registry


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
