# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The log viewer: redaction, every GAIA log, and the model server's own vs the user's vs remote.

The log stream is exercised against a real WebSocket server speaking
Lemonade's protocol, so the request shape (bearer header, ``logs.subscribe``
with ``after_seq``) is checked, not just that a call was made.
"""

from __future__ import annotations

import asyncio
import json
import socket
from unittest import mock

import pytest

from gaia import log_reader as reader
from gaia import log_sources
from gaia.llm import lemonade_client as lc
from gaia.llm import lemonade_logs as logs

SECRET = "embedded-key-0123456789"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for name in ("LEMONADE_BASE_URL", "LEMONADE_API_KEY", "GAIA_LEMONADE_EMBEDDED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    monkeypatch.setattr(lc, "_read_embedded_lemonade_state", lambda *a, **k: None)


def _health(monkeypatch, *, answer=None, error=None):
    def health_check(self, timeout=None):
        if error:
            raise lc.LemonadeClientError(error)
        return answer

    monkeypatch.setattr(lc.LemonadeClient, "health_check", health_check)


def _lemond_log(tmp_path, text):
    path = tmp_path / "gaia-home" / "lemonade" / "lemond.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


class TestRedact:
    @pytest.mark.parametrize(
        "line, leaked",
        [
            ("Authorization: Bearer abcdef123456", "abcdef123456"),
            ("sending bearer zz9-token-value-1", "zz9-token-value-1"),
            ('{"api_key": "abc123secret"}', "abc123secret"),
            ("url?api_key=abc123secret&x=1", "abc123secret"),
            ("lemond --api-key abc123secret --port 1", "abc123secret"),
            ("token=abc123secret", "abc123secret"),
            ("HF hf_abcdefghijklmnopqrstuvwxyz", "hf_abcdefghijklmnopqrstuvwxyz"),
            ("key sk-abcdefghijklmnopqrstu", "sk-abcdefghijklmnopqrstu"),
            ("gh ghp_abcdefghijklmnopqrstuvwx", "ghp_abcdefghijklmnopqrstuvwx"),
        ],
    )
    def test_secret_shapes_are_removed(self, line, leaked):
        out = reader.redact(line)
        assert leaked not in out
        assert reader.REDACTED in out

    def test_known_secrets_are_removed_wherever_they_appear(self):
        out = reader.redact(f"loaded with {SECRET} inline", [SECRET])
        assert SECRET not in out and reader.REDACTED in out

    def test_a_placeholder_word_used_as_a_key_is_not_scrubbed_from_every_line(
        self, monkeypatch
    ):
        # "lemonade" is the stock placeholder key; it is also in every path and module name.
        monkeypatch.setenv("SOME_LOCAL_API_KEY", "lemonade")
        monkeypatch.setenv("REAL_API_KEY", "fw_Abc123def456")
        monkeypatch.setenv("SMTP_PASSWORD", "Sunshineforever")
        secrets = reader.env_secrets()
        assert "lemonade" not in secrets and "fw_Abc123def456" in secrets
        assert "Sunshineforever" in secrets
        line = "gaia.daemon.lemonade_clients | C:/Users/a/.gaia/lemonade/lemond.log"
        assert reader.redact(line, ["lemonade"]) == line
        assert reader.redact_lines(line.encode(), ["lemonade"]) == line.encode()

    def test_ordinary_lines_and_schemas_stay_readable(self):
        line = "2026-11-04 10:06:05.218 [Info] (Server) Starting HTTP server on localhost:56150"
        assert reader.redact(line) == line
        schema = '"password":{"type":"string"}'
        assert reader.redact(schema) == schema

    @pytest.mark.parametrize(
        "line",
        [
            "(Process) 0.01.718.868 W load: control-looking token:      1 '<eos>' was not control-type",
            "(Process) I slot update_slots: n_tokens = 512, batch.n_tokens = 512",
            "(Process) W load: special_eos_id is not in special_eog_ids - the tokenizer config may be incorrect",
        ],
    )
    def test_llama_cpp_token_talk_is_not_a_secret(self, line):
        assert reader.redact(line) == line


class TestParseLine:
    def test_severity_and_tag(self):
        line = reader.parse_line(
            "2026-11-04 10:06:05.218 [Warn] (LlamaCpp) slow load\n"
        )
        assert (line.severity, line.tag) == ("warning", "LlamaCpp")
        assert line.text.endswith("slow load")

    def test_continuation_line_has_no_severity(self):
        line = reader.parse_line("    at frame 3")
        assert line.severity is None and line.tag is None

    def test_a_huge_line_keeps_its_start(self):
        line = reader.parse_line(
            "2026-11-04 10:06:05.218 [Info] (Process) " + "x" * 10_000
        )
        assert len(line.text) < reader.MAX_LINE_CHARS + 50
        assert "more characters" in line.text


# ---------------------------------------------------------------------------
# GAIA's own Lemonade: lemond.log
# ---------------------------------------------------------------------------


def _lines(n, start=0):
    return "".join(
        f"2026-11-04 10:00:{i % 60:02d}.000 [Info] (Server) line {i}\n"
        for i in range(start, start + n)
    )


class TestReadLogFile:
    def test_missing_file_says_so(self, tmp_path):
        r = reader.read_log_file(tmp_path / "nope.log")
        assert not r.available and r.reason == "no_file"

    def test_last_lines_then_only_new_ones(self, tmp_path):
        path = tmp_path / "lemond.log"
        path.write_text(_lines(10), encoding="utf-8")
        first = reader.read_log_file(path, limit=4)
        assert [line.text.split()[-1] for line in first.lines] == ["6", "7", "8", "9"]
        assert first.dropped == 6
        with open(path, "a", encoding="utf-8") as f:
            f.write(_lines(2, start=10))
            f.write("2026-11-04 10:00:12.000 [Error] (Process) half a li")
        nxt = reader.read_log_file(path, after=int(first.cursor))
        assert [line.text.split()[-1] for line in nxt.lines] == ["10", "11"]
        assert not nxt.reset
        # The half-written line comes whole on the next read.
        with open(path, "a", encoding="utf-8") as f:
            f.write("ne\n")
        last = reader.read_log_file(path, after=int(nxt.cursor))
        assert [line.text.split()[-1] for line in last.lines] == ["line"]
        assert last.lines[0].severity == "error"

    def test_a_replaced_file_resets(self, tmp_path):
        path = tmp_path / "lemond.log"
        path.write_text(_lines(10), encoding="utf-8")
        cursor = int(reader.read_log_file(path).cursor)
        path.write_text(_lines(1), encoding="utf-8")
        r = reader.read_log_file(path, after=cursor)
        assert r.reset and len(r.lines) == 1

    def test_redacts_known_secrets(self, tmp_path):
        path = tmp_path / "lemond.log"
        path.write_text(f"2026-11-04 10:00:00.000 [Info] (main) key {SECRET}\n")
        r = reader.read_log_file(path, secrets=[SECRET])
        assert SECRET not in r.lines[0].text


class TestEmbedded:
    def test_reads_the_file_even_when_lemonade_is_down(self, tmp_path, monkeypatch):
        _lemond_log(tmp_path, f"2026-11-04 10:00:00.000 [Error] (FLM) died {SECRET}\n")
        monkeypatch.setattr(
            lc,
            "_read_embedded_lemonade_state",
            lambda *a, **k: {"api_key": SECRET, "port": 1, "pid": 1},
        )
        _health(monkeypatch, error="connection refused")
        r = asyncio.run(logs.read_lemonade_log())
        assert r.available and r.source == "file" and r.owner == "gaia"
        assert r.server_answering is False
        assert r.lines[0].severity == "error" and SECRET not in r.lines[0].text

    def test_follow_up_reads_do_not_probe_the_server(self, tmp_path, monkeypatch):
        _lemond_log(tmp_path, _lines(3))
        probe = mock.Mock(side_effect=AssertionError("probed"))
        monkeypatch.setattr(lc.LemonadeClient, "health_check", probe)
        r = asyncio.run(logs.read_lemonade_log(after="0"))
        assert r.available and r.server_answering is None and len(r.lines) == 3

    def test_no_log_yet(self, monkeypatch):
        _health(monkeypatch, answer={"status": "ok"})
        r = asyncio.run(logs.read_lemonade_log())
        assert not r.available and r.reason == "no_file"


# ---------------------------------------------------------------------------
# The user's Lemonade: its log stream
# ---------------------------------------------------------------------------


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeLogStream:
    """A WebSocket server speaking Lemonade's ``/logs/stream`` protocol."""

    def __init__(self, entries, key="user-key-abcdef"):
        self.entries = entries
        self.key = key
        self.port = _free_port()
        self.requests = []

    async def handler(self, request):
        from aiohttp import web

        if request.headers.get("Authorization") != f"Bearer {self.key}":
            return web.Response(status=401)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        message = json.loads((await ws.receive()).data)
        self.requests.append(message)
        if message.get("type") != "logs.subscribe":
            await ws.send_json(
                {"type": "error", "message": "Expected logs.subscribe message"}
            )
        else:
            after = message.get("after_seq") or 0
            await ws.send_json(
                {
                    "type": "logs.snapshot",
                    "entries": [e for e in self.entries if e["seq"] > after],
                }
            )
        await ws.close()
        return ws

    async def run(self, body):
        from aiohttp import web

        app = web.Application()
        app.router.add_get("/logs/stream", self.handler)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", self.port).start()
        try:
            return await body()
        finally:
            await runner.cleanup()


def _entry(seq, severity, tag, msg):
    return {
        "seq": seq,
        "severity": severity,
        "tag": tag,
        "timestamp": "2026-11-04 10:00:00.000",
        "line": f"2026-11-04 10:00:00.000 [{severity}] ({tag}) {msg}",
    }


class TestStream:
    @pytest.mark.allow_network
    def test_reads_entries_after_a_sequence_with_the_bearer_key(self):
        server = FakeLogStream(
            [_entry(1, "Info", "main", "start"), _entry(2, "Error", "LlamaCpp", "boom")]
        )

        async def body():
            all_ = await logs.read_log_stream(
                "http://127.0.0.1:13305", server.port, server.key
            )
            after = await logs.read_log_stream(
                "http://127.0.0.1:13305", server.port, server.key, after=1
            )
            return all_, after

        all_, after = asyncio.run(server.run(body))
        assert [e["seq"] for e in all_] == [1, 2]
        assert [e["seq"] for e in after] == [2]
        assert server.requests == [
            {"type": "logs.subscribe", "after_seq": 0},
            {"type": "logs.subscribe", "after_seq": 1},
        ]

    @pytest.mark.allow_network
    def test_a_wrong_key_is_an_error(self):
        import aiohttp

        server = FakeLogStream([])

        async def body():
            await logs.read_log_stream("http://127.0.0.1:1", server.port, "wrong")

        with pytest.raises(aiohttp.ClientError):
            asyncio.run(server.run(body))


class TestUsersLemonade:
    URL = "http://localhost:13305"

    @pytest.fixture(autouse=True)
    def _external(self, monkeypatch):
        monkeypatch.setenv("LEMONADE_BASE_URL", self.URL)
        monkeypatch.setenv("LEMONADE_API_KEY", "user-key-abcdef")

    @pytest.mark.allow_network
    def test_stream_lines_are_parsed_and_redacted(self, monkeypatch):
        server = FakeLogStream(
            [
                _entry(4, "Info", "main", "key user-key-abcdef"),
                _entry(5, "Warn", "FastFlowLM", "npu busy"),
            ]
        )
        _health(monkeypatch, answer={"status": "ok", "websocket_port": server.port})

        async def body():
            return await logs.read_lemonade_log()

        r = asyncio.run(server.run(body))
        assert r.available and r.source == "stream" and r.owner == "user"
        assert r.cursor == "5"
        assert [line.severity for line in r.lines] == ["info", "warning"]
        assert "user-key-abcdef" not in r.lines[0].text

    def test_not_answering(self, monkeypatch):
        _health(monkeypatch, error="connection refused")
        r = asyncio.run(logs.read_lemonade_log())
        assert not r.available and r.reason == "not_running"
        assert r.server_answering is False

    def test_no_stream_reported(self, monkeypatch):
        _health(monkeypatch, answer={"status": "ok", "version": "10.0.0"})
        r = asyncio.run(logs.read_lemonade_log())
        assert r.reason == "no_stream" and "websocket_port" in r.detail

    def test_stream_failure_is_named_without_the_key(self, monkeypatch):
        _health(monkeypatch, answer={"status": "ok", "websocket_port": _free_port()})
        r = asyncio.run(logs.read_lemonade_log())
        assert not r.available and r.reason == "stream_failed"
        assert "user-key-abcdef" not in (r.detail or "")

    def test_remote_server(self, monkeypatch):
        monkeypatch.setenv("LEMONADE_BASE_URL", "http://10.9.8.7:13305")
        _health(monkeypatch, error="timed out")
        r = asyncio.run(logs.read_lemonade_log())
        assert r.owner == "remote" and r.reason == "remote"


# ---------------------------------------------------------------------------
# Router and diagnostics
# ---------------------------------------------------------------------------


class TestRouter:
    @pytest.fixture
    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from gaia.ui.routers import logs as router_mod

        app = FastAPI()
        app.include_router(router_mod.router)
        client = TestClient(app, client=("127.0.0.1", 50000))
        client.headers.pop("x-gaia-ui", None)
        return client

    H = {"X-Gaia-UI": "1"}

    @pytest.mark.parametrize(
        "url", ["/api/logs", "/api/logs/gaia", "/api/logs/model_server"]
    )
    def test_needs_the_ui_header(self, client, url):
        assert client.get(url).status_code == 403

    @pytest.mark.parametrize("url", ["/api/logs", "/api/logs/gaia"])
    def test_refused_over_mobile_access(self, client, url):
        r = client.get(url, headers={**self.H, "X-Forwarded-For": "1.2.3.4"})
        assert r.status_code == 403 and "mobile access" in r.json()["detail"]

    @pytest.mark.parametrize("url", ["/api/logs", "/api/logs/gaia"])
    def test_refused_from_another_computer(self, url):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from gaia.ui.routers import logs as router_mod

        app = FastAPI()
        app.include_router(router_mod.router)
        other = TestClient(app, client=("192.168.1.20", 50000))
        r = other.get(url, headers=self.H)
        assert r.status_code == 403 and "another computer" in r.json()["detail"]

    def test_model_server_lines(self, client, tmp_path, monkeypatch):
        _lemond_log(tmp_path, "2026-11-04 10:00:00.000 [Info] (main) hi\n")
        _health(monkeypatch, answer={"status": "ok"})
        body = client.get("/api/logs/model_server", headers=self.H).json()
        assert body["source"] == "file" and body["server_answering"] is True
        assert body["owner"] == "gaia"
        assert body["lines"] == [
            {
                "text": "2026-11-04 10:00:00.000 [Info] (main) hi",
                "severity": "info",
                "tag": "main",
            }
        ]
        assert body["cursor"]

    def test_lists_every_source_with_its_files(self, client, gaia_logs):
        body = client.get("/api/logs", headers=self.H).json()
        ids = [s["id"] for s in body["sources"]]
        assert ids == list(log_sources.SOURCES)
        files = {s["id"]: [f["name"] for f in s["files"]] for s in body["sources"]}
        assert files["gaia"] == ["gaia.log"]
        assert files["service"] == ["daemon.log"]
        assert files["agent"][0] == "gaia-agent.log"
        assert "gaia/sidecar-2.log" in files["agent"]
        assert files["desktop"] == ["electron-main.log"]
        assert files["other"] == ["gaia-tui.log"]
        assert files["model_server"] == []

    def test_reads_gaias_log_redacted(self, client, gaia_logs, monkeypatch):
        monkeypatch.setenv("FIREWORKS_API_KEY", "fw-secret-value-123")
        (gaia_logs / "gaia.log").write_text(
            "[2026-11-04 11:48:16,311] | ERROR | gaia.ui | chat.py:1 | "
            "sent fw-secret-value-123\n",
            encoding="utf-8",
        )
        body = client.get("/api/logs/gaia", headers=self.H).json()
        assert body["available"] and body["source"] == "file"
        line = body["lines"][0]
        assert line["severity"] == "error"
        assert "fw-secret-value-123" not in line["text"]

    def test_reads_a_named_file_then_only_new_lines(self, client, gaia_logs):
        name = {"file": "gaia/sidecar-2.log"}
        first = client.get("/api/logs/agent", params=name, headers=self.H).json()
        assert [line["text"] for line in first["lines"]] == ["sidecar two"]
        sidecar = gaia_logs / "agents" / "gaia" / "logs" / "sidecar-2.log"
        with open(sidecar, "a", encoding="utf-8") as f:
            f.write("more\n")
        nxt = client.get(
            "/api/logs/agent",
            params={**name, "after": first["cursor"]},
            headers=self.H,
        ).json()
        assert [line["text"] for line in nxt["lines"]] == ["more"]

    @pytest.mark.parametrize(
        "url", ["/api/logs/nope", "/api/logs/gaia?file=..%2F..%2Fconfig.json"]
    )
    def test_only_listed_logs_can_be_read(self, client, gaia_logs, url):
        assert client.get(url, headers=self.H).status_code == 404

    def test_a_source_with_nothing_written_says_so(self, client):
        body = client.get("/api/logs/service", headers=self.H).json()
        assert not body["available"] and body["reason"] == "no_file"

    def test_unreadable_choice_is_a_409(self, client, monkeypatch):
        from gaia.llm import lemonade_choice as choice

        def broken():
            raise choice.LemonadeChoiceError("credential store locked")

        monkeypatch.setattr(choice, "describe", broken)
        r = client.get("/api/logs/model_server", headers=self.H)
        assert r.status_code == 409 and "credential store" in r.json()["detail"]

    def test_a_locked_credential_store_does_not_hide_gaias_log(
        self, client, gaia_logs, monkeypatch
    ):
        from gaia.llm import lemonade_choice as choice

        def locked(*a, **k):
            raise choice.LemonadeChoiceError("credential store locked")

        monkeypatch.setattr(lc, "resolve_lemonade_api_key", locked)
        body = client.get("/api/logs/gaia", headers=self.H).json()
        assert body["available"]
        assert "saved Lemonade key could not be read" in body["detail"]

    def test_a_damaged_config_does_not_hide_gaias_log(
        self, client, gaia_logs, monkeypatch
    ):
        from gaia.config import GaiaConfigError

        def damaged(*a, **k):
            raise GaiaConfigError("config.json is not valid JSON")

        monkeypatch.setattr(lc, "resolve_lemonade_api_key", damaged)
        body = client.get("/api/logs/gaia", headers=self.H).json()
        assert body["available"]
        assert "saved Lemonade key could not be read" in body["detail"]


@pytest.fixture
def gaia_logs(tmp_path, monkeypatch):
    """A GAIA home and user profile with one of each log."""
    from gaia.daemon import paths as daemon_paths

    home = tmp_path / "gaia-home"
    profile = tmp_path / "profile"
    monkeypatch.setattr(log_sources, "_user_gaia_dir", lambda: profile / ".gaia")
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(tmp_path / "daemon-home"))
    monkeypatch.delenv("GAIA_LOG_FILE", raising=False)
    files = {
        home / "gaia.log": "[2026-11-04 11:48:16,311] | INFO | gaia.ui | x.py:1 | hi\n",
        home / "logs" / "gaia-agent.log": "agent\n",
        home / "logs" / "gaia-tui.log": "tui\n",
        home / "agents" / "gaia" / "logs" / "sidecar-1.log": "sidecar one\n",
        home / "agents" / "gaia" / "logs" / "sidecar-2.log": "sidecar two\n",
        profile / ".gaia" / "electron-main.log": "[2026-11-04T19:48:29.751Z] INFO ok\n",
        daemon_paths.log_path(): "[2026-11-04 11:48:15] | INFO | gaia.daemon | d:1 | up\n",
    }
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return home


class TestSeverityAcrossFormats:
    @pytest.mark.parametrize(
        "line, severity",
        [
            (
                "[2026-11-04 11:48:16,311] | WARNING | gaia.ui | x.py:1 | slow",
                "warning",
            ),
            (
                "[2026-11-04 11:48:15] | \x1b[31mERROR\x1b[0m | gaia.daemon | d:1 | x",
                "error",
            ),
            (
                "2026-11-04 03:02:49,337 | pid:129436 | ERROR | gaia.agents | bad",
                "error",
            ),
            ("2026-11-04T03:42:44.512Z [INFO] Seeding complete", "info"),
            ("[2026-11-04T19:48:29.751Z] WARN [auto-updater] no network", "warning"),
            ("[2026-11-04T19:48:29.750Z] [error] Found nothing", "error"),
            (
                'INFO:     127.0.0.1:59156 - "POST /v1/gaia/query HTTP/1.1" 200 OK',
                "info",
            ),
            ("Traceback (most recent call last):", None),
        ],
    )
    def test_level_is_read_from_each_format(self, line, severity):
        assert reader.parse_line(line).severity == severity

    def test_colour_codes_are_stripped(self):
        line = reader.parse_line("[t] | \x1b[37mINFO\x1b[0m | gaia | x | hi")
        assert "\x1b" not in line.text


class TestRedactLines:
    def test_redacts_secret_lines_and_leaves_the_rest_byte_for_byte(self):
        data = b"".join(
            [
                b"plain line\n",
                b"Authorization: Bearer abcdefgh1234\n",
                b"n_tokens = 512, token: 1 '<eos>'\n",
                b"ok token=zzz999 tail\n",
                b"\xff\xfe raw bytes stay\n",
                b"key " + SECRET.encode() + b"\n",
            ]
        )
        lines = reader.redact_lines(data, [SECRET]).split(b"\n")
        assert lines[0] == b"plain line"
        assert lines[1] == b"Authorization: [redacted]"
        assert lines[2] == b"n_tokens = 512, token: 1 '<eos>'"
        assert lines[3] == b"ok token=[redacted] tail"
        assert lines[4] == b"\xff\xfe raw bytes stay"
        assert SECRET.encode() not in b"\n".join(lines)


class TestDiagnostics:
    def test_gaia_mode_bundles_the_file_not_the_stream(self):
        from gaia.cli import _diag_lemonade_server_log

        assert _diag_lemonade_server_log() is None

    def test_users_server_log_is_bundled(self, monkeypatch):
        from gaia.cli import _diag_lemonade_server_log

        monkeypatch.setenv("LEMONADE_BASE_URL", "http://localhost:13305")

        async def fake(**_):
            r = reader.LogRead(
                owner="user", base_url="u", source="stream", available=True
            )
            r.lines = [reader.LogLine("a"), reader.LogLine("b")]
            return r

        monkeypatch.setattr(logs, "read_lemonade_log", fake)
        assert _diag_lemonade_server_log() == "a\nb\n"

    def test_unreadable_users_log_says_why(self, monkeypatch):
        from gaia.cli import _diag_lemonade_server_log

        monkeypatch.setenv("LEMONADE_BASE_URL", "http://localhost:13305")

        async def fake(**_):
            return reader.LogRead(owner="user", base_url="u", reason="not_running")

        monkeypatch.setattr(logs, "read_lemonade_log", fake)
        assert "not_running" in _diag_lemonade_server_log()
