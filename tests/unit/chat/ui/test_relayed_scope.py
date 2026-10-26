# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``GAIA_ALLOWED_PATHS`` and the scope a chat's agent actually gets.

The Electron app starts the backend in the user's home folder, so a chat with
no documents falls back to GAIA's own documents folder. Intersected with the
folder the user set in ``GAIA_ALLOWED_PATHS`` that left nothing at all: the
log said the setting "confines file access to []". A relayed chat also never
sent its scope to the sidecar, and the session panel showed a scope that was
never narrowed.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import gaia.ui._chat_helpers as chat_helpers
from gaia.security import ALLOWED_PATHS_ENV, AllowedPathsEnvError, PathValidator
from gaia.ui import session_environment
from gaia.ui.database import ChatDatabase
from gaia.ui.email_sidecar import daemon_client, relay
from gaia.ui.email_sidecar.profiles import (
    EMAIL_PROFILE,
    GAIA_PROFILE,
    scope_supported,
    working_directory_supported,
)
from gaia.ui.models import ChatRequest
from gaia.ui.sse_handler import SSEOutputHandler


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A home folder holding GAIA's documents folder and the user's work folder."""
    home = (tmp_path / "home").resolve()
    managed = home / ".gaia" / "documents"
    managed.mkdir(parents=True)
    work = home / "Work" / "q3-report"
    work.mkdir(parents=True)
    project = (tmp_path / "project").resolve()
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(chat_helpers, "_managed_documents_dir", lambda: managed)
    monkeypatch.delenv(ALLOWED_PATHS_ENV, raising=False)
    return SimpleNamespace(home=home, managed=managed, work=work, project=project)


def _scope(tree, monkeypatch, *, cwd, env=None):
    monkeypatch.chdir(cwd)
    if env is not None:
        monkeypatch.setenv(ALLOWED_PATHS_ENV, str(env))
    return chat_helpers._compute_allowed_paths([])


def test_a_backend_started_in_home_works_in_the_folder_the_user_set(tree, monkeypatch):
    allowed = _scope(tree, monkeypatch, cwd=tree.home, env=tree.work)

    validator = PathValidator(allowed)

    assert validator.allowed_paths == {tree.work}
    assert validator.is_path_allowed(str(tree.work / "q.csv"), prompt_user=False)


def test_without_the_setting_home_still_falls_back_to_gaias_folder(tree, monkeypatch):
    assert _scope(tree, monkeypatch, cwd=tree.home) == [str(tree.managed)]


def test_a_setting_that_overlaps_the_fallback_still_only_narrows(tree, monkeypatch):
    inside = tree.project / "sub"
    inside.mkdir()

    allowed = _scope(tree, monkeypatch, cwd=tree.project, env=inside)

    assert PathValidator(allowed).allowed_paths == {inside}


def test_an_entry_too_broad_to_grant_is_not_used(tree, monkeypatch):
    # Home itself shares GAIA's documents folder, so nothing stands in for it.
    allowed = _scope(tree, monkeypatch, cwd=tree.home, env=tree.home)

    assert allowed == [str(tree.managed)]


def test_attached_documents_are_not_replaced_by_the_setting(tree, monkeypatch):
    monkeypatch.chdir(tree.home)
    monkeypatch.setenv(ALLOWED_PATHS_ENV, str(tree.work))
    doc = tree.project / "plan.txt"
    doc.write_text("x", encoding="utf-8")

    allowed = chat_helpers._compute_allowed_paths([str(doc)])

    assert str(tree.work) not in allowed


# ── The session panel ───────────────────────────────────────────────────────


def test_the_panel_shows_the_scope_a_relayed_chat_works_in(tree, monkeypatch):
    monkeypatch.chdir(tree.home)
    monkeypatch.setenv(ALLOWED_PATHS_ENV, str(tree.work))
    monkeypatch.setattr(session_environment, "_live_agent", lambda _sid: None)
    monkeypatch.setattr(session_environment, "relayed", lambda _session: True)
    db = ChatDatabase(":memory:")
    session = db.create_session(agent_type="gaia")

    body = session_environment.describe(db, session)

    assert [(f["path"], f["source"]) for f in body["folders"]] == [
        (str(tree.work), "allowed_paths")
    ]


def test_the_panel_drops_what_the_setting_excludes(tree, monkeypatch):
    inside = tree.project / "sub"
    inside.mkdir()
    monkeypatch.chdir(tree.project)
    monkeypatch.setenv(ALLOWED_PATHS_ENV, str(inside))
    monkeypatch.setattr(session_environment, "_live_agent", lambda _sid: None)
    db = ChatDatabase(":memory:")
    session = db.create_session(agent_type="gaia")

    body = session_environment.describe(db, session)

    assert [f["path"] for f in body["folders"]] == [str(inside)]


# ── The relay ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "version, expected", [("2.18", True), ("3.0", True), ("2.17", False), (None, False)]
)
def test_only_a_flagship_at_2_18_takes_the_scope(version, expected):
    assert scope_supported(GAIA_PROFILE, version) is expected


@pytest.mark.parametrize(
    "version, expected", [("2.22", True), ("3.0", True), ("2.21", False), (None, False)]
)
def test_only_a_flagship_at_2_22_takes_the_working_directory(version, expected):
    assert working_directory_supported(GAIA_PROFILE, version) is expected
    assert working_directory_supported(EMAIL_PROFILE, "9.9") is False


def test_the_relay_sends_the_workspace_only_to_a_supporting_flagship(monkeypatch):
    workspace = "/tmp/workspace"

    new = _dispatch(monkeypatch, "2.22", [workspace])
    old = _dispatch(monkeypatch, "2.21", [workspace])

    assert new["working_directory"] == workspace
    assert "working_directory" not in old


def test_email_never_takes_the_scope():
    assert scope_supported(EMAIL_PROFILE, "9.9") is False


class _Proxy:
    agent_id = "gaia"

    def __init__(self):
        self.bodies = []

    def init(self):
        return 200, {}

    def query_stream(self, body, read_timeout, on_response):
        self.bodies.append(body)
        yield {"type": "final", "answer": "ok"}


def _dispatch(monkeypatch, version, allowed):
    proxy = _Proxy()

    class _Handle:
        api_version = version

        def proxy(self):
            return proxy

    monkeypatch.setattr(daemon_client, "acquire_handle", lambda _id: _Handle())
    chat_helpers._dispatch_sidecar_query(
        SSEOutputHandler(),
        ChatRequest(session_id="s", message="list my files", agent_type="gaia"),
        [],
        None,
        "gaia",
        allowed_paths=allowed,
        working_directory=allowed[0],
    )
    [body] = proxy.bodies
    return body


def test_the_relay_sends_the_scope_the_ui_computed(tree, monkeypatch):
    monkeypatch.setenv(ALLOWED_PATHS_ENV, str(tree.work))
    other = tree.project

    body = _dispatch(monkeypatch, "2.18", [str(tree.work), str(other)])

    # Already narrowed: the sidecar may not share this process's environment.
    assert body["allowed_paths"] == [str(tree.work)]


def test_an_invalid_setting_stops_the_relayed_turn_with_its_remedy(tree, monkeypatch):
    # The folder was deleted after the app started: the sidecar may not see
    # this environment, so an empty scope would refuse every file unexplained.
    monkeypatch.setenv(ALLOWED_PATHS_ENV, str(tree.project / "gone"))
    proxy = _Proxy()

    class _Handle:
        api_version = "2.18"

        def proxy(self):
            return proxy

    monkeypatch.setattr(daemon_client, "acquire_handle", lambda _id: _Handle())
    handler = SSEOutputHandler()
    chat_helpers._dispatch_sidecar_query(
        handler,
        ChatRequest(session_id="s", message="list my files", agent_type="gaia"),
        [],
        None,
        "gaia",
        allowed_paths=[str(tree.work)],
    )

    assert proxy.bodies == []
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    [error] = [e for e in events if e.get("type") == "agent_error"]
    assert ALLOWED_PATHS_ENV in error["content"]
    assert "does not exist" in error["content"]


def test_the_panel_raises_on_an_invalid_setting(tree, monkeypatch):
    monkeypatch.chdir(tree.home)
    monkeypatch.setenv(ALLOWED_PATHS_ENV, "relative/folder")
    monkeypatch.setattr(session_environment, "_live_agent", lambda _sid: None)
    db = ChatDatabase(":memory:")
    session = db.create_session(agent_type="gaia")

    with pytest.raises(AllowedPathsEnvError, match="not an absolute path"):
        session_environment.describe(db, session)


def test_an_older_flagship_is_sent_no_scope(tree, monkeypatch):
    body = _dispatch(monkeypatch, "2.17", [str(tree.work)])

    assert "allowed_paths" not in body


def test_relay_query_puts_the_scope_in_the_body(tmp_path):
    proxy = _Proxy()

    relay.relay_query(
        SSEOutputHandler(),
        proxy,
        query="q",
        context=[],
        profile=GAIA_PROFILE,
        allowed_paths=[str(tmp_path)],
    )

    assert proxy.bodies[0]["allowed_paths"] == [str(tmp_path)]
    assert Path(proxy.bodies[0]["allowed_paths"][0]).is_absolute()
