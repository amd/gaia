# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for ``gaia_agent_email.tools.text_signals`` (#2581).

Covers:
- The module stays a dependency-free leaf: zero ``gaia_agent_email`` imports,
  so anything (including ``triage_heuristics.py``, itself a deliberate
  zero-internal-import leaf) can import it without closing an import cycle.
- ``has_direct_ask_signal`` is phrasing-based, not punctuation-based: a bare
  ``?`` alone does not qualify, and genuine ask phrasing qualifies even with
  no ``?`` at all.
- ``has_meeting_time_signal`` catches the #2580 incident wording ("any
  chance to meet this Thursday at 9am") — added because the existing
  calendar heuristic (``detect_meeting_request_heuristic``) did not catch
  that phrasing at the time this predicate was written. #2583 has since
  taught the calendar heuristic a separate invite phrase that also catches
  it; this suite asserts only what ``has_meeting_time_signal`` itself does,
  not whether the calendar heuristic can or can't see the same text (both
  detecting it is fine — see ``test_incident_wording_qualifies`` below).
  It still requires a concrete time token (not a bare "chance to meet").
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

import gaia_agent_email.tools.text_signals as text_signals  # noqa: E402
from gaia_agent_email.tools.text_signals import (  # noqa: E402
    has_direct_ask_signal,
    has_meeting_time_signal,
)


class TestLeafInvariant:
    """``triage_heuristics.py`` is a deliberate import leaf (zero internal
    imports); ``text_signals.py`` must stay leaf-shaped too so anything —
    including a future leaf module — can use these predicates without
    closing an import cycle."""

    def test_module_has_no_gaia_agent_email_imports(self):
        src = Path(text_signals.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("gaia_agent_email"):
                    offenders.append(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("gaia_agent_email"):
                        offenders.append(alias.name)
        assert offenders == [], (
            f"text_signals.py must import nothing from gaia_agent_email "
            f"(found: {offenders}) — it must stay a dependency-free leaf"
        )

    def test_module_source_has_no_gaia_import_at_all(self):
        # Belt-and-suspenders textual check in addition to the AST walk.
        src = Path(text_signals.__file__).read_text(encoding="utf-8")
        assert not re.search(
            r"^\s*(from|import)\s+gaia\b", src, re.MULTILINE
        ), "text_signals.py must not import from the gaia package at all"


class TestDirectAskSignal:
    def test_bare_question_mark_alone_does_not_qualify(self):
        assert has_direct_ask_signal("", "is this the best deal ever?") is False

    def test_genuine_ask_phrasing_with_no_question_mark_qualifies(self):
        assert (
            has_direct_ask_signal("", "please confirm the numbers by friday.") is True
        )

    def test_incident_style_direct_ask_qualifies(self):
        assert (
            has_direct_ask_signal(
                "", "did you get a chance to look at the proposal I sent over?"
            )
            is True
        )

    def test_marketing_copy_with_question_mark_does_not_qualify(self):
        assert (
            has_direct_ask_signal(
                "50% off ends today!",
                "are you ready to save big on your next purchase?",
            )
            is False
        )

    def test_case_insensitivity_handled_by_caller_lowercasing(self):
        # The predicate expects already-lowercased input; verify a
        # lowercased match works (documents the contract).
        assert has_direct_ask_signal("", "could you please confirm?".lower()) is True


class TestMeetingTimeSignal:
    def test_incident_wording_qualifies(self):
        assert (
            has_meeting_time_signal("", "any chance to meet this thursday at 9am?")
            is True
        )

    def test_bare_meeting_verb_without_time_does_not_qualify(self):
        assert has_meeting_time_signal("", "let's meet up sometime soon") is False

    def test_unrelated_text_does_not_qualify(self):
        assert has_meeting_time_signal("", "the meeting notes are attached") is False
