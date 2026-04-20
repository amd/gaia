# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""``ReviewToolsMixin`` — one ``@tool`` per pass plus the one-shot gate.

The coder agent composes this mixin into her tool set so the seven-pass
gate is addressable as a tool call (``review_diff_gate``) and each
individual pass is addressable for partial / debug runs
(``review_diff_self_static``, etc.).

Call pattern::

    from gaia.coder.review import ReviewToolsMixin

    m = ReviewToolsMixin()
    m.register_review_tools()
    # Now the tool registry has 8 new entries — 7 individual passes plus
    # review_diff_gate.

Every tool accepts ``pr_or_branch`` as its only required arg and returns
either a :class:`gaia.coder.review.pass_result.PassResult` or a
:class:`gaia.coder.review.gate.GateResultDict` depending on which tool was
called. The TypedDict return shapes mean the agent can feed the result
into downstream tool calls without custom serialisation.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

# Direct submodule imports avoid the circular-import hazard with
# ``gaia.coder.review.__init__`` (which itself imports from this module).
import gaia.coder.review.pass_1_static as pass_1_static
import gaia.coder.review.pass_2_functional as pass_2_functional
import gaia.coder.review.pass_3_architectural as pass_3_architectural
import gaia.coder.review.pass_4_security as pass_4_security
import gaia.coder.review.pass_5_prose as pass_5_prose
import gaia.coder.review.pass_6_adversarial as pass_6_adversarial
import gaia.coder.review.pass_7_feedback_binding as pass_7_feedback_binding
from gaia.agents.base.tools import tool
from gaia.coder.review.gate import GateResultDict, run_all_passes
from gaia.coder.review.pass_result import PassResult

logger = logging.getLogger(__name__)


class ReviewToolsMixin:
    """Mixin exposing the seven review passes + the gate as agent tools."""

    def register_review_tools(self) -> None:
        """Register ``review_diff_self_*`` and ``review_diff_gate``.

        Registration defines eight tools in the agent tool registry:

        * ``review_diff_self_static``
        * ``review_diff_self_functional``
        * ``review_diff_self_architectural``
        * ``review_diff_self_security``
        * ``review_diff_self_prose``
        * ``review_diff_self_adversarial``
        * ``review_diff_self_feedback_binding``
        * ``review_diff_gate`` — the one-shot entry that runs all applicable
          passes and returns an aggregated :class:`GateResultDict`.

        The ``base_ref`` and ``repo_root`` parameters on the individual
        passes are kept as agent-visible kwargs so the EM can diagnose a
        gate rejection by rerunning one pass by hand via the TUI.
        """

        @tool
        def review_diff_self_static(
            pr_or_branch: str,
            base_ref: str = "coder",
            repo_root: Optional[str] = None,
        ) -> PassResult:
            """Pass 1 — deterministic lint / tsc / regex guards (§8 row 1)."""
            return pass_1_static.run_pass(
                pr_or_branch,
                base_ref=base_ref,
                repo_root=Path(repo_root) if repo_root else None,
            )

        @tool
        def review_diff_self_functional(
            pr_or_branch: str,
            base_ref: str = "coder",
            repo_root: Optional[str] = None,
        ) -> PassResult:
            """Pass 2 — pytest / npm test / optional mutmut (§8 row 2)."""
            return pass_2_functional.run_pass(
                pr_or_branch,
                base_ref=base_ref,
                repo_root=Path(repo_root) if repo_root else None,
            )

        @tool
        def review_diff_self_architectural(
            pr_or_branch: str,
            base_ref: str = "coder",
            repo_root: Optional[str] = None,
        ) -> PassResult:
            """Pass 3 — LLM architectural review via Opus 4.7 (§8 row 3)."""
            return pass_3_architectural.run_pass(
                pr_or_branch,
                base_ref=base_ref,
                repo_root=Path(repo_root) if repo_root else None,
            )

        @tool
        def review_diff_self_security(
            pr_or_branch: str,
            base_ref: str = "coder",
            repo_root: Optional[str] = None,
        ) -> PassResult:
            """Pass 4 — gitleaks / AST scan / pip-audit / npm audit (§8 row 4)."""
            return pass_4_security.run_pass(
                pr_or_branch,
                base_ref=base_ref,
                repo_root=Path(repo_root) if repo_root else None,
            )

        @tool
        def review_diff_self_prose(
            pr_or_branch: str,
            base_ref: str = "coder",
            repo_root: Optional[str] = None,
        ) -> PassResult:
            """Pass 5 — PR prose + persona linter (§8 row 5)."""
            return pass_5_prose.run_pass(
                pr_or_branch,
                base_ref=base_ref,
                repo_root=Path(repo_root) if repo_root else None,
            )

        @tool
        def review_diff_self_adversarial(
            pr_or_branch: str,
            base_ref: str = "coder",
            repo_root: Optional[str] = None,
            success_criterion: Optional[str] = None,
        ) -> PassResult:
            """Pass 6 — adversarial fresh-context Opus review (§8 row 6)."""
            return pass_6_adversarial.run_pass(
                pr_or_branch,
                base_ref=base_ref,
                repo_root=Path(repo_root) if repo_root else None,
                success_criterion=success_criterion,
            )

        @tool
        def review_diff_self_feedback_binding(
            pr_or_branch: str,
            base_ref: str = "coder",
            repo_root: Optional[str] = None,
        ) -> PassResult:
            """Pass 7 — feedback binding for self-fix PRs (§8 row 7)."""
            return pass_7_feedback_binding.run_pass(
                pr_or_branch,
                base_ref=base_ref,
                repo_root=Path(repo_root) if repo_root else None,
            )

        @tool
        def review_diff_gate(
            pr_or_branch: str,
            is_self_fix: bool = False,
            base_ref: str = "coder",
            repo_root: Optional[str] = None,
            success_criterion: Optional[str] = None,
        ) -> GateResultDict:
            """Run all applicable review passes and return the aggregated verdict.

            This is the tool the coder agent calls *before* invoking
            ``gh_pr_create``. If ``overall`` is not ``"pass"`` the agent
            must revise before opening the PR per §8.
            """
            return run_all_passes(
                pr_or_branch,
                is_self_fix=is_self_fix,
                base_ref=base_ref,
                repo_root=Path(repo_root) if repo_root else None,
                success_criterion=success_criterion,
            ).as_dict()


__all__ = ["ReviewToolsMixin"]
