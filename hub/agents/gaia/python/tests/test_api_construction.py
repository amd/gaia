# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Contract gate between ``gaia.api.agent_registry`` and this agent's constructor.

``AgentRegistry.get_agent`` is the only way an OpenAI-compatible client reaches
the flagship, and it builds it blind: every key of ``AGENT_MODELS["gaia"]
["init_params"]``, plus an ``output_handler`` it adds itself, goes straight in as
a keyword argument. ``GaiaAgent`` funnels those into the ``GaiaAgentConfig``
dataclass, so a keyword the dataclass does not declare is a ``TypeError`` raised
per request — ``/v1/chat/completions`` 500s for every caller while the rest of
the suite stays green. ``output_handler`` exists on ``ChatAgentConfig`` for no
reason other than this call site.

``get_agent`` is therefore called here rather than re-implemented: a local copy
of its body would pin ``AGENT_MODELS`` against the constructor but not the
caller, and the next keyword added to ``init_params`` would slip through the
same gap this file exists to close.
"""

from __future__ import annotations

import contextlib
import importlib
from dataclasses import fields

import pytest
from gaia_agent.agent import GaiaAgent, GaiaAgentConfig

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.api.agent_registry import AGENT_MODELS, AgentRegistry, _apply_env_overrides
from gaia.api.sse_handler import SSEOutputHandler

MODEL_ID = "gaia"


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


def test_get_agent_builds_the_flagship_and_installs_the_sse_handler():
    """Drive the real registry, then assert the handler actually took effect.

    Construction not raising is only half of it: ``silent_mode=True`` also ships
    in ``init_params``, and if it won over ``output_handler`` the agent would
    stream to a plain console while the API looked perfectly healthy.
    """
    # Memory init is forced off for the same reason as the sibling tests: no
    # embedder in CI, and MemoryMixin's self-disable makes the surface differ
    # between a dev box and a runner.
    with _isolated_registry(), pytest.MonkeyPatch.context() as mp:
        mp.setenv("GAIA_MEMORY_DISABLED", "1")
        agent = AgentRegistry().get_agent(MODEL_ID)

    assert isinstance(agent, GaiaAgent)
    assert isinstance(agent.console, SSEOutputHandler), (
        "get_agent's output_handler did not reach the agent's console — "
        "GaiaAgentConfig/ChatAgentConfig must declare an output_handler field "
        "and ChatAgent must forward it to Agent.__init__."
    )


def test_registry_class_path_resolves_to_this_agent():
    """The other half of the contract: the dotted path the registry imports.

    ``get_agent`` folds a stale path's ``ImportError`` into a generic "agent not
    available" ``ValueError``, so a package or class rename surfaces as a
    runtime 500 rather than anything CI notices.
    """
    class_path = AGENT_MODELS[MODEL_ID]["class_name"]
    module_path, class_name = class_path.rsplit(".", 1)
    resolved = getattr(importlib.import_module(module_path), class_name, None)

    assert resolved is GaiaAgent, (
        f"AGENT_MODELS['{MODEL_ID}']['class_name'] is {class_path!r}, which no "
        "longer resolves to gaia_agent.agent.GaiaAgent."
    )


def test_debug_env_overrides_stay_within_the_config_fields():
    """``gaia api start --debug`` injects extra keywords; they must be fields too.

    ``_apply_env_overrides`` adds ``debug``/``silent_mode``/``show_prompts``/
    ``streaming`` to ``init_params`` when the API server is started with those
    flags. They reach the constructor exactly like ``output_handler`` does, so
    that path deserves the same gate — it only runs at import, which is why it
    is re-invoked here instead of monkeypatching the environment alone.
    """
    declared = {f.name for f in fields(GaiaAgentConfig)}
    assert "output_handler" in declared

    init_params = AGENT_MODELS[MODEL_ID]["init_params"]
    saved = dict(init_params)
    try:
        with pytest.MonkeyPatch.context() as mp:
            for var in (
                "GAIA_API_DEBUG",
                "GAIA_API_SHOW_PROMPTS",
                "GAIA_API_STREAMING",
            ):
                mp.setenv(var, "1")
            _apply_env_overrides()
        applied = dict(init_params)
        undeclared = sorted(set(applied) - declared)
    finally:
        init_params.clear()
        init_params.update(saved)

    # Asserted by VALUE, not by key presence. ``streaming`` and ``silent_mode``
    # are already keys of AGENT_MODELS' init_params, so every key-based check —
    # a diff against the pre-call state or a subset test — stays green when
    # GAIA_API_STREAMING stops being read. Only the value moves.
    expected = {
        "debug": True,
        "show_prompts": True,
        "streaming": True,
        "silent_mode": False,
    }
    dead = sorted(k for k, v in expected.items() if applied.get(k) != v)
    assert not dead, (
        f"_apply_env_overrides did not apply every flag: {dead}. Expected "
        f"{expected}, got { {k: applied.get(k) for k in expected} }."
    )
    assert not undeclared, (
        f"init_params keys absent from GaiaAgentConfig: {undeclared}. get_agent "
        "passes each one as a keyword, so this is a TypeError per request."
    )
