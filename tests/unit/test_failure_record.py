# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A failed model call leaves one redacted local record, and sends nothing."""

import json
import os
import socket
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from gaia.llm import failure_record as fr
from gaia.llm.lemonade_client import LemonadeClientError
from gaia.llm.providers.lemonade import (
    LemonadeContextOverflowError,
    LemonadeNetworkError,
    LemonadeStreamCutOffError,
    LemonadeUpstreamTimeoutError,
)

pytestmark = pytest.mark.failure_records

KEY = "sk-local-lemonade-key-0123456789"
EMBEDDED_KEY = "embedded-key-must-not-ship-42"
LOCAL = "http://127.0.0.1:51002/api/v1"


class _Resp:
    def __init__(self, body):
        self.status_code = 200
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A GAIA home with GAIA's own Lemonade running, and every probe faked."""
    gaia_dir = tmp_path / ".gaia"
    (gaia_dir / "lemonade").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("GAIA_HOME", str(gaia_dir))
    monkeypatch.delenv("GAIA_CONFIG_DIR", raising=False)
    monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
    monkeypatch.delenv("LEMONADE_API_KEY", raising=False)
    (gaia_dir / "lemonade" / "state.json").write_text(
        json.dumps({"pid": os.getpid(), "port": 51002, "api_key": EMBEDDED_KEY}),
        encoding="utf-8",
    )
    (gaia_dir / "lemonade" / "lemond.log").write_text(
        "\n".join(
            [
                "[Info] Server listening on 127.0.0.1:51002",
                f"[Process] llama-server --api-key {EMBEDDED_KEY} -m model.gguf",
                f"[Info] Authorization: Bearer {KEY}",
                f"[Error] could not open {tmp_path / 'models' / 'x.gguf'}",
                "[Error] llama-server exited with code 3",
            ]
        ),
        encoding="utf-8",
    )

    calls = []

    def fake_get(url, headers=None, timeout=None, **_):
        calls.append(url)
        if url.endswith("/health"):
            return _Resp(
                {
                    "status": "ok",
                    "version": "10.3.0",
                    "all_models_loaded": [
                        {
                            "model_name": "gemma4-it-e4b-FLM",
                            "recipe": "flm",
                            "device": "npu",
                        }
                    ],
                }
            )
        return _Resp(
            {
                "OS Version": "Linux-7.4.0-rc1",
                "Processor": "AMD Ryzen AI 7 350",
                "devices": {"amd_npu": {"available": True, "driver_version": "1.6"}},
                "recipes": {
                    "flm": {
                        "backends": {"npu": {"state": "installed", "version": "0.9.30"}}
                    }
                },
                "api_key": "should-be-blanked",
            }
        )

    monkeypatch.setattr(
        requests.Session, "get", lambda _s, url, **kw: fake_get(url, **kw)
    )

    class _Proc:
        def __init__(self, pid, name):
            self.info = {"pid": pid, "name": name}

        def status(self):
            return "running"

        def create_time(self):
            return 0.0

    import psutil

    monkeypatch.setattr(
        psutil,
        "process_iter",
        lambda attrs=None: [_Proc(11, "lemond"), _Proc(12, "flm"), _Proc(13, "bash")],
    )
    return SimpleNamespace(dir=gaia_dir / "failures", calls=calls, tmp=tmp_path)


def _read(home, record_id):
    return json.loads((home.dir / f"{record_id}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "exc, kind",
    [
        (LemonadeNetworkError(), "network"),
        (ConnectionRefusedError("[Errno 111] Connection refused"), "network"),
        (
            LemonadeClientError("model_load_error: llama-server failed to start"),
            "load_failure",
        ),
        (LemonadeStreamCutOffError(), "backend_crash"),
        (LemonadeUpstreamTimeoutError(), "timeout"),
        (requests.ReadTimeout("read timed out"), "timeout"),
    ],
)
def test_record_names_the_failure_and_the_machine(home, exc, kind):
    record_id = fr.record_failure(
        exc, where="agent_turn", model="gemma4-it-e4b-FLM", base_url=LOCAL, api_key=KEY
    )

    assert fr.RECORD_ID_RE.fullmatch(record_id)
    rec = _read(home, record_id)
    assert rec["kind"] == kind
    assert rec["error"]["class"].endswith(type(exc).__name__)
    assert rec["versions"]["gaia"]
    assert rec["os"]["kernel"]
    assert rec["model"]["id"] == "gemma4-it-e4b-FLM"
    ep = rec["endpoints"]
    assert ep["mode"] == "embedded"
    assert ep["agent_base_url"] == LOCAL
    assert ep["lemonade_url"] == LOCAL
    assert ep["agent_matches_lemonade"] is True
    assert ep["api_key_sent"] is True
    assert ep["embedded"]["pid_alive"] is True
    assert rec["health"]["all_models_loaded"][0]["recipe"] == "flm"
    assert rec["system_info"]["devices"]["amd_npu"]["available"] is True
    assert (
        rec["system_info"]["recipes"]["flm"]["backends"]["npu"]["version"] == "0.9.30"
    )
    assert {p["name"] for p in rec["processes"]} == {"lemond", "flm"}
    assert rec["logs"]["lines"][-1].endswith("exited with code 3")


def test_secrets_and_home_folder_never_reach_the_record(home):
    exc = LemonadeClientError(f"401 for key {KEY} via Authorization: Bearer {KEY}")
    record_id = fr.record_failure(exc, where="cli", base_url=LOCAL, api_key=KEY)

    text = (home.dir / f"{record_id}.json").read_text(encoding="utf-8")
    for secret in (KEY, EMBEDDED_KEY, "should-be-blanked", str(home.tmp)):
        assert secret not in text
    assert fr.REDACTED in text
    assert "~" in text  # the model path, without the user's folder


def test_remote_lemonade_is_named_but_never_contacted(home, monkeypatch):
    def no_socket(*args, **kwargs):
        raise AssertionError(f"network call during a failure record: {args}")

    monkeypatch.setattr(socket, "create_connection", no_socket)
    remote = "http://203.0.113.5:13305/api/v1"
    record_id = fr.record_failure(
        LemonadeNetworkError(), where="agent_turn", base_url=remote, api_key=None
    )

    rec = _read(home, record_id)
    assert home.calls == []
    assert rec["endpoints"]["agent_base_url"] == remote
    assert rec["endpoints"]["agent_matches_lemonade"] is False
    assert rec["endpoints"]["api_key_sent"] is False
    assert "skipped" in rec["health"]


def test_only_the_local_lemonade_is_probed(home):
    fr.record_failure(LemonadeNetworkError(), where="agent_turn", base_url=LOCAL)

    assert home.calls == [f"{LOCAL}/health", f"{LOCAL}/system-info"]
    assert all(fr._is_loopback(requests.utils.urlparse(u).hostname) for u in home.calls)


def test_url_credentials_are_dropped(home):
    url = "http://user:hunter2@127.0.0.1:51002/api/v1?key=abc"
    record_id = fr.record_failure(LemonadeNetworkError(), where="cli", base_url=url)

    text = (home.dir / f"{record_id}.json").read_text(encoding="utf-8")
    assert "hunter2" not in text and "key=abc" not in text


def test_record_is_capped_and_stays_valid_json(home, monkeypatch):
    log = home.tmp / ".gaia" / "lemonade" / "lemond.log"
    log.write_text("\n".join("x" * 1900 for _ in range(400)), encoding="utf-8")
    monkeypatch.setattr(fr, "MAX_RECORD_BYTES", 64 * 1024)

    record_id = fr.record_failure(LemonadeNetworkError(), where="cli", base_url=LOCAL)

    path = home.dir / f"{record_id}.json"
    assert path.stat().st_size <= 64 * 1024
    rec = json.loads(path.read_text(encoding="utf-8"))
    assert rec["logs"]["trimmed"] is True
    assert rec["logs"]["lines"]


def test_only_the_last_records_are_kept(home, monkeypatch):
    ids = iter(f"GF-20261104-1200{i:02d}-abcd" for i in range(fr.KEEP_RECORDS + 3))
    monkeypatch.setattr(fr, "_new_id", lambda: next(ids))
    for _ in range(fr.KEEP_RECORDS + 3):
        fr.record_failure(LemonadeNetworkError(), where="cli", base_url=LOCAL)

    kept = [p.stem for p in fr.recent_records()]
    assert len(kept) == fr.KEEP_RECORDS
    assert kept[0] == "GF-20261104-120003-abcd"


def test_a_restarted_lemonade_shows_between_records(home):
    first = fr.record_failure(LemonadeNetworkError(), where="cli", base_url=LOCAL)
    state = home.tmp / ".gaia" / "lemonade" / "state.json"
    state.write_text(json.dumps({"pid": 999999, "port": 51002}), encoding="utf-8")
    second = fr.record_failure(LemonadeNetworkError(), where="cli", base_url=LOCAL)

    assert _read(home, first)["lemonade_restarted_since_last_record"] is None
    assert _read(home, second)["lemonade_restarted_since_last_record"] is True


def test_refusals_the_user_can_fix_are_not_recorded():
    assert not fr.is_recordable(LemonadeContextOverflowError())
    assert fr.is_recordable(LemonadeNetworkError())


def test_an_unwritable_home_does_not_replace_the_users_error(home, monkeypatch):
    def refuse(*_a, **_k):
        raise PermissionError("read-only")

    monkeypatch.setattr(Path, "write_bytes", refuse)
    assert (
        fr.record_failure(LemonadeNetworkError(), where="cli", base_url=LOCAL) is None
    )


def test_note_names_the_record_and_says_nothing_was_sent():
    note = fr.record_note("GF-20261104-192233-3f2a")
    assert "GF-20261104-192233-3f2a" in note and "nothing was sent" in note
    assert fr.record_note(None) == ""


# ── Where the record is used ────────────────────────────────────────────────


def _agent(backend):
    from unittest.mock import patch

    from gaia.agents.base.agent import Agent
    from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

    class _Agent(Agent):
        def _get_system_prompt(self) -> str:
            return "test"

        def _register_tools(self) -> None:
            pass

    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _Agent(
            silent_mode=True, skip_lemonade=True, model_id=DEFAULT_MODEL_NAME
        )
    agent.chat = SimpleNamespace(llm_client=SimpleNamespace(_backend=backend))
    return agent


def test_failed_answer_keeps_gaias_words_and_names_the_record(home):
    agent = _agent(SimpleNamespace(base_url=LOCAL, api_key=KEY))
    said = "Lemonade could not load the model: llama-server exited with code 3"
    exc = LemonadeClientError(said)

    answer = agent._model_call_failed_answer(exc, None, 2, [])

    record_id = fr.RECORD_ID_RE.search(answer).group(0)
    assert (home.dir / f"{record_id}.json").is_file()
    assert "Lemonade could not load the model" in answer
    assert "nothing was sent" in answer
    assert _read(home, record_id)["where"] == "agent_turn"


def test_an_agent_bug_is_not_recorded_as_a_model_failure(home):
    agent = _agent(SimpleNamespace(base_url=LOCAL, api_key=None))

    answer = agent._model_call_failed_answer(KeyError("chunk"), None, 1, [])

    assert not fr.RECORD_ID_RE.search(answer)
    assert not home.dir.exists()


def test_a_refusal_that_already_says_why_is_not_recorded():
    from gaia.llm.lemonade_client import InsufficientMemoryError

    assert not fr.is_recordable(InsufficientMemoryError("Not enough free memory"))


def test_a_record_that_cannot_be_built_never_replaces_the_error(home, monkeypatch):
    def no_home():
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(fr, "failures_dir", no_home)
    assert (
        fr.record_failure(LemonadeNetworkError(), where="cli", base_url=LOCAL) is None
    )
    assert fr.record_local_failure(LemonadeNetworkError(), where="cli") is None


def test_url_passwords_and_any_spelling_of_home_are_redacted(home):
    tmp = str(home.tmp)
    text = f"GET https://u:hunter2@box/api/v1 from {tmp.lower()} and " + json.dumps(
        tmp + "\\models"
    )
    out = fr.redact(text)
    assert "hunter2" not in out
    assert tmp.lower() not in out.lower()
    assert json.dumps(tmp)[1:-1].lower() not in out.lower()


# Assembled at runtime so no key-shaped literal sits in the repo.
_PEM_BEGIN = "-----BEGIN " + "RSA PRIVATE KEY-----"
_PEM_END = "-----END " + "RSA PRIVATE KEY-----"
_PEM_BODY = "NOTAREALKEY" * 3


def test_private_keys_are_redacted_whole_even_when_truncated():
    pem = f"{_PEM_BEGIN}\n{_PEM_BODY}\nNOTAREALKEYTOO\n{_PEM_END}"
    out = fr.redact(f"before\n{pem}\nafter")
    assert _PEM_BODY not in out and "NOTAREALKEYTOO" not in out
    assert "before" in out and "after" in out
    assert _PEM_BODY not in fr.redact(f"tail\n{_PEM_BEGIN}\n{_PEM_BODY}")
    encrypted = f"{_PEM_BEGIN}\nProc-Type: 4,ENCRYPTED\n\n{_PEM_BODY}\n{_PEM_END}"
    assert _PEM_BODY not in fr.redact(encrypted)


def test_a_quoted_pem_header_alone_is_not_wiped_to_the_end():
    text = f"error: expected {_PEM_BEGIN}\nthen the rest of the log\n"
    assert fr.redact(text) == text
    assert fr.matched_pattern_names(text) == []


def test_header_like_lines_after_a_pem_header_do_not_backtrack():
    text = f"{_PEM_BEGIN}\n" + "-:\r\n" * 5000 + "!"
    start = time.perf_counter()
    assert fr.matched_pattern_names(text) == []
    fr.redact(text)
    assert time.perf_counter() - start < 5


def test_a_json_escaped_or_indented_private_key_is_caught_and_redacted():
    body = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC"
    cases = [
        json.dumps({"private_key": f"{_PEM_BEGIN}\n{body}\n{_PEM_END}\n", "n": 1}),
        json.dumps({"k": f"{_PEM_BEGIN}\r\n{body}\r\n{_PEM_END}\r\n"}),
        json.dumps(
            {"k": f"{_PEM_BEGIN}\nProc-Type: 4,ENCRYPTED\n\n{body}\n{_PEM_END}"}
        ),
        f"tls:\n  key: |\n    {_PEM_BEGIN}\n    {body}\n    {_PEM_END}\n",
    ]
    for text in cases:
        assert fr.matched_pattern_names(text) == ["PEM private key"], text
        assert body not in fr.redact(text), text
    escaped = json.dumps({"private_key": f"{_PEM_BEGIN}\n{body}\n{_PEM_END}", "n": 1})
    assert json.loads(fr.redact(escaped))["n"] == 1


def test_a_json_quoted_pem_header_alone_is_not_flagged():
    text = json.dumps({"msg": f"expected {_PEM_BEGIN}\nthen the rest"})
    assert fr.matched_pattern_names(text) == []
    assert fr.redact(text) == text


@pytest.mark.parametrize(
    "first, line, count",
    [
        ("\\n", "-:\\n", 20000),
        ("\\n", "a:b\\r\\n", 20000),
        ("\n", " \\n", 20000),
        ("\n", " ", 100000),
    ],
    ids=["escaped-header", "escaped-crlf-header", "indent-escaped", "spaces"],
)
def test_escaped_header_lines_after_a_pem_header_do_not_backtrack(first, line, count):
    text = _PEM_BEGIN + first + line * count + "!"
    start = time.perf_counter()
    assert fr.matched_pattern_names(text) == []
    fr.redact(text)
    assert time.perf_counter() - start < 5


_ASSIGN = "key/token/password assignment"
_PREFIX = "provider key prefix (sk-, ghp_, xoxb-...)"
_JSON_AUTH = "auth header or cookie in JSON"


# (before, secret, after): assembled at runtime so no credential sits in the repo.
@pytest.mark.parametrize(
    "before, secret, after, kind",
    [
        ("secret_access_key = ", "a1" * 32, "", _ASSIGN),
        ("AWS_SECRET_ACCESS_KEY=", "x" * 40, "", _ASSIGN),
        ("access_key_id = ", "b2" * 16, "", _ASSIGN),
        ("id ", "AKIA" + "ABCDEFGHIJKLMNOP", "", "AWS/R2-style access key ID"),
        ("", "github_" + "pat_" + "A1" * 15, "", _PREFIX),
        ("", "ghu_" + "Z9" * 8, "", _PREFIX),
        ("", "hf_" + "c" * 34, "", "Hugging Face token"),
        ("", "AI" + "za" + "d" * 35, "", "Google API key"),
        (
            "",
            "eyJ" + "hbGciOiJI" + ".eyJ" + "zdWIiOiIx" + "." + "e" * 16,
            "",
            "JSON web token",
        ),
        ('{"authorization": "', "f" * 20, '"}', _JSON_AUTH),
        ('{"name": "Cookie", "value": "', "sid=" + "g" * 12, '"}', _JSON_AUTH),
        ('{"x-api-key": "', "h" * 20, '"}', _JSON_AUTH),
    ],
    ids=lambda v: v if v in (_ASSIGN, _PREFIX, _JSON_AUTH) else None,
)
def test_common_credential_formats_are_caught_and_redacted(before, secret, after, kind):
    text = before + secret + after
    assert kind in fr.matched_pattern_names(text)
    out = fr.redact(text)
    assert fr.REDACTED in out and secret not in out


@pytest.mark.parametrize(
    "text",
    [
        "max_tokens=500 tokens_per_second: 3",
        '{"authorization": "[redacted]"}',
        '{"name": "Content-Type", "value": "application/json"}',
        "AKIA is a prefix; AKIAshort",
        "the hf_ prefix and an eyJ string",
        "access_key_id is a field name",
    ],
)
def test_lookalikes_are_not_flagged(text):
    assert fr.matched_pattern_names(text) == []


def test_matched_pattern_names_name_patterns_never_values():
    assert fr.matched_pattern_names("agent answered in 3.2s; basic") == []
    found = fr.matched_pattern_names(f"Authorization: Bearer {KEY}")
    assert found and all(KEY not in name for name in found)
    pem = f"{_PEM_BEGIN}\n{_PEM_BODY}\n{_PEM_END}"
    assert fr.matched_pattern_names(pem) == ["PEM private key"]


def test_probe_ignores_proxies_and_redirects(monkeypatch):
    seen = {}

    def fake(session, url, **kw):
        seen.update(trust_env=session.trust_env, **kw)
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(requests.Session, "get", fake)
    fr._probe_json(f"{LOCAL}/health", None)
    assert seen["trust_env"] is False and seen["allow_redirects"] is False


def test_cli_records_lemonade_failures_only(home, monkeypatch):
    from gaia.cli import _cli_error_text

    monkeypatch.setenv("LEMONADE_BASE_URL", LOCAL)
    assert fr.RECORD_ID_RE.search(_cli_error_text(LemonadeNetworkError()))
    assert not fr.RECORD_ID_RE.search(
        _cli_error_text(LemonadeNetworkError(), local=False)
    )


def test_typed_failure_keeps_its_advice_and_adds_the_record(home):
    agent = _agent(SimpleNamespace(base_url=LOCAL, api_key=None))
    exc = LemonadeNetworkError()

    answer = agent._model_call_failed_answer(exc, exc.user_message, 1, [])

    assert "Couldn't reach the local LLM" in answer
    assert fr.RECORD_ID_RE.search(answer)


def test_gaias_own_server_that_did_not_come_back_names_the_record(home):
    agent = _agent(SimpleNamespace(base_url=LOCAL, api_key=None))
    agent.chat.llm_client.gaia_lemonade_stopped = lambda: True
    agent._turn_model_retries["server_stopped"] = True

    answer = agent._model_call_failed_answer(LemonadeNetworkError(), None, 1, [])

    assert "could not restart it" in answer
    assert fr.RECORD_ID_RE.search(answer)


def test_a_model_not_served_by_lemonade_gets_no_record(home):
    agent = _agent(None)  # e.g. a Claude client: no Lemonade to describe

    answer = agent._model_call_failed_answer(LemonadeClientError("boom"), None, 1, [])

    assert "Boom" in answer
    assert not fr.RECORD_ID_RE.search(answer)
    assert not home.dir.exists()


def test_agent_ui_error_names_the_record(home, monkeypatch):
    from gaia.ui import _chat_helpers

    monkeypatch.setenv("LEMONADE_BASE_URL", LOCAL)
    text = _chat_helpers._model_failure_text(
        LemonadeNetworkError(), "gemma4-it-e4b-FLM"
    )

    assert text.startswith("Couldn't reach the local LLM")
    record_id = fr.RECORD_ID_RE.search(text).group(0)
    assert _read(home, record_id)["model"]["id"] == "gemma4-it-e4b-FLM"


def test_agent_ui_leaves_non_model_errors_alone(home):
    from gaia.ui import _chat_helpers

    assert _chat_helpers._model_failure_text(KeyError("tool"), "m") is None
    assert not home.dir.exists()


def test_a_server_that_refuses_health_is_not_asked_for_devices(home, monkeypatch):
    def refused(_session, url, **_):
        home.calls.append(url)
        raise requests.ConnectionError("[Errno 111] Connection refused")

    monkeypatch.setattr(requests.Session, "get", refused)
    record_id = fr.record_failure(LemonadeNetworkError(), where="cli", base_url=LOCAL)

    rec = _read(home, record_id)
    assert home.calls == [f"{LOCAL}/health"]
    assert "Connection refused" in rec["health"]["error"]
    assert rec["system_info"] is None


# ── #6632: an FLM crash during the first NPU load ───────────────────────────


class _HTTPFailure(LemonadeClientError):
    """A Lemonade refusal carrying its HTTP response, as ``_send_request`` raises."""

    def __init__(self, message, status, body):
        super().__init__(message)
        self.response = SimpleNamespace(status_code=status, text=body)


def test_flm_crash_during_load_record_says_why(home, monkeypatch):
    """The record from one failure answers what #6632 couldn't: what GAIA asked for,
    why that window, what Lemonade answered, and whether FLM died."""
    from gaia.llm import call_ledger
    from gaia.llm import lemonade_client as lc
    from gaia.llm.model_fit import FitVerdict

    model = "gemma4-it-e4b-FLM"
    call_ledger.clear()
    log = home.tmp / ".gaia" / "lemonade" / "lemond.log"
    log.write_text(
        "[Info] Loading gemma4-it-e4b-FLM with recipe flm\n"
        "[Process] flm serve --ctx-len 65536\n"
        "[Error] [Process] flm exited with signal 11 (Segmentation fault)\n",
        encoding="utf-8",
    )

    # How the window was chosen: sized to the PC, then to the memory free now.
    monkeypatch.delenv("GAIA_CTX_SIZE", raising=False)
    monkeypatch.setattr(lc, "_chosen_ctx_size", lambda *_: None)
    monkeypatch.setattr(lc, "_model_ctx_size", lambda *_: 131072)
    monkeypatch.setattr(lc, "fit_ctx_to_free_memory", lambda m, ctx, url=None: 65536)
    ctx = lc.resolve_ctx_size(model=model, base_url=LOCAL)
    assert ctx == 65536

    client = lc.LemonadeClient(
        base_url=LOCAL, api_key=KEY, keep_alive=True, verbose=False
    )
    monkeypatch.setattr(lc, "free_memory_verdict", lambda *_: FitVerdict(fits=True))
    monkeypatch.setattr(client, "_model_recipe", lambda _m: "flm")
    crash = _HTTPFailure(
        "Lemonade /load failed with status 500: flm serve exited unexpectedly",
        500,
        '{"error": {"message": "flm serve exited unexpectedly (signal 11)"}}',
    )

    def crashed_load(*_a, **_k):
        raise crash

    monkeypatch.setattr(client, "_post_load_with_transient_retry", crashed_load)
    with pytest.raises(Exception):
        client._load_model_leased(model, ctx_size=ctx, prompt=False, load_retries=0)

    # GAIA's first chat on the NPU then finds the server gone.
    def refused(*_a, **_k):
        raise requests.ConnectionError("[Errno 104] Connection reset by peer")

    monkeypatch.setattr(client, "_chat_completions_once", refused)
    messages = [
        {"role": "system", "content": "x" * 4000},
        {"role": "user", "content": "hi"},
    ]
    tools = [
        {"type": "function", "function": {"name": f"t{i}", "parameters": {}}}
        for i in range(12)
    ]
    with pytest.raises(requests.ConnectionError) as failed:
        client.chat_completions(
            model, messages, tools=tools, max_completion_tokens=2048, stream=False
        )

    record_id = fr.record_failure(
        failed.value, where="agent_turn", model=model, base_url=LOCAL, api_key=KEY
    )
    rec = _read(home, record_id)
    req = rec["requests"]

    choice = req["ctx_choices"][model]
    assert choice["ctx_size"] == 65536
    assert choice["for_capacity"] == 131072 and choice["after_free_memory"] == 65536
    assert "memory free now" in choice["how"]
    assert req["memory_checks"][model]["fits"] is True

    load = next(c for c in req["calls"] if c["kind"] == "load")
    assert load["request"]["model_name"] == model
    assert load["request"]["ctx_size"] == 65536
    assert load["outcome"] == "failed" and load["http_status"] == 500
    assert "signal 11" in load["http_body"]
    assert load["started"] and load["ended"] and load["seconds"] >= 0

    chat = next(c for c in req["calls"] if c["kind"] == "chat")
    assert chat["model"] == model
    assert chat["tools"] == 12 and chat["tool_schema_bytes"] > 0
    assert chat["prompt_tokens_estimate"] > 1000
    assert chat["max_tokens"] == 2048 and chat["stream"] is False
    assert "Connection reset" in chat["error"]
    assert chat["started"] <= chat["ended"]
    assert "x" * 100 not in json.dumps(rec)  # sizes, never the words

    assert rec["kind"] == "network"
    assert rec["npu"]["flm_backend"]["backends"]["npu"]["version"] == "0.9.30"
    assert rec["npu"]["device"]["available"] is True
    assert {p["name"] for p in rec["processes"]} >= {"lemond", "flm"}
    assert rec["endpoints"]["embedded"]["pid_alive"] is True
    assert any("signal 11" in line for line in rec["logs"]["lines"])
    assert KEY not in json.dumps(rec)


def test_a_dead_lemond_reports_how_it_ended(home, monkeypatch):
    from gaia.llm import lemonade_embedded

    state = home.tmp / ".gaia" / "lemonade" / "state.json"
    state.write_text(json.dumps({"pid": 4_000_000, "port": 51002}), encoding="utf-8")
    monkeypatch.setattr(lemonade_embedded, "_exit_details", lambda pid: (-9, None))

    record_id = fr.record_failure(LemonadeNetworkError(), where="cli", base_url=LOCAL)

    embedded = _read(home, record_id)["endpoints"]["embedded"]
    assert embedded["pid_alive"] is False
    assert embedded["exit"] == "killed by signal 9"


def test_a_load_made_by_another_gaia_process_reaches_the_record(home):
    """The Agent UI backend loads; the agent's own process chats and fails."""
    from gaia.llm import call_ledger

    call_ledger.clear()
    call_ledger.note_ctx_choice("gemma4-it-e4b-FLM", 65536, "NPU default")
    noted = call_ledger.begin(
        "load", request={"model_name": "gemma4-it-e4b-FLM", "ctx_size": 65536}
    )
    call_ledger.end(noted, error=LemonadeClientError("flm exited"))
    path = call_ledger.shared_loads_path()
    loads = json.loads(path.read_text(encoding="utf-8"))
    loads[-1]["pid"] = 1  # as if the UI backend had made it
    path.write_text(json.dumps(loads), encoding="utf-8")
    call_ledger.clear()

    record_id = fr.record_failure(
        LemonadeNetworkError(), where="agent_turn", base_url=LOCAL
    )

    other = _read(home, record_id)["requests"]["loads_by_other_processes"]
    assert other[-1]["request"]["ctx_size"] == 65536
    assert other[-1]["ctx_choice"]["how"] == "NPU default"
    assert "flm exited" in other[-1]["error"]
    assert not fr.recent_records()[-1].name.startswith("recent-loads")


def test_loads_finished_at_once_by_several_processes_all_kept(tmp_path):
    """The UI backend and the agent can finish a load together; neither note is lost."""
    import subprocess
    import sys

    from gaia.llm import call_ledger

    script = (
        "import sys, time\n"
        "from gaia.llm import call_ledger\n"
        "start = float(sys.argv[2])\n"
        "while time.time() < start: time.sleep(0.001)\n"
        "call_ledger._share_load({'kind': 'load', 'request': {'model_name': sys.argv[1]}})\n"
    )
    env = {**os.environ, "GAIA_HOME": str(tmp_path / ".gaia")}
    env["PYTHONPATH"] = os.pathsep.join(
        [str(Path(fr.__file__).resolve().parents[2]), env.get("PYTHONPATH", "")]
    )
    start = __import__("time").time() + 3
    models = [f"model-{i}" for i in range(call_ledger.KEEP_SHARED_LOADS)]
    procs = [
        subprocess.Popen([sys.executable, "-c", script, m, str(start)], env=env)
        for m in models
    ]
    assert [p.wait(timeout=60) for p in procs] == [0] * len(procs)

    saved = json.loads(
        (tmp_path / ".gaia" / "failures" / "recent-loads.json").read_text("utf-8")
    )
    assert sorted(load["request"]["model_name"] for load in saved) == models
