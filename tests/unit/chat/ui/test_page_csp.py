# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The Content-Security-Policy on the Agent UI page the backend serves."""

import base64
import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gaia.ui.server import create_app

THEME_SCRIPT = "document.documentElement.setAttribute('data-theme', 'dark');"
INDEX = (
    "<!doctype html><html><head>"
    f"<script>{THEME_SCRIPT}</script>"
    '<script type="module" src="./assets/app.js"></script>'
    "</head><body><div id=root></div></body></html>"
)


def _policy(response) -> dict:
    header = response.headers["content-security-policy"]
    return {
        part.split()[0]: part.split()[1:]
        for part in (p.strip() for p in header.split(";"))
        if part
    }


@pytest.fixture
def client(tmp_path: Path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text(INDEX, encoding="utf-8")
    app = create_app(webui_dist=str(tmp_path), db_path=":memory:")
    return TestClient(app, raise_server_exceptions=False)


def test_page_has_a_policy_that_runs_only_its_own_scripts(client):
    policy = _policy(client.get("/"))
    digest = base64.b64encode(hashlib.sha256(THEME_SCRIPT.encode()).digest()).decode()
    assert policy["script-src"] == ["'self'", f"'sha256-{digest}'"]
    assert policy["default-src"] == ["'self'"]


def test_page_talks_only_to_its_own_server(client):
    policy = _policy(client.get("/"))
    assert policy["connect-src"] == ["'self'"]
    assert policy["object-src"] == ["'none'"]
    assert policy["frame-ancestors"] == ["'none'"]
    assert policy["base-uri"] == ["'self'"]


def test_policy_follows_the_script_in_the_page_served_now(client, tmp_path):
    (tmp_path / "index.html").write_text(
        INDEX.replace("'dark'", "'light'"), encoding="utf-8"
    )
    changed = THEME_SCRIPT.replace("'dark'", "'light'")
    digest = base64.b64encode(hashlib.sha256(changed.encode()).digest()).decode()
    assert f"'sha256-{digest}'" in _policy(client.get("/"))["script-src"]


def test_a_page_route_gets_the_policy_too(client):
    assert "content-security-policy" in client.get("/some/route").headers


def test_the_no_build_page_has_a_policy_without_inline_scripts(tmp_path):
    app = create_app(webui_dist=str(tmp_path / "missing"), db_path=":memory:")
    response = TestClient(app, raise_server_exceptions=False).get("/")
    assert _policy(response)["script-src"] == ["'self'"]
