# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Attaching a file never asks permission for GAIA's own upload copy.

On rc13 the Agent UI asked "Let GAIA use this file?" for
``home\\chat\\uploads\\<uuid>.pdf`` right after the user picked
``kestrel-plan.pdf``. The grant must still be tight: only a regular file
directly inside the uploads folder, linked the way the composer links it.
"""

import asyncio
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import gaia.ui._chat_helpers as helpers
from gaia.security import PathValidator
from gaia.ui._chat_helpers import (
    _compute_allowed_paths,
    _relayed_documents,
    _with_attachments,
)
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest
from gaia.ui.utils import attached_upload_paths, resolve_upload_links, uploads_dir


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    up = uploads_dir()
    up.mkdir(parents=True)
    (up / "abc.pdf").write_bytes(b"%PDF-1.4")
    (up / "shot.png").write_bytes(b"\x89PNG")
    return up.resolve()


def _attached(message: str) -> list:
    return attached_upload_paths(resolve_upload_links(message))


def test_a_composer_attachment_is_granted(home):
    msg = "When does the trial begin?\n\n[kestrel-plan.pdf](/api/files/uploads/abc.pdf)"
    assert _attached(msg) == [str(home / "abc.pdf")]


def test_the_grant_means_no_prompt(home, tmp_path):
    allowed = _with_attachments(
        _compute_allowed_paths([]), _attached("[a.pdf](/api/files/uploads/abc.pdf)")
    )
    prompts = []
    validator = PathValidator(allowed_paths=allowed)
    validator.set_access_prompt(lambda path: prompts.append(path) or False)
    assert validator.validate_read(str(home / "abc.pdf"))[0] is True
    assert prompts == []
    # Only that file: its neighbours in the uploads folder stay out of scope.
    (home / "other.pdf").write_bytes(b"%PDF")
    assert validator.validate_read(str(home / "other.pdf"))[0] is False


def test_attachments_never_drop_the_sessions_working_folder(home, tmp_path):
    base = _compute_allowed_paths([])
    allowed = _with_attachments(base, [str(home / "abc.pdf")])
    assert set(base) <= set(allowed)


@pytest.mark.parametrize(
    "message",
    [
        # Unresolved browser links and plain mentions are not the composer's form.
        "[x](/api/files/uploads/abc.pdf)",
        "read {up}{sep}abc.pdf please",
        "({up}{sep}abc.pdf)",
        # Traversal and sub-folders out of the uploads folder.
        "[x]({up}{sep}..{sep}config.json)",
        "[x]({up}{sep}sub{sep}abc.pdf)",
        "[x]({up}{sep}..)",
        "[x]({up}{sep}missing.pdf)",
        # A sibling folder that merely shares the prefix.
        "[x]({up}2{sep}abc.pdf)",
    ],
)
def test_anything_but_a_real_upload_link_is_not_granted(home, message):
    (home.parent / "config.json").write_text("{}")
    (home / "sub").mkdir()
    (home / "sub" / "abc.pdf").write_bytes(b"%PDF")
    sibling = Path(str(home) + "2")
    sibling.mkdir()
    (sibling / "abc.pdf").write_bytes(b"%PDF")
    msg = message.format(up=home, sep=os.sep)
    assert attached_upload_paths(msg) == []


def test_a_symlink_in_the_uploads_folder_is_not_granted(home, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("token")
    link = home / "link.txt"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available to this user")
    assert attached_upload_paths(f"[x]({link})") == []


def test_a_directory_named_like_an_upload_is_not_granted(home):
    (home / "dir.pdf").mkdir()
    assert attached_upload_paths(f"[x]({home / 'dir.pdf'})") == []


def test_each_attachment_is_granted_once(home):
    msg = (
        "[a](/api/files/uploads/abc.pdf) and again [b](/api/files/uploads/abc.pdf)\n"
        "![s](/api/files/uploads/shot.png)"
    )
    assert _attached(msg) == sorted([str(home / "abc.pdf"), str(home / "shot.png")])


def test_a_relayed_sidecar_is_sent_documents_but_not_images(home):
    attached = [str(home / "abc.pdf"), str(home / "shot.png")]
    assert _relayed_documents(["/docs/a.txt"], attached) == [
        "/docs/a.txt",
        str(home / "abc.pdf"),
    ]
    # An attachment already in the session's documents is not sent twice.
    assert _relayed_documents([str(home / "abc.pdf")], attached) == [
        str(home / "abc.pdf")
    ]


# --- the chat turn itself grants it (cached agent, both transports) ----------


class _ReadingAgent:
    def __init__(self, scope, target):
        self.path_validator = PathValidator(allowed_paths=[str(scope)])
        self.target = str(target)
        self.indexed_files = set()
        self.conversation_history = []
        self.rag = None
        self.can_read = None
        self._cancel_event = threading.Event()
        self.console = None

    def _register_tools(self):
        pass

    def process_query(self, _message, **_kwargs):
        self.can_read = self.path_validator.is_path_allowed(
            self.target, prompt_user=False
        )
        return "ok"


@pytest.fixture
def chat(home, tmp_path, monkeypatch):
    monkeypatch.setattr(helpers, "_agent_registry", None)
    monkeypatch.setattr(helpers, "_maybe_load_expected_model", lambda *a, **k: None)
    helpers._agent_cache.clear()
    scope = tmp_path / "scope"
    scope.mkdir()
    db = ChatDatabase(":memory:")
    session = db.create_session(model="M-GGUF", agent_type="chat")
    agent = _ReadingAgent(scope, home / "abc.pdf")
    helpers._store_agent(session["id"], "M-GGUF", [], agent, "chat")
    yield db, db.get_session(session["id"]), agent
    helpers._agent_cache.clear()
    db.close()


_TURN = "When does it begin?\n\n[kestrel-plan.pdf](/api/files/uploads/abc.pdf)"


def test_a_streaming_turn_can_read_its_attachment(chat):
    db, session, agent = chat
    request = ChatRequest(
        session_id=session["id"], message=resolve_upload_links(_TURN), stream=True
    )

    async def drain():
        async for _ in helpers._stream_chat_impl(
            SimpleNamespace(handler=None), db, session, request
        ):
            pass

    asyncio.run(drain())
    assert agent.can_read is True


def test_a_non_streaming_turn_can_read_its_attachment(chat):
    db, session, agent = chat
    request = ChatRequest(
        session_id=session["id"], message=resolve_upload_links(_TURN), stream=False
    )
    asyncio.run(helpers._get_chat_response(db, session, request))
    assert agent.can_read is True


def test_a_turn_without_the_link_cannot_read_it(chat):
    db, session, agent = chat
    request = ChatRequest(session_id=session["id"], message="hi", stream=False)
    asyncio.run(helpers._get_chat_response(db, session, request))
    assert agent.can_read is False
