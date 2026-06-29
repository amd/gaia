# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for python_factory kwarg filtering (issue #973).

The UI's session layer injects four session-context kwargs (``rag_documents``,
``library_documents``, ``allowed_paths``, ``ui_session_id``) into every
``registry.create_agent(...)`` call. Built-in factories filter against
``ChatAgentConfig`` dataclass fields. Before the fix, ``python_factory``
forwarded everything blindly, so any custom Python agent inheriting only the
base ``Agent`` (which has no ``**kwargs`` in its ``__init__``) crashed with
``TypeError: Agent.__init__() got an unexpected keyword argument 'rag_documents'``.

These tests exercise:
1. ``_accepted_init_params`` directly — fast, edge-case-focused unit tests
   that don't touch the registry or disk.
2. ``python_factory`` indirectly via ``registry._load_python_agent`` with a
   class written to ``tmp_path`` — the integration path that produces the
   actual closure used in production.
"""

import logging
import textwrap
from dataclasses import dataclass
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.registry import (
    _SECURITY_RELEVANT_KWARGS,
    AgentRegistry,
    _accepted_init_params,
)

# ---------------------------------------------------------------------------
# Test isolation: clear sys.modules cache for dynamically loaded agent
# modules so reordering tests cannot leak state through ``importlib``.
# Same pattern as tests/unit/agents/test_registry.py.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _purge_custom_agent_modules():
    import sys

    before = {k for k in sys.modules if k.startswith("gaia_custom_agent_")}
    yield
    after = {k for k in sys.modules if k.startswith("gaia_custom_agent_")}
    for name in after - before:
        sys.modules.pop(name, None)


# ---------------------------------------------------------------------------
# _accepted_init_params: direct helper tests
# ---------------------------------------------------------------------------


class TestAcceptedInitParams:
    """Direct tests of the introspection helper.

    No registry, no disk, no agent imports — just ``inspect.signature`` over
    synthetic class hierarchies. Fast and edge-case-focused.
    """

    def test_bare_base_agent_excludes_session_kwargs(self):
        """A class inheriting Agent with no own __init__ accepts only the
        base Agent params — rag_documents and friends are NOT in the set.
        """

        class Bare(Agent):
            AGENT_ID = "test/bare"
            AGENT_NAME = "Bare"
            AGENT_DESCRIPTION = "test"
            CONVERSATION_STARTERS = []

            def _get_system_prompt(self):
                return ""

            def _register_tools(self):
                pass

        accepted = _accepted_init_params(Bare)
        assert accepted is not None
        assert "model_id" in accepted  # base Agent has model_id
        assert "skip_lemonade" in accepted  # base Agent has skip_lemonade
        assert "rag_documents" not in accepted
        assert "ui_session_id" not in accepted
        assert "allowed_paths" not in accepted

    def test_declaring_agent_includes_extra_param(self):
        """A class that explicitly declares rag_documents in its __init__
        gets it in accepted; the filter will pass it through.
        """

        class Declaring(Agent):
            AGENT_ID = "test/declaring"
            AGENT_NAME = "Declaring"
            AGENT_DESCRIPTION = "test"
            CONVERSATION_STARTERS = []

            def __init__(self, rag_documents=None, **kwargs):
                self._rag_docs = rag_documents
                super().__init__(**kwargs)

            def _get_system_prompt(self):
                return ""

            def _register_tools(self):
                pass

        accepted = _accepted_init_params(Declaring)
        assert accepted is not None
        assert "rag_documents" in accepted

    def test_full_passthrough_returns_none(self):
        """If every __init__ in the chain accepts **kwargs, return None
        (caller forwards everything as-is). Synthetic chain — no Agent base.
        """

        class A:
            def __init__(self, **kwargs):
                pass

        class B(A):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)

        assert _accepted_init_params(B) is None

    def test_positional_only_excluded(self):
        """PEP 570 positional-only params can't be passed by keyword. They
        MUST be excluded — otherwise klass(**filtered) would replace one
        TypeError with a different TypeError ("X is positional-only").
        """

        class PosOnly:
            def __init__(self, x, /, **kwargs):
                self.x = x

        accepted = _accepted_init_params(PosOnly)
        # x is positional-only, not keyword-passable. NOT in accepted.
        # Since the level has **kwargs, accepted is the union of the chain.
        # PosOnly's __init__ has VAR_KEYWORD, so all_inspected_levels_have_var_keyword
        # would be True if it were the only level — but object.__init__ is
        # excluded, so this is the only level. Should return None (passthrough).
        # That's correct: when the class accepts **kwargs, we pass everything;
        # the user is responsible for handling positional-only correctly.
        assert accepted is None

    def test_positional_only_with_strict_base_excluded(self):
        """When a strict base (no **kwargs) restricts the chain, positional-only
        params from a subclass must NOT enter the keyword-passable set.
        """

        class StrictBase:
            def __init__(self, foo=None):
                self.foo = foo

        class WithPosOnly(StrictBase):
            def __init__(self, x, /, foo=None):
                super().__init__(foo=foo)

        accepted = _accepted_init_params(WithPosOnly)
        assert accepted is not None
        assert "foo" in accepted
        assert "x" not in accepted  # positional-only, must be excluded

    def test_dataclass_init_collects_fields(self):
        """A @dataclass-generated __init__ has POSITIONAL_OR_KEYWORD params
        for each field; the helper picks them up correctly.
        """

        @dataclass
        class DataclassAgent:
            foo: int = 0
            bar: str = "x"

        accepted = _accepted_init_params(DataclassAgent)
        assert accepted is not None
        assert "foo" in accepted
        assert "bar" in accepted

    def test_c_extension_init_returns_none(self):
        """If inspect.signature raises (as it can on C-extension __init__),
        the helper returns None (permissive — don't claim to know what the
        level accepts).
        """

        class StubClass:
            pass

        # Patch inspect.signature inside the registry module to raise.
        with patch(
            "gaia.agents.registry.inspect.signature",
            side_effect=ValueError("no signature"),
        ):
            # Give StubClass an __init__ so the loop body executes.
            StubClass.__init__ = lambda self: None
            assert _accepted_init_params(StubClass) is None

    def test_no_init_in_mro_returns_empty_set(self):
        """A class whose entire MRO inherits object.__init__ (no own __init__
        anywhere) returns set(): filter to empty so klass() is called bare.
        Otherwise we'd fall through to "forward all" and object.__init__
        would raise on extra kwargs.
        """

        class A:
            pass  # uses object.__init__

        class B(A):
            pass

        assert _accepted_init_params(B) == set()

    def test_mixin_chain_unions_params(self):
        """When a mixin declares a kwarg that the leaf class doesn't, the
        union across MRO levels still includes the mixin's param.
        """

        class Mixin:
            def __init__(self, mixin_arg=None, **kwargs):
                self.mixin_arg = mixin_arg
                super().__init__(**kwargs)

        class Base:
            def __init__(self, base_arg=None):
                self.base_arg = base_arg

        class Combined(Mixin, Base):
            pass

        accepted = _accepted_init_params(Combined)
        assert accepted is not None
        assert "mixin_arg" in accepted
        assert "base_arg" in accepted


# ---------------------------------------------------------------------------
# python_factory: integration tests via _load_python_agent + create_agent
# ---------------------------------------------------------------------------


# The exact session-kwarg set the UI injects per
# gaia.ui._chat_helpers._session_agent_kwargs (plus the model_id from
# _build_create_kwargs). Reproducing the literal production failure.
_PRODUCTION_KWARGS = {
    "model_id": "Gemma-4-E4B-it-GGUF",
    "rag_documents": ["doc1.pdf"],
    "library_documents": ["lib1.pdf"],
    "allowed_paths": ["/tmp/allowed"],
    "ui_session_id": "session-abc123",
}


def _write_minimal_agent(tmp_path, agent_id="test/minimal"):
    """Write a minimal custom agent to ``tmp_path`` and return the dir.

    Reproduces the failing pattern: bare ``super().__init__(**kwargs)``
    with no per-kwarg declaration.
    """
    agent_dir = tmp_path / "minimal-agent"
    agent_dir.mkdir()
    (agent_dir / "agent.py").write_text(textwrap.dedent(f"""
            from gaia.agents.base.agent import Agent

            class MinimalAgent(Agent):
                AGENT_ID = "{agent_id}"
                AGENT_NAME = "Minimal"
                AGENT_DESCRIPTION = "Reproduces issue #973"
                CONVERSATION_STARTERS = []

                def __init__(self, **kwargs):
                    super().__init__(**kwargs)

                def _get_system_prompt(self):
                    return "test"

                def _register_tools(self):
                    from gaia.agents.base.tools import _TOOL_REGISTRY
                    _TOOL_REGISTRY.clear()
            """))
    return agent_dir


class TestPythonFactoryFiltering:
    """Integration tests that exercise the actual ``python_factory`` closure
    via ``_load_python_agent``."""

    def test_production_kwargs_no_typeerror(self, tmp_path):
        """The literal failing case: production-exact kwargs reach
        create_agent, the bare-super agent constructs successfully.
        """
        agent_dir = _write_minimal_agent(tmp_path, "test/prod-973")
        registry = AgentRegistry()
        registry._load_python_agent(agent_dir, agent_dir / "agent.py", None)

        # Add skip_lemonade so we don't try to talk to a real LLM server.
        kwargs = dict(_PRODUCTION_KWARGS)
        kwargs["skip_lemonade"] = True

        agent = registry.create_agent("test/prod-973", **kwargs)

        assert agent is not None
        # model_id passed through (it's in base Agent's signature)
        assert agent.model_id == "Gemma-4-E4B-it-GGUF"

    def test_unknown_kwargs_logged_at_debug(self, tmp_path, caplog):
        """Non-security dropped kwargs log at DEBUG level and the agent
        still constructs (not just "no exception")."""
        agent_dir = _write_minimal_agent(tmp_path, "test/debug-log")
        registry = AgentRegistry()
        registry._load_python_agent(agent_dir, agent_dir / "agent.py", None)

        with caplog.at_level(logging.DEBUG, logger="gaia.agents.registry"):
            agent = registry.create_agent(
                "test/debug-log",
                rag_documents=["a.pdf"],
                library_documents=["b.pdf"],
                ui_session_id="x",
                skip_lemonade=True,
            )

        assert agent is not None
        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
        drop_msgs = [
            r.getMessage() for r in debug_records if "dropped" in r.getMessage()
        ]
        assert any("rag_documents" in m for m in drop_msgs)
        assert any("library_documents" in m for m in drop_msgs)
        assert any("ui_session_id" in m for m in drop_msgs)

    def test_allowed_paths_drop_logs_at_warning(self, tmp_path, caplog):
        """Security-relevant kwargs (``allowed_paths``) log at WARNING when
        dropped, naming the kwarg and the class so the author has a visible
        signal to declare it. Per CLAUDE.md "no silent fallbacks".
        """
        agent_dir = _write_minimal_agent(tmp_path, "test/sec-warn")
        registry = AgentRegistry()
        registry._load_python_agent(agent_dir, agent_dir / "agent.py", None)

        with caplog.at_level(logging.WARNING, logger="gaia.agents.registry"):
            registry.create_agent(
                "test/sec-warn",
                allowed_paths=["/tmp/sandbox"],
                skip_lemonade=True,
            )

        warning_msgs = [
            r.getMessage()
            for r in caplog.records
            if r.levelno == logging.WARNING and "security-relevant" in r.getMessage()
        ]
        assert len(warning_msgs) >= 1
        assert "allowed_paths" in warning_msgs[0]
        assert "MinimalAgent" in warning_msgs[0]

    def test_declared_session_kwargs_pass_through_no_warning(self, tmp_path, caplog):
        """An agent that declares allowed_paths and rag_documents in its own
        __init__ receives those kwargs and the security warning does NOT fire.
        """
        agent_dir = tmp_path / "declaring-agent"
        agent_dir.mkdir()
        (agent_dir / "agent.py").write_text(textwrap.dedent("""
                from gaia.agents.base.agent import Agent

                class DeclaringAgent(Agent):
                    AGENT_ID = "test/declaring"
                    AGENT_NAME = "Declaring"
                    AGENT_DESCRIPTION = "Declares session kwargs"
                    CONVERSATION_STARTERS = []

                    def __init__(
                        self, rag_documents=None, allowed_paths=None, **kwargs
                    ):
                        self.declared_rag = rag_documents
                        self.declared_allowed = allowed_paths
                        super().__init__(**kwargs)

                    def _get_system_prompt(self):
                        return "test"

                    def _register_tools(self):
                        from gaia.agents.base.tools import _TOOL_REGISTRY
                        _TOOL_REGISTRY.clear()
                """))
        registry = AgentRegistry()
        registry._load_python_agent(agent_dir, agent_dir / "agent.py", None)

        with caplog.at_level(logging.WARNING, logger="gaia.agents.registry"):
            agent = registry.create_agent(
                "test/declaring",
                rag_documents=["a.pdf"],
                allowed_paths=["/tmp/sandbox"],
                skip_lemonade=True,
            )

        assert agent.declared_rag == ["a.pdf"]
        assert agent.declared_allowed == ["/tmp/sandbox"]
        # No security warning — allowed_paths was accepted, not dropped.
        sec_warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "security-relevant" in r.getMessage()
        ]
        assert sec_warnings == []

    def test_security_relevant_kwargs_constant_includes_allowed_paths(self):
        """Sanity check on the constant: ``allowed_paths`` must be flagged
        as security-relevant. If this set ever shrinks, that's a deliberate
        decision — make the test fail so the change is reviewed.
        """
        assert "allowed_paths" in _SECURITY_RELEVANT_KWARGS


# ---------------------------------------------------------------------------
# Built-in chat factory regression
# ---------------------------------------------------------------------------


class TestChatFactoryRegression:
    """The fix in ``python_factory`` MUST NOT touch ``chat_factory``.
    Built-in chat goes through its own factory which filters via
    ``dataclasses.fields(ChatAgentConfig)`` — that path is unchanged.
    """

    def test_chat_agent_config_declares_session_fields(self):
        """``ChatAgentConfig`` exposes the four UI-injected session kwargs
        plus ``model_id`` as dataclass fields — that's the contract
        ``chat_factory`` filters against. If a refactor ever drops one of
        these fields, ``chat_factory`` would silently stop forwarding it
        and built-in chat would lose session context.
        """
        import dataclasses as _dc

        pytest.importorskip("gaia_agent_chat")
        from gaia_agent_chat.agent import ChatAgentConfig

        valid_fields = {f.name for f in _dc.fields(ChatAgentConfig)}
        assert "rag_documents" in valid_fields
        assert "library_documents" in valid_fields
        assert "allowed_paths" in valid_fields
        assert "ui_session_id" in valid_fields
        assert "model_id" in valid_fields

    def test_chat_factory_filters_unknown_kwargs_via_create_agent(self):
        """End-to-end exercise of ``chat_factory`` through the registry:
        unknown kwargs are filtered out (no TypeError) and known session
        kwargs reach the constructed agent. This is the actual regression
        guard for AC item "built-in chat continues to receive its session
        kwargs unchanged".
        """
        pytest.importorskip("gaia_agent_chat")
        registry = AgentRegistry()
        registry.discover()

        # Exercise the literal production-injection path: model_id +
        # session kwargs + an unknown kwarg the factory must drop.
        agent = registry.create_agent(
            "chat",
            model_id="Qwen3-0.6B-GGUF",
            rag_documents=["doc.pdf"],
            library_documents=[],
            allowed_paths=["/tmp"],
            ui_session_id="session-xyz",
            this_kwarg_does_not_exist="bogus",
            skip_lemonade=True,
        )

        assert agent is not None
        # ChatAgent stores its config; verify the session kwargs reached it.
        assert agent.config.rag_documents == ["doc.pdf"]
        assert agent.config.allowed_paths == ["/tmp"]
        assert agent.config.ui_session_id == "session-xyz"
        assert agent.config.model_id == "Qwen3-0.6B-GGUF"
