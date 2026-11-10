# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Chat attachments keep a readable name that links and citations can use."""

import io
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gaia.ui.server import create_app
from gaia.ui.sse_handler import _summarize_tool_result
from gaia.ui.utils import attached_upload_paths, resolve_upload_links, uploads_dir
from gaia.upload_names import (
    display_file_name,
    upload_copy_name,
    upload_original_name,
)

# UPLOAD_URL_RE in src/gaia/apps/webui/src/utils/markdown.ts.
WEB_UI_SERVES = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

HOSTILE_NAMES = [
    "Q3 plan (final).pdf",
    "../../evil.pdf",
    "..\\..\\evil.pdf",
    "a b; rm -rf $(x).pdf",
    "[link](http://x).pdf",
    "-draft.pdf",
    "...pdf",
    "تقرير الاجتماع.pdf",
    "x" * 300 + ".pdf",
]


@pytest.mark.parametrize("name", HOSTILE_NAMES)
def test_any_name_becomes_one_the_web_ui_serves(name):
    assert WEB_UI_SERVES.fullmatch(upload_copy_name(name, ".pdf"))


@pytest.mark.parametrize(
    "name, shown",
    [
        ("kestrel-plan.pdf", "kestrel-plan.pdf"),
        ("Q3 plan (final).pdf", "Q3_plan_final.pdf"),
        ("../../evil.pdf", "evil.pdf"),
        ("..\\..\\evil.pdf", "evil.pdf"),
        ("-draft.pdf", "draft.pdf"),
        ("تقرير الاجتماع.pdf", "attachment.pdf"),
    ],
)
def test_the_copy_is_cited_by_its_readable_name(name, shown):
    copy = Path("home") / "chat" / "uploads" / upload_copy_name(name, ".pdf")
    assert display_file_name(str(copy)) == shown


def test_two_uploads_of_one_file_get_different_copies():
    assert upload_copy_name("a.pdf", ".pdf") != upload_copy_name("a.pdf", ".pdf")


@pytest.mark.parametrize(
    "path",
    [
        # A copy made before attachments kept their names.
        Path("home") / "chat" / "uploads" / "f27f4885-cec8-4161-9dbd-ebc8a3c25005.pdf",
        # The user's own files, whatever they are called.
        Path("docs") / "0123456789ab_report.pdf",
        Path("docs") / "uploads" / "0123456789ab_report.pdf",
    ],
)
def test_other_files_keep_their_full_name(path):
    assert upload_original_name(str(path)) is None
    assert display_file_name(str(path)) == path.name


def test_the_indexed_step_names_an_attachment_as_the_user_knows_it():
    copy = str(Path("home") / "chat" / "uploads" / "0123456789ab_plan.pdf")
    summary = _summarize_tool_result({"num_chunks": 3, "file_path": copy})
    assert summary == "Indexed plan.pdf (3 chunks)"


def test_the_indexed_step_keeps_the_path_of_any_other_file():
    path = str(Path("docs") / "plan.pdf")
    summary = _summarize_tool_result({"num_chunks": 3, "file_path": path})
    assert summary == f"Indexed {path} (3 chunks)"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    with TestClient(create_app(db_path=":memory:")) as test_client:
        yield test_client


@pytest.mark.parametrize("name", HOSTILE_NAMES)
def test_an_uploaded_attachment_reaches_the_agent(client, name):
    response = client.post(
        "/api/files/upload",
        files={"file": (name, io.BytesIO(b"%PDF-1.4"), "application/pdf")},
    )
    assert response.status_code == 200, response.text
    upload = response.json()

    # The composer's link, as the chat route resolves it for the agent.
    link = f"[attachment]({upload['url']})"
    resolved = resolve_upload_links(f"Summarize it\n\n{link}")

    copy = str(uploads_dir().resolve() / upload["filename"])
    assert copy in resolved
    assert attached_upload_paths(resolved) == [copy]
    assert upload_original_name(copy)
