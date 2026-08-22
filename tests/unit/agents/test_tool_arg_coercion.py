# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A model that sends "120" for an int parameter must still be understood.

JSON tool-call arguments arrive as whatever the model emitted, and models
routinely quote numbers. Nothing converted them, so the raw string reached the
tool body and failed wherever it was first used as a number.

Observed on the flagship: asked to build a PDF, it called
``execute_python_file(timeout="120")``. That reached
``subprocess.run(timeout="120")``, which fails inside the stdlib with
``unsupported operand type(s) for +: 'float' and 'str'`` — a message with no
mention of the tool, the argument, or the agent. The agent read it as a bug in
the *script it was running* and told the user their code was broken. The real
fault never appeared in the transcript at all.

Coercion happens once, in the dispatcher, so no tool has to defend itself. A
value that genuinely cannot be converted is refused with an actionable error
rather than passed through to fail somewhere deeper.
"""

from __future__ import annotations

import inspect

import pytest

from gaia.agents.base.agent import Agent


def _sig(func):
    return inspect.signature(func)


class _Host(Agent):
    def _get_system_prompt(self):
        return "test"

    def _register_tools(self):
        pass


@pytest.fixture
def host():
    return _Host.__new__(_Host)


def sample(path: str, timeout: int = 60, ratio: float = 1.0, force: bool = False):
    """Stand-in for a real tool signature: one of each coercible kind."""


class TestNumbersArriveAsStrings:
    """The reported failure, and its neighbours."""

    def test_the_execute_python_file_case(self, host):
        args, err = host._coerce_tool_args("t", _sig(sample), {"timeout": "120"})
        assert err is None
        assert args["timeout"] == 120
        assert isinstance(args["timeout"], int)

    def test_float_parameter(self, host):
        args, err = host._coerce_tool_args("t", _sig(sample), {"ratio": "0.25"})
        assert err is None and args["ratio"] == pytest.approx(0.25)

    def test_surrounding_whitespace_is_tolerated(self, host):
        args, err = host._coerce_tool_args("t", _sig(sample), {"timeout": " 120 "})
        assert err is None and args["timeout"] == 120

    def test_an_int_written_as_a_float_string(self, host):
        args, err = host._coerce_tool_args("t", _sig(sample), {"timeout": "120.0"})
        assert err is None and args["timeout"] == 120

    @pytest.mark.parametrize(
        "raw,expected",
        [("true", True), ("True", True), ("1", True), ("false", False), ("no", False)],
    )
    def test_booleans(self, host, raw, expected):
        args, err = host._coerce_tool_args("t", _sig(sample), {"force": raw})
        assert err is None and args["force"] is expected

    def test_a_number_for_a_string_parameter(self, host):
        args, err = host._coerce_tool_args("t", _sig(sample), {"path": 42})
        assert err is None and args["path"] == "42"


class TestAlreadyCorrectValuesAreUntouched:
    def test_nothing_changes_when_types_already_match(self, host):
        original = {"path": "/tmp/x", "timeout": 30, "ratio": 0.5, "force": True}
        args, err = host._coerce_tool_args("t", _sig(sample), dict(original))
        assert err is None and args == original

    def test_unannotated_and_unknown_parameters_pass_through(self, host):
        def loose(a, **kwargs):
            pass

        args, err = host._coerce_tool_args("t", _sig(loose), {"a": "1", "b": "2"})
        assert err is None and args == {"a": "1", "b": "2"}

    def test_none_is_left_alone(self, host):
        args, err = host._coerce_tool_args("t", _sig(sample), {"timeout": None})
        assert err is None and args["timeout"] is None


class TestUnconvertibleValuesAreRefusedNotPassedOn:
    """Fail loudly at the boundary, not deep inside a stdlib call."""

    def test_a_word_where_a_number_belongs(self, host):
        args, err = host._coerce_tool_args("t", _sig(sample), {"timeout": "soon"})
        assert err is not None
        assert "timeout" in err and "int" in err
        assert args == {"timeout": "soon"}, "args must not be half-converted"

    def test_a_fraction_for_a_whole_number(self, host):
        _, err = host._coerce_tool_args("t", _sig(sample), {"timeout": "1.5"})
        assert err is not None and "whole number" in err

    def test_a_flag_confused_for_a_count(self, host):
        """True would coerce to 1 in Python — that hides a real model error."""
        _, err = host._coerce_tool_args("t", _sig(sample), {"timeout": True})
        assert err is not None and "boolean" in err

    def test_a_word_where_a_boolean_belongs(self, host):
        _, err = host._coerce_tool_args("t", _sig(sample), {"force": "maybe"})
        assert err is not None and "force" in err

    def test_every_problem_is_reported_not_just_the_first(self, host):
        _, err = host._coerce_tool_args(
            "t", _sig(sample), {"timeout": "soon", "ratio": "lots"}
        )
        assert "timeout" in err and "ratio" in err


class TestTheDispatcherActuallyCallsIt:
    def test_execute_tool_coerces_before_dispatch(self):
        """Dispatch is split across a timing wrapper and the impl it delegates to.

        Reading only one half would pass while the coercion sat in the other, so
        assert the delegation too — otherwise splitting the method again silently
        turns this into a test of the wrong function.
        """
        wrapper = inspect.getsource(Agent._execute_tool)
        impl = inspect.getsource(Agent._execute_tool_impl)
        assert (
            "_execute_tool_impl" in wrapper
        ), "_execute_tool no longer delegates to _execute_tool_impl"
        assert (
            "_coerce_tool_args" in impl
        ), "coercion is not wired into the dispatch path, so no tool benefits"
        assert impl.index("_coerce_tool_args") < impl.index(
            "_call_tool_bounded"
        ), "arguments must be fitted before the tool runs, not after"
