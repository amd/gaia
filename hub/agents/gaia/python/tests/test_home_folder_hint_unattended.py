# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A refused home folder names no folder when nobody on the sidecar run can approve it.

The sidecar's ``/query`` handler can carry a confirmation only for a session
whose caller can answer. On any other run the agent's next request for a
named folder ends the run, so the refusal must not point at one.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("gaia_agent")

from gaia_agent.server import QueryRequest, _declare_who_answers  # noqa: E402

from gaia.agents.base.agent import Agent  # noqa: E402
from gaia.agents.tools.filesystem_tools import FileSystemToolsMixin  # noqa: E402
from gaia.security import PathValidator  # noqa: E402
from gaia.ui.sse_handler import SSEOutputHandler  # noqa: E402


class _BrowseAgent(Agent, FileSystemToolsMixin):
    def __init__(self, scope, console):
        self.path_validator = PathValidator(allowed_paths=[str(scope)])
        with patch("gaia.agents.base.agent.AgentSDK"):
            super().__init__(
                silent_mode=True, skip_lemonade=True, output_handler=console
            )

    def _get_system_prompt(self) -> str:
        return "browse"

    def _resolve_skill_manifest(self, explicit=None):
        # This module sits under the flagship package, whose manifest isn't ours.
        return None

    def _register_tools(self) -> None:
        self.register_filesystem_tools()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "gaia.security._system_temp_roots", lambda: {str(tmp_path / "systemp")}
    )
    home = tmp_path / "home"
    (home / "Movies").mkdir(parents=True)
    scope = tmp_path / "scope"
    scope.mkdir()
    return home, scope


def _sidecar_handler(*, session, can_answer_questions):
    """The handler the sidecar's ``/query`` builds, told who can answer."""
    handler = SSEOutputHandler()
    request = QueryRequest(
        query="q",
        run_id=str(uuid.uuid4()),
        context=[],
        can_answer_questions=can_answer_questions,
    )
    _declare_who_answers(handler, session, request)
    return handler


def _refusal_names_movies(home, scope, console):
    agent = _BrowseAgent(scope, console)
    # Only the refusal sees the fake home; the agent loads its skills first.
    with patch.object(Path, "home", classmethod(lambda cls: home)):
        refused = json.dumps(
            agent._execute_tool("browse_directory", {"path": str(home)})
        )
    assert "too broad" in refused
    return json.dumps(str(home / "Movies"))[1:-1] in refused


@pytest.mark.parametrize(
    "session, can_answer_questions",
    [(None, None), (object(), False), (None, True)],
    ids=["sessionless", "cannot_answer", "sessionless_even_if_watched"],
)
def test_an_unattended_sidecar_run_is_offered_no_folder(
    home, session, can_answer_questions
):
    console = _sidecar_handler(
        session=session, can_answer_questions=can_answer_questions
    )

    assert console.answers_confirmations is False
    assert not _refusal_names_movies(*home, console)


def test_an_attended_sidecar_run_is_still_offered_the_folder(home):
    console = _sidecar_handler(session=object(), can_answer_questions=True)

    assert console.nobody_answers is False
    assert _refusal_names_movies(*home, console)
