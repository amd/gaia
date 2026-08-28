# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Turning image generation on must not change who the agent thinks it is.

``SDToolsMixin`` ships a ``get_sd_system_prompt`` fragment that opens with
"You are an expert image generation assistant" and runs ~5K chars. Base-agent
prompt composition auto-discovers every ``get_*_system_prompt`` method on the
instance, so once ``init_sd`` runs that fragment lands at the FRONT of the
flagship's prompt — measured at +4,971 chars (a 40% increase) and a persona
that is wrong for every non-image turn.

The capability still has to be reachable, so the fix is not "leave SD off": it
is that the procedure belongs in the ``image-gen`` skill, which renders only on
turns that need it. These tests pin both halves — tools present, persona absent.
"""

from __future__ import annotations

import contextlib

import pytest
from gaia_agent.agent import GaiaAgent, GaiaAgentConfig

from gaia.agents.base.tools import _TOOL_REGISTRY

SD_TOOLS = {"generate_image", "list_sd_models", "get_generation_history"}


@contextlib.contextmanager
def _isolated_registry():
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    try:
        yield
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


@pytest.fixture(scope="module")
def flagship():
    """The built agent, plus a SNAPSHOT of the registry it was built with.

    ``select`` scores against whatever registry it is handed, and sibling test
    modules clear the process-global ``_TOOL_REGISTRY`` in their own fixtures.
    Reading it live makes selection assertions depend on test order — a smaller
    registry lets everything fit under the cap, so a negative assertion passes
    or fails according to what ran first. The snapshot pins it.
    """
    with _isolated_registry(), pytest.MonkeyPatch.context() as mp:
        mp.setenv("GAIA_MEMORY_DISABLED", "1")
        agent = GaiaAgent(config=GaiaAgentConfig(silent_mode=True))
        agent._registry_snapshot = dict(agent._tools_registry)
        yield agent


def _select_fresh(agent, query):
    """First-turn selection for ``query``, independent of test order.

    Skips when the embedder is unreachable: scoring a real query against real
    tool descriptions needs live embeddings, and a plain CI runner has no
    Lemonade. Keyed off the loader's own ``session_disabled`` flag rather than
    a bare ``None``, so a genuine selection regression still fails here.
    """
    agent.tool_loader.reset_session()
    selected = agent.tool_loader.select(query, agent._registry_snapshot)
    if selected is None and agent.tool_loader.session_disabled:
        pytest.skip("semantic tool selection needs a reachable embedder")
    return selected


def test_image_generation_is_reachable_out_of_the_box(flagship):
    """The point of the change: a default flagship can actually draw.

    PR #2995 removed the standalone SD agent on the basis that image
    generation stayed available behind ``enable_sd_tools`` — but nothing
    turned that flag on, so no user could reach it.
    """
    assert GaiaAgentConfig().enable_sd_tools is True
    assert SD_TOOLS <= set(flagship._registry_snapshot)


def test_the_sd_persona_stays_out_of_the_system_prompt(flagship):
    """The regression: SD tools on must not rewrite the agent's identity."""
    prompt = flagship.system_prompt

    assert "expert image generation assistant" not in prompt
    # The distinctive junk from the SD prompt's "proven quality boosters" list;
    # its presence means the whole ~5K fragment came along.
    assert "Aqua Vista" not in prompt


def test_the_capability_is_still_advertised_as_a_bundle(flagship):
    """Suppressing the persona must not make the tools undiscoverable.

    The one-line bundle entry is how a semantic miss is recovered, and it is
    the entire intended prompt cost of this capability.
    """
    assert "- image_gen:" in flagship.system_prompt


@pytest.mark.parametrize(
    "query",
    [
        "draw me a picture of a red bicycle",
        "generate an image of a mountain at sunset",
    ],
)
def test_an_image_request_selects_the_tools_without_any_skill_loaded(flagship, query):
    """Registering the tools is not the same as the model being shown them.

    The flagship runs per-turn semantic tool selection, so a tool outside the
    turn's selected set is invisible to the model. If this fails the capability
    is only reachable through the escape hatch, which the model has to think to
    use.
    """
    selected = _select_fresh(flagship, query)

    assert SD_TOOLS <= set(selected)


def test_a_document_question_does_not_drag_in_the_image_tools(flagship):
    """The other half: breadth must not cost every unrelated turn.

    Must be a FRESH conversation. Selected tools stay loaded across turns
    until the cap evicts them, so asking this after an image request measures
    that (correct) stickiness rather than the first-turn match.
    """
    selected = _select_fresh(flagship, "what does my document say about revenue")

    assert not SD_TOOLS & set(selected)
