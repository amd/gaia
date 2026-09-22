# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Exercise the CLI across a real socket with deterministic HTTP/SSE fixtures."""

import io
import json
import threading
import uuid
from http.client import RemoteDisconnected
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from gaia_agent import service_client as cli


@pytest.fixture
def endpoint(monkeypatch):
    state = SimpleNamespace(
        posts=[], frames=[{"type": "final", "answer": "42"}], redirect=False
    )

    class Handler(BaseHTTPRequestHandler):
        def reply(self, code, data, content_type="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if state.redirect:
                self.send_response(302)
                self.send_header("Location", "/redirected")
                self.end_headers()
                return
            if self.headers.get("Authorization") != "Bearer fixture-token":
                self.reply(401, b'{"detail":"Unauthorized"}')
            else:
                self.reply(200, b'{"ready":true}')

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.posts.append((self.path, body, self.headers.get("Authorization")))
            if self.path == "/v1/gaia/query":
                if getattr(state, "broken_chunk", False):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    self.wfile.write(b"20\r\ndata: {")
                    self.close_connection = True
                    return
                if getattr(state, "reject_query", False):
                    self.reply(409, b'{"detail":"Run already active"}')
                    return
                data = ": heartbeat\n\n" + "".join(
                    "data: " + json.dumps(event) + "\n\n" for event in state.frames
                )
                self.reply(200, data.encode(), "text/event-stream")
            else:
                self.reply(200, b'{"cancelled":true,"delivered":true}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("GAIA_GAIA_SIDECAR_TOKEN", "fixture-token")
    monkeypatch.delenv("GAIA_GAIA_SIDECAR_TOKEN_FILE", raising=False)
    state.url = f"http://127.0.0.1:{server.server_port}"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_status_authenticates_all_probes(endpoint, capsys):
    assert cli.main(["--url", endpoint.url, "status"]) == 0
    assert len(capsys.readouterr().out.splitlines()) == 4


def test_query_json_and_request_context(endpoint, tmp_path, capsys):
    context = tmp_path / "context.json"
    context.write_text('[{"role":"user","content":"previous turn"}]')
    run_id = str(uuid.uuid4())
    assert (
        cli.main(
            [
                "--url",
                endpoint.url,
                "query",
                "hello",
                "--json",
                "--run-id",
                run_id,
                "--session-id",
                "conversation",
                "--context-file",
                str(context),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["answer"] == "42"
    path, body, auth = endpoint.posts[0]
    assert body["context"][0]["content"] == "previous turn"
    assert body["run_id"] == run_id
    assert body["session_id"] == "conversation"
    assert body["can_answer_questions"] is False
    assert auth == "Bearer fixture-token"


@pytest.mark.parametrize("frames", [[], [{"type": "status", "message": "working"}]])
def test_truncated_stream_fails_and_requests_cancellation(endpoint, frames):
    endpoint.frames = frames
    assert cli.main(["--url", endpoint.url, "query", "hello"]) == 1
    assert endpoint.posts[-1][0].endswith("/cancel")


def test_error_event_is_nonzero(endpoint):
    endpoint.frames = [{"type": "error", "detail": "model unavailable"}]
    assert cli.main(["--url", endpoint.url, "query", "hello"]) == 1


def test_ctrl_c_requests_cancel(endpoint):
    client = cli.Client(endpoint.url, "fixture-token")
    run_id = str(uuid.uuid4())

    def interrupt(event):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        client.query({"query": "hello", "run_id": run_id, "context": []}, interrupt)
    assert endpoint.posts[-1][0] == f"/v1/gaia/query/{run_id}/cancel"


def test_interactive_response_stays_on_stream(endpoint, monkeypatch):
    endpoint.frames = [
        {"type": "needs_input", "request_id": "question-1", "question": "Which?"},
        {"type": "final", "answer": "done"},
    ]
    stdin = io.StringIO("choice\n")
    monkeypatch.setattr(stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.sys, "stdin", stdin)
    assert cli.main(["--url", endpoint.url, "query", "hello", "--interactive"]) == 0
    assert endpoint.posts[-1][0].endswith("/respond")
    assert endpoint.posts[-1][1] == {"request_id": "question-1", "response": "choice"}


def test_redirect_is_not_followed(endpoint):
    endpoint.redirect = True
    assert cli.main(["--url", endpoint.url, "status"]) == 1


def test_bad_auth_fails(endpoint, monkeypatch, capsys):
    monkeypatch.setenv("GAIA_GAIA_SIDECAR_TOKEN", "wrong")
    assert cli.main(["--url", endpoint.url, "status"]) == 1
    assert "HTTP 401" in capsys.readouterr().err


def test_token_file_overrides_environment(endpoint, tmp_path):
    token = tmp_path / "token"
    token.write_text("fixture-token\n")
    assert cli.main(["--url", endpoint.url, "--token-file", str(token), "status"]) == 0


def test_multiline_sse_and_partial_frame():
    assert list(
        cli.events(io.BytesIO(b': ping\n\ndata: {"type":\ndata: "final"}\n\n'))
    ) == [{"type": "final"}]
    with pytest.raises(cli.ClientError, match="incomplete"):
        list(cli.events(io.BytesIO(b'data: {"type":"final"}')))


def test_client_dispatch_does_not_start_service(monkeypatch):
    from gaia_agent import server, service

    monkeypatch.setattr(
        service, "main", lambda: pytest.fail("Unexpected service start")
    )
    monkeypatch.setattr(cli, "main", lambda args: 17 if args == ["status"] else 1)
    assert server.main(["--client", "status"]) == 17


def test_duplicate_run_rejection_does_not_cancel_existing_run(endpoint):
    endpoint.reject_query = True
    assert cli.main(["--url", endpoint.url, "query", "hello"]) == 1
    assert [path for path, _, _ in endpoint.posts] == ["/v1/gaia/query"]


def test_broken_http_chunk_is_reported_and_cancelled(endpoint, capsys):
    endpoint.broken_chunk = True
    assert cli.main(["--url", endpoint.url, "query", "hello"]) == 1
    assert endpoint.posts[-1][0].endswith("/cancel")
    assert "Error:" in capsys.readouterr().err


def test_open_reports_remote_disconnect_as_client_error(monkeypatch):
    client = cli.Client("http://127.0.0.1:8080", "fixture-token")
    failure = RemoteDisconnected("Remote end closed connection without response")
    attempts = []

    def disconnect(*args, **kwargs):
        attempts.append(1)
        raise failure

    monkeypatch.setattr(client.opener, "open", disconnect)
    with pytest.raises(
        cli.ClientError, match="Cannot reach GAIA: Remote end closed"
    ) as error:
        client.open("/health")
    assert error.value.__cause__ is failure
    assert attempts == [1]


def test_sensitive_answer_uses_hidden_input(endpoint, monkeypatch, capsys):
    endpoint.frames = [
        {
            "type": "needs_input",
            "request_id": "secret",
            "question": "Token?",
            "sensitive": True,
        },
        {"type": "final", "answer": "done"},
    ]
    stdin = io.StringIO()
    monkeypatch.setattr(stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.sys, "stdin", stdin)
    monkeypatch.setattr(
        stdin, "readline", lambda: pytest.fail("Secret read with terminal echo")
    )
    monkeypatch.setattr(
        cli.getpass, "getpass", lambda *args, **kwargs: "private-answer"
    )
    assert cli.main(["--url", endpoint.url, "query", "hello", "--interactive"]) == 0
    assert endpoint.posts[-1][1]["response"] == "private-answer"
    output = capsys.readouterr()
    assert "private-answer" not in output.out + output.err


@pytest.mark.parametrize(
    "failure", [EOFError(), cli.getpass.GetPassWarning("Echo unavailable")]
)
def test_sensitive_input_failure_cancels_without_plaintext_fallback(
    endpoint, monkeypatch, failure
):
    endpoint.frames = [
        {"type": "needs_input", "request_id": "secret", "sensitive": True}
    ]
    stdin = io.StringIO("do-not-read\n")
    monkeypatch.setattr(stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.sys, "stdin", stdin)

    def unavailable(*args, **kwargs):
        if isinstance(failure, cli.getpass.GetPassWarning):
            cli.warnings.warn(failure)
        else:
            raise failure

    monkeypatch.setattr(cli.getpass, "getpass", unavailable)
    assert cli.main(["--url", endpoint.url, "query", "hello", "--interactive"]) == 1
    assert endpoint.posts[-1][0].endswith("/cancel")
    assert not any(path.endswith("/respond") for path, _, _ in endpoint.posts)
    assert stdin.tell() == 0


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), -float("inf"), 0, -1])
def test_nonfinite_or_nonpositive_timeout_rejected(timeout):
    with pytest.raises(cli.ClientError, match="finite and positive"):
        cli.Client("http://localhost:8080", "token", timeout=timeout)


def test_unacknowledged_interactive_answer_cancels(endpoint, monkeypatch):
    endpoint.frames = [{"type": "needs_input", "request_id": "question"}]
    stdin = io.StringIO("answer\n")
    monkeypatch.setattr(stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.sys, "stdin", stdin)
    original = cli.Client.json

    def json_response(self, path, body=None, timeout=None):
        if path.endswith("/respond"):
            return {"delivered": False}
        return original(self, path, body, timeout)

    monkeypatch.setattr(cli.Client, "json", json_response)
    assert cli.main(["--url", endpoint.url, "query", "hello", "--interactive"]) == 1
    assert endpoint.posts[-1][0].endswith("/cancel")
