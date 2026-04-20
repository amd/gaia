# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Phase 1 skeleton tests for ``gaia.coder``.

These tests pin the public surface downstream sibling branches rely on:

* The ``gaia-coder`` console script runs and shows its subcommand list.
* ``from gaia.coder import CoderAgent`` works.
* ``from gaia.coder.loop import DEFAULT_LOOP`` works.
* The default loop has exactly the 20 states defined in §15.3 of the
  spec, including the updated three-way ``self_review`` transition
  (``publish`` | ``debug`` | ``edit``).
* :func:`gaia.coder.loop.introspect_state_machine` returns a Mermaid
  render (any of the ``graph TD`` or ``stateDiagram`` dialects satisfy
  §7.7).
"""

from __future__ import annotations

import subprocess
import sys


def test_cli_help_runs() -> None:
    """``gaia-coder --help`` must exit 0 and list subcommands."""
    # Invoke via `python -m gaia.coder.cli` so the test works whether or
    # not the ``gaia-coder`` console script has been installed. This is
    # the same invariant either route exercises.
    result = subprocess.run(
        [sys.executable, "-m", "gaia.coder.cli", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"gaia-coder --help exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    # A handful of subcommands must appear in the help output.
    for expected in ("daemon", "status", "introspect", "trust"):
        assert expected in result.stdout, (
            f"Expected subcommand {expected!r} missing from help output:\n"
            f"{result.stdout}"
        )


def test_package_imports() -> None:
    """Public re-exports must resolve from the top-level package."""
    from gaia.coder import CoderAgent  # noqa: F401  (import is the assertion)
    from gaia.coder.loop import DEFAULT_LOOP  # noqa: F401

    # Sanity: ``CoderAgent`` is constructible without arguments.
    agent = CoderAgent()
    assert agent.loop is DEFAULT_LOOP


def test_default_loop_has_20_states() -> None:
    """§15.3: the default loop has exactly 20 states."""
    from gaia.coder.loop import DEFAULT_LOOP

    assert (
        len(DEFAULT_LOOP.states) == 20
    ), f"DEFAULT_LOOP has {len(DEFAULT_LOOP.states)} states, expected 20"


def test_self_review_has_three_transitions() -> None:
    """§15.3: ``self_review`` transitions are publish | debug | edit."""
    from gaia.coder.loop import DEFAULT_LOOP

    self_review = DEFAULT_LOOP.state_by_name("self_review")
    targets = [t.to for t in self_review.transitions]

    assert len(self_review.transitions) == 3, (
        f"self_review should have exactly 3 transitions, found "
        f"{len(self_review.transitions)}: {targets}"
    )
    assert set(targets) == {"publish", "debug", "edit"}, (
        f"self_review targets should be {{publish, debug, edit}}, got "
        f"{set(targets)}"
    )


def test_introspect_state_machine_returns_mermaid() -> None:
    """The introspection helper must return a Mermaid-compatible string.

    §7.7 specifies the tool renders both JSON and a Mermaid diagram for
    the EM's inspection. The dialect (``graph TD`` vs ``stateDiagram``)
    is an implementation detail; the test accepts either.
    """
    from gaia.coder.loop import introspect_state_machine

    payload = introspect_state_machine()

    assert isinstance(payload, dict)
    assert "json" in payload
    assert "mermaid" in payload

    mermaid = payload["mermaid"]
    assert isinstance(mermaid, str)
    assert "graph TD" in mermaid or "stateDiagram" in mermaid, (
        "Mermaid render should use either `graph TD` or `stateDiagram` "
        f"dialect; got:\n{mermaid[:200]}"
    )
