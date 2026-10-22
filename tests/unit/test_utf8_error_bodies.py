# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A UTF-8 error message keeps its "—" from Lemonade to the Agent UI's API."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests
import responses
from fastapi.testclient import TestClient
from requests.utils import get_encoding_from_headers

from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError
from gaia.llm.retry import http_body_text
from gaia.ui.server import create_app

MESSAGE = "Model failed to load — not enough memory"
MOJIBAKE = "â€”"  # "—" read as cp1252
LATIN1 = "â\u0080\u0094"  # "—" read as ISO-8859-1


class _ErrorBodyHandler(BaseHTTPRequestHandler):
    """Answers every GET with a 500 whose UTF-8 body names no charset."""

    content_type = "text/plain"

    def do_GET(self):  # noqa: N802 - http.server's naming
        body = MESSAGE.encode("utf-8")
        self.send_response(500)
        self.send_header("Content-Type", self.content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def error_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ErrorBodyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.mark.allow_network
def test_requests_alone_mangles_an_undeclared_text_body(error_server):
    """The precondition: without the fix, this is what users saw."""
    response = requests.get(f"{error_server}/x", timeout=5)
    assert LATIN1 in response.text


@pytest.mark.allow_network
def test_http_body_text_reads_utf8_when_no_charset_is_named(error_server):
    response = requests.get(f"{error_server}/x", timeout=5)
    assert http_body_text(response) == MESSAGE


def test_http_body_text_honours_a_declared_charset():
    response = requests.Response()
    response._content = MESSAGE.encode("utf-16")
    response.headers["Content-Type"] = "text/plain; charset=utf-16"
    response.encoding = "utf-16"
    assert http_body_text(response) == MESSAGE


@pytest.mark.allow_network
def test_lemonade_request_error_keeps_the_dash(error_server, tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path))
    client = LemonadeClient(base_url=f"{error_server}/api/v1", verbose=False)
    with pytest.raises(LemonadeClientError) as raised:
        client._send_request("get", f"{error_server}/api/v1/models")
    assert MESSAGE in str(raised.value)
    assert MOJIBAKE not in str(raised.value)
    assert LATIN1 not in str(raised.value)


@responses.activate
def test_chat_completion_error_keeps_the_dash(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path))
    base = "http://localhost:13305/api/v1"
    model = "Gemma-4-E4B-it-GGUF"
    responses.add(
        responses.GET,
        f"{base}/health",
        json={"status": "ok", "model_loaded": model, "all_models_loaded": []},
    )
    responses.add(
        responses.GET,
        f"{base}/models",
        json={"data": [{"id": model, "recipe": "llamacpp", "downloaded": True}]},
    )
    responses.add(
        responses.POST,
        f"{base}/load",
        json={"status": "success", "message": f"Loaded model: {model}"},
    )
    responses.add(
        responses.POST,
        f"{base}/chat/completions",
        body=MESSAGE.encode("utf-8"),
        status=400,
        content_type="text/plain",
    )
    client = LemonadeClient(base_url=base, model=model, verbose=False)
    with pytest.raises(LemonadeClientError) as raised:
        client.chat_completions(
            model=model,
            messages=[{"role": "user", "content": "hi"}],
            auto_download=False,
        )
    assert MESSAGE in str(raised.value)


def test_agent_ui_json_declares_utf8():
    """Clients that default to Latin-1 (PowerShell 5.1) read the dash right."""
    client = TestClient(create_app(db_path=":memory:"))
    # No lifespan ran, so the scheduler is absent and its 503 detail has a "—".
    r = client.get("/api/schedules", headers={"X-Gaia-UI": "1"})
    assert r.status_code == 503
    assert r.headers["content-type"] == "application/json; charset=utf-8"
    detail = r.content.decode(get_encoding_from_headers(r.headers))
    assert "—" in detail
    assert MOJIBAKE not in detail
