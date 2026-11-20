# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""On a paired device, file reads stay inside one chat's folders and never
reach a secret.

A paired device names the chat it reads for. Previews, images, folder
listings and searches then see only what that chat may touch, as its session
panel lists it, GAIA's secret-file policy still refuses keys and ``.env``
files inside it, and a preview is capped. Documents are added to the library
on the PC. The PC's own reads are unchanged.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gaia import security as gaia_security
from gaia.ui import server as server_mod
from gaia.ui.routers.files import DEVICE_PREVIEW_MAX_LINES
from gaia.ui.server import create_app

from .device_scope import ALLOWED, PC, on_listener

pytestmark = pytest.mark.allow_network  # TestClient only; nothing leaves the machine

UI = {"X-Gaia-UI": "1"}


@pytest.fixture
def home(mock_home, monkeypatch):
    # The home folder is never a chat's working folder, so no default scope.
    monkeypatch.chdir(mock_home)
    ssh = mock_home / ".ssh"
    ssh.mkdir()
    (ssh / "id_ed25519").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\n")
    (ssh / "config").write_text("Host build\n  IdentityFile ~/.ssh/id_ed25519\n")
    docker = mock_home / ".docker"
    docker.mkdir()
    (docker / "config.json").write_text('{"auths": {"ghcr.io": "registry-secret"}}')
    monkeypatch.setattr(gaia_security, "SECRET_DIRECTORIES", {str(ssh), str(docker)})
    project = mock_home / "project"
    project.mkdir()
    (project / "notes.md").write_text("\n".join(f"line {i}" for i in range(300)))
    (project / ".env").write_text("TOKEN=project-secret\n")
    (project / "id_rsa").write_text("-----BEGIN RSA PRIVATE KEY-----\n")
    (project / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (project / "credentials.json").write_text('{"token": "project-secret"}')
    (mock_home / "elsewhere.md").write_text("not this chat's\n")
    return mock_home


@pytest.fixture
def indexed(monkeypatch):
    """The files the library indexed; each yields one chunk."""
    seen = []

    async def index(path, display_name=None, **_):
        seen.append(display_name or path.name)
        return 1

    monkeypatch.setattr(server_mod, "_index_document", index)
    return seen


@pytest.fixture
def app(home, indexed):
    return create_app(db_path=":memory:")


@pytest.fixture
def chat(app, home):
    db = app.state.db
    session = db.create_session(title="Phone chat")
    db.set_session_environment(
        session["id"], folders=[str(home / "project")], withheld=[], internet_off=False
    )
    return session["id"]


@pytest.fixture
def device(app):
    return TestClient(on_listener(app, ALLOWED), client=PC)


@pytest.fixture
def wide(app, home):
    """A chat that somehow holds the whole home folder."""
    db = app.state.db
    session_id = db.create_session(title="Whole home")["id"]
    db.set_session_environment(
        session_id, folders=[str(home)], withheld=[], internet_off=False
    )
    return session_id


@pytest.mark.parametrize(
    "route, target, chat_name",
    [
        ("/api/files/preview", ".ssh/id_ed25519", "chat"),
        ("/api/files/image", ".ssh/id_ed25519", "chat"),
        ("/api/files/preview", ".ssh/config", "wide"),
        ("/api/files/preview", "project/.env", "wide"),
        ("/api/files/preview", "elsewhere.md", "chat"),
        ("/api/files/preview", "project/notes.md", None),
        ("/api/files/image", "project/chart.png", None),
        ("/api/files/browse", ".", "chat"),
    ],
    ids=[
        "ssh-key-preview",
        "ssh-key-image",
        "credential-folder-in-scope",
        "dotenv-in-scope",
        "outside-the-chat",
        "preview-names-no-chat",
        "image-names-no-chat",
        "browse-above-the-chat",
    ],
)
def test_device_reads_refused(device, chat, wide, home, route, target, chat_name):
    params = {"path": str(home / target)} if target else {}
    if chat_name:
        params["session_id"] = {"chat": chat, "wide": wide}[chat_name]
    resp = device.get(route, params=params)
    assert resp.status_code == 403
    assert "PRIVATE KEY" not in resp.text and "project-secret" not in resp.text


def test_device_search_stays_in_the_chat_and_skips_secrets(device, chat, wide, home):
    refused = device.get(
        "/api/files/search",
        params={"query": "id_", "folder": str(home / ".ssh"), "session_id": chat},
    )
    assert refused.status_code == 403
    assert device.get("/api/files/search", params={"query": "notes"}).status_code == 403

    found = device.get(
        "/api/files/search", params={"query": ".md", "session_id": chat}
    ).json()
    names = {r["name"] for r in found["results"]}
    assert "notes.md" in names and "elsewhere.md" not in names

    everywhere = device.get(
        "/api/files/search", params={"query": "id_", "session_id": wide}
    ).json()
    assert not {r["name"] for r in everywhere["results"]} & {"id_rsa", "id_ed25519"}


def test_allowed_reads_and_the_line_cap(app, device, chat, home):
    notes = {"path": str(home / "project" / "notes.md"), "lines": 200}
    capped = device.get("/api/files/preview", params={**notes, "session_id": chat})
    assert capped.status_code == 200
    assert len(capped.json()["preview_lines"]) == DEVICE_PREVIEW_MAX_LINES
    image = device.get(
        "/api/files/image",
        params={"path": str(home / "project" / "chart.png"), "session_id": chat},
    )
    assert image.status_code == 200
    listing = device.get(
        "/api/files/browse",
        params={"path": str(home / "project"), "session_id": chat},
    ).json()
    assert listing["parent_path"] is None
    assert {"notes.md", "chart.png"} <= {e["name"] for e in listing["entries"]}
    assert not {".env", "id_rsa"} & {e["name"] for e in listing["entries"]}
    started = device.get("/api/files/browse", params={"session_id": chat}).json()
    assert started["current_path"] == started["quick_links"][0]["path"]

    pc = TestClient(app, client=PC)
    assert (
        len(pc.get("/api/files/preview", params=notes).json()["preview_lines"]) == 200
    )
    outside = pc.get("/api/files/preview", params={"path": str(home / "elsewhere.md")})
    assert outside.status_code == 200


@pytest.mark.parametrize(
    "method, route, body",
    [
        ("POST", "/api/documents/upload-path", {"filepath": "project/notes.md"}),
        ("POST", "/api/documents/index-folder", {"folder_path": "project"}),
    ],
    ids=["upload-path", "index-folder"],
)
def test_documents_are_added_on_the_pc(
    app, device, chat, home, indexed, method, route, body
):
    body = {key: str(home / value) for key, value in body.items()}
    resp = device.request(
        method, route, json=body, params={"session_id": chat}, headers=UI
    )
    assert resp.status_code == 403
    assert indexed == [] and app.state.db.list_documents() == []


def test_a_library_document_opens_on_a_device_through_preview(app, device, chat, home):
    notes = home / "project" / "notes.md"
    doc = app.state.db.add_document(
        filename=notes.name,
        filepath=str(notes),
        file_hash="h" * 64,
        file_size=notes.stat().st_size,
        chunk_count=1,
    )
    served = device.get(f"/api/documents/{doc['id']}/file", params={"session_id": chat})
    assert served.status_code == 403 and "line 0" not in served.text
    preview = device.get(
        "/api/files/preview", params={"path": str(notes), "session_id": chat}
    )
    assert preview.status_code == 200
    assert preview.json()["preview_lines"][0] == "line 0"
