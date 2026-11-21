# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""`gaia -p`: one headless turn, the answer (or one JSON object) on stdout."""

import io
import json
import logging
import os
import sys
import threading
import time
import types
from unittest.mock import MagicMock, patch

import pytest

from gaia import print_mode
from gaia.cli import build_parser

# ---------------------------------------------------------------------------
# Argparse wiring
# ---------------------------------------------------------------------------


@pytest.fixture
def parser():
    return build_parser()


def test_p_with_text_sets_the_prompt_and_no_subcommand(parser):
    args = parser.parse_args(["-p", "hello world"])
    assert args.print_prompt == "hello world"
    assert args.action is None


def test_long_form_print_is_the_same_flag(parser):
    assert parser.parse_args(["--print", "hi"]).print_prompt == "hi"


def test_bare_p_means_read_stdin(parser):
    assert parser.parse_args(["-p"]).print_prompt == print_mode.READ_STDIN


def test_empty_p_is_not_a_request_to_read_stdin(parser):
    assert parser.parse_args(["-p", ""]).print_prompt == ""


def test_no_p_leaves_print_prompt_unset(parser):
    assert parser.parse_args([]).print_prompt is None


@pytest.mark.parametrize("fmt", ["text", "json"])
def test_output_format_choices(parser, fmt):
    args = parser.parse_args(["-p", "x", "--output-format", fmt])
    assert args.print_output_format == fmt


def test_output_format_rejects_unknown(parser, capsys):
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["-p", "x", "--output-format", "yaml"])
    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_all_print_flags_parse_together(parser):
    args = parser.parse_args(
        [
            "-p",
            "x",
            "--model",
            "amd.DeepSeek-V4.1-Flash",
            "--max-steps",
            "7",
            "--dangerously-skip-permissions",
            "--base-url",
            "http://h:1/api/v1",
        ]
    )
    assert args.print_model == "amd.DeepSeek-V4.1-Flash"
    assert args.print_max_steps == 7
    assert args.print_skip_permissions is True
    assert args.base_url == "http://h:1/api/v1"


def test_print_flag_defaults(parser):
    args = parser.parse_args(["-p", "x"])
    assert args.print_output_format is None
    assert args.print_max_steps is None
    assert args.print_model is None
    assert args.print_skip_permissions is False
    assert args.print_verbose is False


def test_bare_p_followed_by_a_subcommand_name_takes_it_as_the_prompt(parser):
    # nargs="?" swallows the next word: `gaia -p chat` asks the agent "chat".
    args = parser.parse_args(["-p", "chat"])
    assert args.print_prompt == "chat"
    assert args.action is None


def test_prompt_with_spaces_that_starts_with_dashes_is_a_prompt(parser):
    # argparse treats a token containing a space as positional, never a flag.
    assert parser.parse_args(["-p", "--help me"]).print_prompt == "--help me"


def test_negative_number_prompt_is_a_prompt(parser):
    assert parser.parse_args(["-p", "-5"]).print_prompt == "-5"


def test_prompt_that_is_exactly_a_flag_is_parsed_as_that_flag(parser, capsys):
    # `gaia -p "--help"`: argparse sees --help, prints help and exits 0. The
    # only way to send that literal prompt is `--print=--help` or stdin.
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["-p", "--help"])
    assert exc.value.code == 0
    assert "usage:" in capsys.readouterr().out


def test_prompt_that_looks_like_an_unknown_flag_is_rejected(parser, capsys):
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["-p", "-x"])
    assert exc.value.code == 2
    assert "unrecognized arguments: -x" in capsys.readouterr().err


def test_equals_form_carries_a_flag_like_prompt(parser):
    assert parser.parse_args(["--print=--help"]).print_prompt == "--help"


# ---------------------------------------------------------------------------
# main() dispatch
# ---------------------------------------------------------------------------


def _run_main(monkeypatch, argv):
    from gaia import cli

    monkeypatch.setattr(sys, "argv", ["gaia", *argv])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    return exc.value.code


def test_main_rejects_p_with_a_subcommand(monkeypatch, capsys):
    run = MagicMock()
    monkeypatch.setattr(print_mode, "run", run)
    code = _run_main(monkeypatch, ["-p", "x", "chat"])
    assert code == 2
    assert "takes no subcommand" in capsys.readouterr().err
    run.assert_not_called()


def test_main_forwards_flags_and_exits_with_runs_code(monkeypatch):
    run = MagicMock(return_value=0)
    monkeypatch.setattr(print_mode, "run", run)
    code = _run_main(
        monkeypatch,
        [
            "-p",
            "hi",
            "--model",
            "m1",
            "--output-format",
            "json",
            "--max-steps",
            "3",
            "--attempts",
            "2",
            "--dangerously-skip-permissions",
            "--base-url",
            "http://h:1/api/v1",
        ],
    )
    assert code == 0
    run.assert_called_once_with(
        "hi",
        model="m1",
        output_format="json",
        verbose=False,
        max_steps=3,
        attempts=2,
        skip_permissions=True,
        full_access=False,
        base_url="http://h:1/api/v1",
        time_limit=None,
        disallowed_tools=(),
        no_web=False,
        no_skills=False,
        yes=False,
    )


def test_main_forwards_tool_restrictions(monkeypatch):
    run = MagicMock(return_value=0)
    monkeypatch.setattr(print_mode, "run", run)
    argv = ["-p", "hi", "--disallowed-tools", "a, b_*", "--disallowed-tools", "c"]
    assert _run_main(monkeypatch, [*argv, "--no-web", "--no-skills"]) == 0
    kwargs = run.call_args.kwargs
    assert kwargs["disallowed_tools"] == ["a", "b_*", "c"]
    assert kwargs["no_web"] is True
    assert kwargs["no_skills"] is True


def test_main_defaults_output_format_to_text(monkeypatch):
    run = MagicMock(return_value=1)
    monkeypatch.setattr(print_mode, "run", run)
    assert _run_main(monkeypatch, ["-p", "hi"]) == 1
    assert run.call_args.kwargs["output_format"] == "text"
    assert run.call_args.kwargs["skip_permissions"] is False
    assert run.call_args.kwargs["attempts"] == 1


@pytest.mark.parametrize(
    "argv",
    [
        ["--model", "m"],
        ["--output-format", "json"],
        ["--verbose"],
        ["--max-steps", "3"],
        ["--time-limit", "600"],
        ["--attempts", "2"],
        ["--dangerously-skip-permissions"],
        ["--full-access"],
        ["--disallowed-tools", "search_web"],
        ["--no-web"],
        ["--no-skills"],
        ["--model", "m", "chat"],
    ],
)
def test_print_only_flags_without_p_are_a_usage_error(monkeypatch, capsys, argv):
    # `gaia --model m chat` used to drop m silently in favour of chat's default.
    run = MagicMock()
    monkeypatch.setattr(print_mode, "run", run)
    assert _run_main(monkeypatch, argv) == 2
    assert "only applies to -p" in capsys.readouterr().err
    run.assert_not_called()


@pytest.mark.parametrize("value", ["0", "-1", "two"])
def test_max_steps_must_be_a_positive_integer(parser, capsys, value):
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["-p", "x", "--max-steps", value])
    assert exc.value.code == 2
    assert "--max-steps" in capsys.readouterr().err


def test_main_forwards_verbose(monkeypatch):
    run = MagicMock(return_value=0)
    monkeypatch.setattr(print_mode, "run", run)
    _run_main(monkeypatch, ["-p", "hi", "--output-format", "json", "--verbose"])
    assert run.call_args.kwargs["verbose"] is True


# ---------------------------------------------------------------------------
# read_prompt
# ---------------------------------------------------------------------------


class _FakeStdin(io.StringIO):
    def __init__(self, text, tty=False):
        super().__init__(text)
        self._tty = tty

    def isatty(self):
        return self._tty


def test_read_prompt_prefers_the_argument(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _FakeStdin("from stdin"))
    assert print_mode.read_prompt("from flag") == "from flag"


def test_read_prompt_reads_and_strips_stdin(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _FakeStdin("  line one\nline two\n\n"))
    assert print_mode.read_prompt(print_mode.READ_STDIN) == "line one\nline two"


def test_read_prompt_decodes_raw_stdin_as_utf8_and_strips_bom(monkeypatch):
    import io as _io

    raw = _io.BytesIO("\ufeffcafé 日本 🎉\n".encode("utf-8"))
    stdin = _io.TextIOWrapper(raw, encoding="cp1252")
    monkeypatch.setattr(sys, "stdin", stdin)
    assert print_mode.read_prompt(print_mode.READ_STDIN) == "café 日本 🎉"


def test_read_prompt_rejects_non_utf8_raw_stdin(monkeypatch):
    import io as _io

    raw = _io.BytesIO(b"\xff\xfehello")
    stdin = _io.TextIOWrapper(raw, encoding="cp1252")
    monkeypatch.setattr(sys, "stdin", stdin)
    with pytest.raises(ValueError, match="UTF-8"):
        print_mode.read_prompt(print_mode.READ_STDIN)


@pytest.mark.parametrize("prompt", ["", "   "])
def test_an_empty_prompt_is_refused_without_touching_stdin(monkeypatch, prompt):
    # A stdin that never closes: reading it is the hang this guards against.
    class _Open:
        def isatty(self):
            return False

        def read(self):
            raise AssertionError("read stdin for an explicitly empty prompt")

    monkeypatch.setattr(sys, "stdin", _Open())
    with pytest.raises(ValueError, match="empty prompt"):
        print_mode.read_prompt(prompt)


def test_read_prompt_rejects_a_terminal(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _FakeStdin("", tty=True))
    with pytest.raises(ValueError, match="needs a prompt"):
        print_mode.read_prompt(print_mode.READ_STDIN)


def test_read_prompt_rejects_missing_stdin(monkeypatch):
    monkeypatch.setattr(sys, "stdin", None)
    with pytest.raises(ValueError, match="needs a prompt"):
        print_mode.read_prompt(None)


@pytest.mark.parametrize("text", ["", "   \n\t\n"])
def test_read_prompt_rejects_empty_stdin(monkeypatch, text):
    monkeypatch.setattr(sys, "stdin", _FakeStdin(text))
    with pytest.raises(ValueError, match="empty prompt"):
        print_mode.read_prompt(print_mode.READ_STDIN)


@pytest.mark.skipif(
    sys.platform != "win32", reason="the console code page is Windows-only"
)
def test_gaia_p_reads_a_prompt_redirected_from_a_file(tmp_path):
    # `gaia -p < prompt.txt`: setting the console code page once shared this
    # stdin, and cmd.exe moved the file to its end before the prompt was read.
    import subprocess

    prompt = tmp_path / "prompt.txt"
    prompt.write_bytes(b"fix the bug in a.py\n")
    echo = (
        "import sys\n"
        "from gaia import print_mode\n"
        "_read = print_mode.read_prompt\n"
        "def _echo(p):\n"
        "    print('PROMPT:' + _read(p)); sys.exit(0)\n"
        "print_mode.read_prompt = _echo\n"
        "from gaia.cli import main\n"
        "sys.exit(main())\n"
    )
    with open(prompt, "rb") as stdin:
        done = subprocess.run(
            [sys.executable, "-c", echo, "-p"],
            stdin=stdin,
            capture_output=True,
            cwd=tmp_path,
            timeout=120,
            check=False,
        )
    out = done.stdout.decode("utf-8", errors="replace")
    assert "PROMPT:fix the bug in a.py" in out, done.stderr.decode(errors="replace")


# ---------------------------------------------------------------------------
# summarize
# ---------------------------------------------------------------------------

_KEYS = {
    "type",
    "is_error",
    "status",
    "result",
    "model",
    "num_steps",
    "num_tool_calls",
    "input_tokens",
    "cached_tokens",
    "output_tokens",
    "duration_s",
    "error_count",
}


def test_summarize_shape_and_tool_call_count():
    result = {
        "status": "success",
        "result": "42",
        "steps_taken": 3,
        "input_tokens": 100,
        "output_tokens": 20,
        "error_count": 0,
        "conversation": [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": {"tool": "read_file"}},
            {"role": "tool", "name": "read_file", "content": "..."},
            {"role": "tool", "name": "search", "content": "..."},
            {"role": "assistant", "content": {"answer": "42"}},
            "not-a-dict",
            None,
        ],
    }
    record = print_mode.summarize(result, "m1", 1.23456)
    assert set(record) == _KEYS
    assert record == {
        "type": "result",
        "is_error": False,
        "status": "success",
        "result": "42",
        "model": "m1",
        "num_steps": 3,
        "num_tool_calls": 2,
        "input_tokens": 100,
        "cached_tokens": 0,
        "output_tokens": 20,
        "duration_s": 1.23,
        "error_count": 0,
    }


def test_summarize_on_an_empty_result_is_an_error_record():
    record = print_mode.summarize({}, None, 0.0)
    assert set(record) == _KEYS | {"error"}
    assert record["is_error"] is True
    assert record["status"] == "error"
    assert record["result"] == ""
    assert record["num_steps"] == 0
    assert record["num_tool_calls"] == 0
    assert record["model"] is None


def test_summarize_tolerates_none_values():
    record = print_mode.summarize(
        {
            "status": None,
            "result": None,
            "steps_taken": None,
            "conversation": None,
            "input_tokens": None,
            "output_tokens": None,
            "error_count": None,
        },
        "m",
        0.5,
    )
    assert record["status"] == "error"
    assert record["result"] == ""
    assert record["input_tokens"] == 0


@pytest.mark.parametrize("status", ["failed", "error", "incomplete", "max_steps"])
def test_summarize_any_non_success_status_is_an_error(status):
    assert print_mode.summarize({"status": status}, "m", 0)["is_error"] is True


# ---------------------------------------------------------------------------
# run() with a stubbed agent and server
# ---------------------------------------------------------------------------


class _StubConsole:
    silence_final_answer = False
    auto_approve_gated_tools = None


class _StubAgent:
    """Records its config and returns whatever the test sets as the result."""

    instances = []
    result = {"status": "success", "result": "pong"}

    def __init__(self, config):
        self.config = config
        self.model_id = getattr(config, "model_id", None)
        self.console = _StubConsole()
        self.queries = []
        self.reset_called = False
        self.drained = False
        _StubAgent.instances.append(self)

    def _ensure_tool_loader_reset(self):
        self.reset_called = True

    def process_query(self, text):
        self.queries.append(text)
        # Anything a real agent logs must stay off stdout; this one prints
        # nothing, so stdout holds only what run() prints.
        return type(self).result

    def wait_for_memory_extraction(self, timeout):
        self.drained = True
        return True


class _StubConfig:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _Catalog:
    """Lemonade as print mode sees it: records every call, answers the catalog."""

    calls: list = []
    downloaded = True
    listed = True
    cloud = False
    size_gb = 5.6
    #: What a download streams to ``on_progress``, then raises if it's an error.
    events: list = []
    download_error = None

    def __init__(self, **kwargs):
        _Catalog.calls.append(("__init__", kwargs))

    def _is_cloud_model(self, model):
        _Catalog.calls.append(("_is_cloud_model", model))
        return _Catalog.cloud

    def get_model_info(self, model):
        _Catalog.calls.append(("get_model_info", model))
        return {
            "id": model,
            "size_gb": _Catalog.size_gb,
            "downloaded": _Catalog.downloaded,
            "listed": _Catalog.listed,
        }

    def ensure_model_downloaded(self, model, **kwargs):
        _Catalog.calls.append(("ensure_model_downloaded", model, kwargs))
        for event in _Catalog.events:
            kwargs["on_progress"](event)
        if _Catalog.download_error is not None:
            raise _Catalog.download_error
        return True

    def __getattr__(self, name):
        def record(*args, **kwargs):
            _Catalog.calls.append((name, args, kwargs))

        return record


@pytest.fixture
def stub_env(monkeypatch):
    """Serve _StubAgent as the flagship and stub out the model server."""
    _StubAgent.instances = []
    _StubAgent.result = {"status": "success", "result": "pong"}
    agent_mod = types.ModuleType("gaia_agent.agent")
    agent_mod.GaiaAgent = _StubAgent
    agent_mod.GaiaAgentConfig = _StubConfig
    monkeypatch.setitem(sys.modules, "gaia_agent", types.ModuleType("gaia_agent"))
    monkeypatch.setitem(sys.modules, "gaia_agent.agent", agent_mod)

    init = MagicMock(return_value=(True, "http://detected:13305/api/v1"))
    monkeypatch.setattr("gaia.cli.initialize_lemonade_for_agent", init)
    _Catalog.calls = []
    _Catalog.downloaded = True
    _Catalog.listed = True
    _Catalog.cloud = False
    _Catalog.size_gb = 5.6
    _Catalog.events = []
    _Catalog.download_error = None
    monkeypatch.setattr("gaia.llm.lemonade_client.LemonadeClient", _Catalog)

    manager = MagicMock()
    monkeypatch.setattr("gaia.logger.configure", lambda *args, **kwargs: manager)
    # print_mode's logger is bound at import; a logging test that ran first can
    # leave its level at INFO, which would print a caught traceback to stderr.
    monkeypatch.setattr("gaia.print_mode.log", MagicMock())
    return init


def _agent():
    assert len(_StubAgent.instances) == 1
    return _StubAgent.instances[0]


def test_run_text_prints_only_the_answer(stub_env, capsys):
    assert print_mode.run("say pong", model="m1") == 0
    captured = capsys.readouterr()
    assert captured.out == "pong\n"
    agent = _agent()
    assert agent.queries == ["say pong"]
    assert agent.reset_called
    assert agent.drained
    assert agent.console.silence_final_answer is True


def test_run_json_prints_one_parseable_object(stub_env, capsys):
    _StubAgent.result = {
        "status": "success",
        "result": "pong",
        "steps_taken": 2,
        "conversation": [{"role": "tool", "name": "t", "content": "x"}],
        "input_tokens": 11,
        "output_tokens": 3,
    }
    assert print_mode.run("q", model="m1", output_format="json") == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1  # exactly one line
    record = json.loads(out)
    assert set(record) == _KEYS
    assert record["result"] == "pong"
    assert record["model"] == "m1"
    assert record["num_tool_calls"] == 1
    assert record["is_error"] is False


@pytest.mark.parametrize("fmt", ["text", "json"])
def test_run_exits_1_on_a_non_success_status(stub_env, capsys, fmt):
    _StubAgent.result = {"status": "failed", "result": "partial"}
    assert print_mode.run("q", model="m", output_format=fmt) == 1
    out = capsys.readouterr().out
    if fmt == "json":
        record = json.loads(out)
        assert record["is_error"] is True
        assert record["status"] == "failed"


def test_run_exits_1_when_the_agent_returns_none(stub_env, capsys):
    _StubAgent.result = None
    assert print_mode.run("q", model="m", output_format="json") == 1
    assert json.loads(capsys.readouterr().out)["status"] == "error"


def test_run_survives_a_result_missing_every_key(stub_env, capsys):
    _StubAgent.result = {"status": "success"}
    assert print_mode.run("q", model="m", output_format="json") == 0
    record = json.loads(capsys.readouterr().out)
    assert record["result"] == ""
    assert record["num_steps"] == 0


def test_run_text_keeps_newlines_and_unicode_verbatim(stub_env, capsys):
    answer = "line 1\nline 2 — ünïcödé 🧠\n\tindented"
    _StubAgent.result = {"status": "success", "result": answer}
    assert print_mode.run("q", model="m") == 0
    assert capsys.readouterr().out == answer + "\n"


def test_run_json_escapes_newlines_and_keeps_unicode(stub_env, capsys):
    answer = 'a\nb "quoted" — 🧠'
    _StubAgent.result = {"status": "success", "result": answer}
    assert print_mode.run("q", model="m", output_format="json") == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1  # the answer's newline is escaped, not raw
    assert "🧠" in out  # ensure_ascii=False
    assert json.loads(out)["result"] == answer


@pytest.mark.parametrize("skip", [True, False])
def test_gated_tools_follow_skip_permissions(stub_env, skip):
    print_mode.run("q", model="m", skip_permissions=skip)
    assert _agent().console.auto_approve_gated_tools is skip


def test_allowed_paths_is_the_cwd(stub_env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    print_mode.run("q", model="m")
    assert _agent().config.allowed_paths == [os.getcwd()]


def test_agent_config_is_headless(stub_env):
    print_mode.run("q", model="m1", max_steps=5)
    cfg = _agent().config
    assert cfg.silent_mode is True
    assert cfg.streaming is True
    assert cfg.max_steps == 5
    assert cfg.model_id == "m1"
    assert cfg.base_url == "http://detected:13305/api/v1"


def test_explicit_base_url_wins_over_the_detected_one(stub_env):
    print_mode.run("q", model="m", base_url="http://remote:1/api/v1")
    assert _agent().config.base_url == "http://remote:1/api/v1"
    assert stub_env.call_args.kwargs["base_url"] == "http://remote:1/api/v1"


def test_server_is_started_quietly_for_the_chosen_model(stub_env):
    print_mode.run("q", model="amd.DeepSeek-V4.1-Flash")
    kwargs = stub_env.call_args.kwargs
    assert kwargs["quiet"] is True
    assert kwargs["model"] == "amd.DeepSeek-V4.1-Flash"


def _config(monkeypatch, *, default_model=None, last_model=None):
    cfg = MagicMock(default_model=default_model, last_model=last_model)
    cfg.resolve_model = (
        lambda requested, fallback: requested or default_model or fallback
    )
    monkeypatch.setattr("gaia.config.GaiaConfig.load", MagicMock(return_value=cfg))


def test_no_model_uses_config_default_model(stub_env, monkeypatch):
    _config(monkeypatch, default_model="cfg-model")
    print_mode.run("q")
    assert stub_env.call_args.kwargs["model"] == "cfg-model"
    assert _agent().config.model_id == "cfg-model"


def test_no_model_runs_the_model_the_apps_reopen_on(stub_env, monkeypatch):
    """A PC set up with a cloud model answers with it, as the apps do (#7734)."""
    _config(monkeypatch, last_model="fireworks.deepseek-v4p1-flash")
    assert print_mode.run("q") == 0
    assert _agent().config.model_id == "fireworks.deepseek-v4p1-flash"
    assert stub_env.call_args.kwargs["model"] == "fireworks.deepseek-v4p1-flash"
    # A cloud model needs no catalog check and nothing on disk.
    assert _Catalog.calls == []


def test_no_model_and_no_config_falls_back_to_the_shipped_default(
    stub_env, monkeypatch
):
    from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

    _config(monkeypatch)
    assert print_mode.run("q") == 0
    assert _agent().config.model_id == DEFAULT_MODEL_NAME


def _run_yes(*args, **kwargs):
    """``gaia -p --yes``: a download was agreed to in advance."""
    return print_mode.run(*args, yes=True, **kwargs)


def _downloads():
    return [c[1] for c in _Catalog.calls if c[0] == "ensure_model_downloaded"]


def _progress(done, total, file="model.gguf", **extra):
    return {
        "event": "progress",
        "file": file,
        "bytes_downloaded": done,
        "bytes_total": total,
        **extra,
    }


def test_a_local_model_that_is_not_downloaded_is_downloaded_then_run(
    stub_env, monkeypatch, capsys
):
    from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

    _config(monkeypatch)
    _Catalog.downloaded = False
    assert _run_yes("q") == 0
    out, err = capsys.readouterr()
    assert out == "pong\n"
    assert f"gaia -p: downloading {DEFAULT_MODEL_NAME} (5.6 GB)" in err
    assert f"gaia -p: downloaded {DEFAULT_MODEL_NAME}" in err
    assert _downloads() == [DEFAULT_MODEL_NAME]
    assert _agent().config.model_id == DEFAULT_MODEL_NAME
    # The model is fetched before the server is asked to load it.
    assert [c.kwargs["preload"] for c in stub_env.call_args_list] == [False, True]
    assert [c[0] for c in _Catalog.calls] == [
        "__init__",
        "_is_cloud_model",
        "get_model_info",
        "ensure_model_downloaded",
    ]


def test_a_model_named_with_model_is_downloaded_with_streamed_progress(
    stub_env, monkeypatch
):
    _config(monkeypatch, last_model="other")
    _Catalog.downloaded = False
    assert _run_yes("q", model="user.Added-27B-GGUF") == 0
    (call,) = [c for c in _Catalog.calls if c[0] == "ensure_model_downloaded"]
    assert call[1] == "user.Added-27B-GGUF"
    assert callable(call[2]["on_progress"])
    assert call[2]["show_progress"] is False
    assert _agent().config.model_id == "user.Added-27B-GGUF"


def test_download_progress_goes_to_stderr_and_json_stdout_stays_one_object(
    stub_env, monkeypatch, capsys
):
    _config(monkeypatch)
    _Catalog.downloaded = False
    _Catalog.size_gb = 2.0
    total = 2_000_000_000
    _Catalog.events = [
        _progress(step * total // 20, total, total_download_size=total)
        for step in range(21)
    ] + [{"event": "complete", "percent": 100}]
    assert _run_yes("q", model="m1", output_format="json") == 0
    out, err = capsys.readouterr()
    assert out.count("\n") == 1
    assert json.loads(out)["result"] == "pong"
    # Not a terminal: one plain line every 10%, and no carriage returns.
    lines = [line for line in err.splitlines() if "downloading m1:" in line]
    assert [line.split("downloading m1: ")[1].split("%")[0] for line in lines] == [
        str(p) for p in range(10, 101, 10)
    ]
    assert lines[4] == "gaia -p: downloading m1: 50% (1.0 / 2.0 GB)"
    assert "\r" not in err
    assert err.rstrip().endswith("gaia -p: downloaded m1")


class _Keyboard:
    """A terminal's stdin: what the user types at the download question."""

    def __init__(self, typed):
        self.typed = typed
        self.asked = 0

    def isatty(self):
        return True

    def readline(self):
        self.asked += 1
        if isinstance(self.typed, BaseException):
            raise self.typed
        return self.typed


@pytest.fixture
def at_a_terminal(monkeypatch):
    """stdin and stderr are a terminal; returns a setter for what is typed."""

    def typed(text):
        keyboard = _Keyboard(text)
        monkeypatch.setattr(sys, "stdin", keyboard)
        monkeypatch.setattr(
            print_mode, "_stream_is_tty", lambda stream: stream is not None
        )
        return keyboard

    return typed


@pytest.mark.parametrize("answer", ["y\n", "Y\n", "yes\n", " YES \n"])
@pytest.mark.parametrize("fmt", ["text", "json"])
def test_a_yes_at_the_question_downloads_then_answers(
    stub_env, monkeypatch, capsys, at_a_terminal, answer, fmt
):
    _config(monkeypatch)
    _Catalog.downloaded = False
    _Catalog.size_gb = 18.6
    keyboard = at_a_terminal(answer)
    assert print_mode.run("q", model="m1", output_format=fmt) == 0
    out, err = capsys.readouterr()
    assert keyboard.asked == 1
    assert err.startswith(
        "gaia -p: m1 is not downloaded (18.6 GB). Download it now? [y/N] "
    )
    assert _downloads() == ["m1"]
    assert out.count("\n") == 1
    assert (json.loads(out)["result"] if fmt == "json" else out.strip()) == "pong"


@pytest.mark.parametrize("answer", ["n\n", "no\n", "\n", "maybe\n", ""])
@pytest.mark.parametrize("fmt", ["text", "json"])
def test_anything_but_yes_at_the_question_downloads_nothing(
    stub_env, monkeypatch, capsys, at_a_terminal, answer, fmt
):
    """A bare Enter and EOF (``""``) are a no: the default is N."""
    _config(monkeypatch)
    _Catalog.downloaded = False
    at_a_terminal(answer)
    assert print_mode.run("q", model="m1", output_format=fmt) == 1
    out, err = capsys.readouterr()
    assert "Download it now? [y/N] " in err
    assert "❌ gaia -p: Nothing was downloaded." in err
    assert "--yes" in err
    assert _downloads() == []
    assert _StubAgent.instances == []
    if fmt == "json":
        assert out.count("\n") == 1
        record = json.loads(out)
        assert record["is_error"] is True
        assert record["error"].startswith("Nothing was downloaded.")
    else:
        assert out == ""


@pytest.mark.parametrize("fmt", ["text", "json"])
def test_ctrl_c_at_the_question_downloads_nothing_and_exits_130(
    stub_env, monkeypatch, capsys, at_a_terminal, fmt
):
    _config(monkeypatch)
    _Catalog.downloaded = False
    at_a_terminal(KeyboardInterrupt())
    assert print_mode.run("q", model="m1", output_format=fmt) == 130
    out, err = capsys.readouterr()
    assert "❌ gaia -p: Nothing was downloaded." in err
    assert "Traceback" not in err
    assert _downloads() == []
    if fmt == "json":
        assert json.loads(out)["is_error"] is True
    else:
        assert out == ""


@pytest.mark.parametrize("fmt", ["text", "json"])
def test_with_nobody_to_ask_nothing_is_downloaded_and_yes_is_named(
    stub_env, monkeypatch, capsys, fmt
):
    """A script or a piped prompt: stdin can't answer, so the run must not hang."""
    _config(monkeypatch)
    _Catalog.downloaded = False
    stdin = MagicMock()
    stdin.isatty.return_value = False
    monkeypatch.setattr(sys, "stdin", stdin)
    assert print_mode.run("q", model="m1", output_format=fmt) == 1
    out, err = capsys.readouterr()
    assert err == (
        "❌ gaia -p: m1 is not downloaded (5.6 GB), and there is no terminal to "
        "ask whether to download it, so nothing was downloaded. Pass --yes to "
        "download it, or --model with a model you have.\n"
    )
    stdin.readline.assert_not_called()
    stdin.read.assert_not_called()
    assert _downloads() == []
    assert _StubAgent.instances == []
    if fmt == "json":
        assert out.count("\n") == 1
        assert json.loads(out)["is_error"] is True
    else:
        assert out == ""


def test_a_question_nobody_would_see_is_not_asked(stub_env, monkeypatch, capsys):
    """stdin is a terminal but stderr goes to a file: the question would be lost."""
    _config(monkeypatch)
    _Catalog.downloaded = False
    keyboard = _Keyboard("y\n")
    monkeypatch.setattr(sys, "stdin", keyboard)
    assert print_mode.run("q", model="m1") == 1
    assert keyboard.asked == 0
    assert "Pass --yes" in capsys.readouterr().err
    assert _downloads() == []


def test_yes_downloads_without_asking(stub_env, monkeypatch, capsys):
    _config(monkeypatch)
    _Catalog.downloaded = False
    stdin = MagicMock()
    monkeypatch.setattr(sys, "stdin", stdin)
    assert _run_yes("q", model="m1", output_format="json") == 0
    out, err = capsys.readouterr()
    stdin.readline.assert_not_called()
    assert "Download it now?" not in err
    assert _downloads() == ["m1"]
    assert json.loads(out)["result"] == "pong"


def test_a_downloaded_model_is_never_asked_about(stub_env, monkeypatch, capsys):
    _config(monkeypatch)
    stdin = MagicMock()
    monkeypatch.setattr(sys, "stdin", stdin)
    assert print_mode.run("q", model="m1") == 0
    stdin.readline.assert_not_called()
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("source", ["last_model", "hybrid_classifier", "hybrid_routed"])
def test_a_model_the_user_did_not_name_is_asked_about_too(
    stub_env, monkeypatch, capsys, source
):
    """With nobody to ask, no implicit pick is downloaded either."""
    from gaia.llm import hybrid

    class _Router:
        def __init__(self):
            self.settings = hybrid.HybridSettings(
                cheap_model="Local-Cheap-GGUF",
                strong_model="amd.Strong",
                classifier_model=(
                    "Local-Classifier-GGUF"
                    if source == "hybrid_classifier"
                    else "amd.Classifier"
                ),
            )

        def decide(self, text, base_url=None):
            return hybrid.RouteDecision(
                model="Local-Cheap-GGUF",
                kind="chat",
                difficulty=1,
                stakes="low",
                reason="test",
                classified=True,
                seconds=0.1,
                policy=self.settings.policy,
            )

    monkeypatch.setattr(hybrid, "HybridRouter", _Router)
    monkeypatch.setattr(
        "gaia.llm.lemonade_client.cloud_model_provider",
        lambda model: "amd" if model.startswith("amd.") else None,
    )
    _config(monkeypatch, last_model="Local-Last-GGUF")
    _Catalog.downloaded = False
    model = None if source == "last_model" else "hybrid"
    assert print_mode.run("q", model=model) == 1
    err = capsys.readouterr().err
    missing = {
        "last_model": "Local-Last-GGUF",
        "hybrid_classifier": "Local-Classifier-GGUF",
        "hybrid_routed": "Local-Cheap-GGUF",
    }[source]
    assert f"{missing} is not downloaded (5.6 GB)" in err
    assert "Pass --yes" in err
    assert _downloads() == []
    assert _StubAgent.instances == []


def test_main_forwards_yes(monkeypatch):
    run = MagicMock(return_value=0)
    monkeypatch.setattr(print_mode, "run", run)
    from gaia import cli

    for flag in ("--yes", "-y"):
        monkeypatch.setattr(sys, "argv", ["gaia", "-p", "hi", flag])
        with pytest.raises(SystemExit) as exit_info:
            cli.main()
        assert exit_info.value.code == 0
        assert run.call_args.kwargs["yes"] is True


def test_yes_without_p_is_a_usage_error(parser, capsys):
    from gaia import cli

    with patch.object(sys, "argv", ["gaia", "--yes", "chat"]):
        with pytest.raises(SystemExit) as exit_info:
            cli.main()
    assert exit_info.value.code == 2
    assert "--yes before a subcommand only applies to -p" in capsys.readouterr().err


class _Terminal(io.StringIO):
    def isatty(self):
        return True


def test_on_a_terminal_the_bar_redraws_in_place():
    stream = _Terminal()
    ticks = iter(range(100))
    bar = print_mode._DownloadProgress(
        "m1", planned_bytes=4_000_000_000, stream=stream, clock=lambda: next(ticks)
    )
    bar(_progress(1_000_000_000, 4_000_000_000))
    bar(_progress(2_000_000_000, 4_000_000_000))
    bar({"event": "complete"})
    frames = stream.getvalue().split("\r")[1:]
    assert [frame.strip() for frame in frames] == [
        "[######------------------]  25%  1.0 / 4.0 GB",
        "[############------------]  50%  2.0 / 4.0 GB",
        "[########################] 100%  4.0 / 4.0 GB",
    ]
    assert stream.getvalue().endswith("\n")
    assert stream.getvalue().count("\n") == 1


def test_the_bar_is_not_redrawn_for_every_event():
    stream = _Terminal()
    now = [0.0]
    bar = print_mode._DownloadProgress(
        "m1", planned_bytes=1000, stream=stream, clock=lambda: now[0]
    )
    for done in range(0, 500):
        bar(_progress(done, 1000))
    assert stream.getvalue().count("\r") == 1
    now[0] = 1.0
    bar(_progress(500, 1000))
    assert stream.getvalue().count("\r") == 2


def test_progress_adds_up_the_files_of_one_model():
    """Lemonade reports bytes per file: the weights, then the vision projector."""
    stream = io.StringIO()
    bar = print_mode._DownloadProgress("m1", planned_bytes=10**9, stream=stream)
    bar(_progress(800_000_000, 800_000_000, file="weights.gguf"))
    bar(_progress(100_000_000, 200_000_000, file="mmproj.gguf"))
    assert bar.bytes_done == 900_000_000
    assert bar.bytes_total == 1_000_000_000
    assert stream.getvalue().splitlines()[-1] == (
        "gaia -p: downloading m1: 90% (0.9 / 1.0 GB)"
    )


def test_progress_with_no_size_yet_draws_nothing():
    stream = _Terminal()
    bar = print_mode._DownloadProgress("m1", stream=stream)
    bar(_progress(0, 0, file=""))
    bar({"event": "complete"})
    assert stream.getvalue() == ""


@pytest.mark.parametrize("fmt", ["text", "json"])
def test_a_failed_download_exits_1_with_the_reason(stub_env, monkeypatch, capsys, fmt):
    from gaia.llm.lemonade_client import LemonadeClientError

    _config(monkeypatch)
    _Catalog.downloaded = False
    _Catalog.download_error = LemonadeClientError(
        "Failed to download model 'm1': connection lost"
    )
    assert _run_yes("q", model="m1", output_format=fmt) == 1
    out, err = capsys.readouterr()
    assert "❌ gaia -p: Failed to download model 'm1': connection lost" in err
    assert "LemonadeClientError" not in err
    if fmt == "json":
        assert json.loads(out)["error"].startswith("Failed to download model 'm1'")
    else:
        assert out == ""
    assert _StubAgent.instances == []


@pytest.mark.parametrize("stop", ["ctrl_c", "cancelled"])
def test_stopping_a_download_exits_130_with_a_clear_message(
    stub_env, monkeypatch, capsys, stop
):
    from gaia.llm.lemonade_client import ModelDownloadCancelledError

    _config(monkeypatch)
    _Catalog.downloaded = False
    _Catalog.events = [_progress(1, 10)]
    _Catalog.download_error = (
        KeyboardInterrupt()
        if stop == "ctrl_c"
        else ModelDownloadCancelledError("Download of m1 stopped at your request.")
    )
    assert _run_yes("q", model="m1") == 130
    out, err = capsys.readouterr()
    assert out == ""
    assert "❌ gaia -p: Download of m1 stopped. Run the same command again" in err
    assert "Traceback" not in err
    assert _StubAgent.instances == []


def test_ctrl_c_ends_the_bar_line_before_the_message(monkeypatch, capsys):
    """The stop message starts on its own line, not after a half-drawn bar."""
    stream = _Terminal()
    monkeypatch.setattr(sys, "stderr", stream)

    class _Client:
        def ensure_model_downloaded(self, model, **kwargs):
            kwargs["on_progress"](_progress(1_000_000, 10_000_000))
            raise KeyboardInterrupt

    with pytest.raises(print_mode.DownloadStopped) as stopped:
        print_mode._download_model(_Client(), "m1", 0.01)
    assert stopped.value.code == 130
    assert "\r" in stream.getvalue()
    assert stream.getvalue().endswith("\n")


def test_a_missing_last_model_is_downloaded_too(stub_env, monkeypatch, capsys):
    _config(monkeypatch, last_model="Gemma-4-E4B-it-GGUF", default_model="other")
    _Catalog.downloaded = False
    assert _run_yes("q") == 0
    assert _downloads() == ["Gemma-4-E4B-it-GGUF"]
    assert capsys.readouterr().out == "pong\n"


def test_an_unknown_last_model_names_last_model_not_default_model(
    stub_env, monkeypatch, capsys
):
    """``last_model`` wins over ``default_model``, so setting the default can't help."""
    _config(monkeypatch, last_model="Nope-GGUF", default_model="other")
    _Catalog.downloaded = False
    _Catalog.listed = False
    assert print_mode.run("q") == 1
    err = capsys.readouterr().err
    assert err.startswith("❌ gaia -p: Nope-GGUF isn't a model the model server")
    assert "RuntimeError" not in err
    assert "`gaia config set last_model <id>`" in err
    assert '`gaia config set last_model ""`' in err
    assert "default_model <id>" not in err
    assert "--model" in err
    assert _downloads() == []


def test_an_unknown_model_passed_with_model_names_only_what_helps(
    stub_env, monkeypatch, capsys
):
    _config(monkeypatch, last_model="other")
    _Catalog.downloaded = False
    _Catalog.listed = False
    assert print_mode.run("q", model="cfg-model", output_format="json") == 1
    out, err = capsys.readouterr()
    error = json.loads(out)["error"]
    assert error.startswith("cfg-model isn't a model the model server knows")
    assert "gaia config set" not in error
    assert "RuntimeError" not in err


def test_an_unknown_default_model_names_default_model(stub_env, monkeypatch, capsys):
    _config(monkeypatch, default_model="cfg-model")
    _Catalog.downloaded = False
    _Catalog.listed = False
    assert print_mode.run("q") == 1
    err = capsys.readouterr().err
    assert "`gaia config set default_model <id>`" in err
    assert "last_model" not in err


def test_a_local_hybrid_classifier_that_is_not_downloaded_is_downloaded_first(
    stub_env, monkeypatch, capsys
):
    from gaia.llm import hybrid

    class _Router:
        def __init__(self):
            self.settings = hybrid.HybridSettings(
                cheap_model="amd.Cheap",
                strong_model="amd.Strong",
                classifier_model="Local-Classifier-GGUF",
            )

        def decide(self, text, base_url=None):
            assert _downloads() == ["Local-Classifier-GGUF"]
            return hybrid.RouteDecision(
                model="amd.Cheap",
                kind="chat",
                difficulty=1,
                stakes="low",
                reason="test",
                classified=True,
                seconds=0.1,
                policy=self.settings.policy,
            )

    monkeypatch.setattr(hybrid, "HybridRouter", _Router)
    monkeypatch.setattr(
        "gaia.llm.lemonade_client.cloud_model_provider",
        lambda model: "amd" if model.startswith("amd.") else None,
    )
    _config(monkeypatch)
    _Catalog.downloaded = False
    assert _run_yes("q", model="hybrid") == 0
    assert _downloads() == ["Local-Classifier-GGUF"]
    assert _agent().config.model_id == "amd.Cheap"


def test_an_unknown_model_is_called_unknown_not_undownloaded(
    stub_env, monkeypatch, capsys
):
    _config(monkeypatch)
    _Catalog.downloaded = False
    _Catalog.listed = False
    assert print_mode.run("q", model="Gemma-4-E4B-typo") == 1
    err = capsys.readouterr().err
    assert "Gemma-4-E4B-typo isn't a model the model server knows" in err
    assert "not downloaded" not in err
    assert "Settings > Model" not in err
    assert _StubAgent.instances == []


def test_a_gaia_model_the_server_has_not_listed_yet_is_downloaded(
    stub_env, monkeypatch, capsys
):
    from gaia.llm.lemonade_client import FLASH_OPTION_MODEL_NAME

    _config(monkeypatch, default_model=FLASH_OPTION_MODEL_NAME)
    _Catalog.downloaded = False
    _Catalog.listed = False
    assert _run_yes("q") == 0
    assert _downloads() == [FLASH_OPTION_MODEL_NAME]
    assert "isn't a model the model server knows" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "routed, setting",
    [
        ("Local-Strong-GGUF", "hybrid_strong_model"),
        ("Local-Cheap-GGUF", "hybrid_cheap_model"),
    ],
)
def test_a_missing_routed_hybrid_model_is_downloaded(
    stub_env, monkeypatch, capsys, routed, setting
):
    from gaia.llm import hybrid

    class _Router:
        def __init__(self):
            self.settings = hybrid.HybridSettings(
                cheap_model="Local-Cheap-GGUF",
                strong_model="Local-Strong-GGUF",
                classifier_model="amd.Classifier",
            )

        def decide(self, text, base_url=None):
            return hybrid.RouteDecision(
                model=routed,
                kind="chat",
                difficulty=1,
                stakes="low",
                reason="test",
                classified=True,
                seconds=0.1,
                policy=self.settings.policy,
            )

    monkeypatch.setattr(hybrid, "HybridRouter", _Router)
    _config(monkeypatch)
    _Catalog.downloaded = False
    assert _run_yes("q", model="hybrid") == 0
    assert routed in _downloads()
    assert _agent().config.model_id == routed


def test_a_gateway_model_the_catalog_leaves_out_is_not_called_undownloaded(
    stub_env, monkeypatch
):
    """Another provider's model, missing while its token is expired, has no download."""
    _config(monkeypatch, last_model="openrouter.some-model")
    _Catalog.downloaded = False
    _Catalog.cloud = True
    assert print_mode.run("q") == 0
    assert _agent().config.model_id == "openrouter.some-model"
    assert "get_model_info" not in [c[0] for c in _Catalog.calls]


def test_a_downloaded_local_model_is_checked_then_loaded(stub_env, monkeypatch):
    _config(monkeypatch, default_model="cfg-model")
    assert print_mode.run("q") == 0
    assert [c.kwargs["preload"] for c in stub_env.call_args_list] == [False, True]
    assert ("get_model_info", "cfg-model") in _Catalog.calls
    assert _Catalog.calls[0][1]["base_url"] == "http://detected:13305/api/v1"


def test_the_relayed_turn_downloads_a_missing_model_first(relay, monkeypatch, capsys):
    _config(monkeypatch, default_model="cfg-model")
    _Catalog.downloaded = False
    assert _run_yes("q") == 0
    assert _downloads() == ["cfg-model"]
    assert len(relay) == 1


def test_the_relayed_turn_does_not_run_after_a_failed_download(
    relay, monkeypatch, capsys
):
    from gaia.llm.lemonade_client import LemonadeClientError

    _config(monkeypatch, default_model="cfg-model")
    _Catalog.downloaded = False
    _Catalog.download_error = LemonadeClientError("Failed to download: disk full")
    assert _run_yes("q") == 1
    assert "disk full" in capsys.readouterr().err
    assert list(relay) == []


def test_run_returns_1_with_an_actionable_message_when_the_server_fails(
    stub_env, capsys
):
    stub_env.return_value = (False, None)
    assert print_mode.run("q", model="m") == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "could not be started" in captured.err
    assert "gaia daemon status" in captured.err
    assert "gaia init" in captured.err
    assert _StubAgent.instances == []


def test_run_reads_the_prompt_from_stdin(stub_env, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", _FakeStdin("piped prompt\n"))
    assert print_mode.run(print_mode.READ_STDIN, model="m") == 0
    assert _agent().queries == ["piped prompt"]


def test_run_rejects_an_unknown_output_format_before_starting_anything(stub_env):
    with pytest.raises(ValueError, match="--output-format"):
        print_mode.run("q", model="m", output_format="yaml")
    stub_env.assert_not_called()


def test_run_with_no_prompt_on_a_terminal_starts_nothing(stub_env, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", _FakeStdin("", tty=True))
    assert print_mode.run(print_mode.READ_STDIN, model="m") == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "needs a prompt" in captured.err
    stub_env.assert_not_called()


def test_memory_is_drained_even_when_the_turn_raises(stub_env):
    class _Boom(_StubAgent):
        def process_query(self, text):
            raise RuntimeError("model call failed")

    sys.modules["gaia_agent.agent"].GaiaAgent = _Boom
    assert print_mode.run("q", model="m") == 1
    assert _agent().drained


class _Outcome:
    def __init__(
        self,
        terminal_type,
        final_answer=None,
        error_detail=None,
        failed=False,
        usage=None,
        model_messages=None,
    ):
        self.terminal_type = terminal_type
        self.final_answer = final_answer
        self.error_detail = error_detail
        self.failed = failed
        self.usage = usage
        self.model_messages = model_messages


@pytest.fixture
def relay(stub_env, monkeypatch):
    """No importable agent, as in the frozen app: the turn goes to the sidecar."""
    monkeypatch.setitem(sys.modules, "gaia_agent.agent", None)

    class Calls(list):
        outcome = _Outcome("final", final_answer="pong from the binary")

    calls = Calls()

    def run_query(agent_id, query, **kwargs):
        calls.append((agent_id, query, kwargs))
        return calls.outcome

    monkeypatch.setattr("gaia.daemon.agent_query.run_query", run_query)
    return calls


def test_without_the_agent_package_the_turn_runs_on_the_agent_binary(
    relay, capsys, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    assert print_mode.run("q", model="m", output_format="json") == 0
    record = json.loads(capsys.readouterr().out)
    assert record["result"] == "pong from the binary" and not record["is_error"]
    [(agent_id, query, kwargs)] = relay
    assert (agent_id, query, kwargs["model"]) == ("gaia", "q", "m")
    # The agent works in the caller's folder, as an in-process -p would.
    assert kwargs["working_directory"] == str(tmp_path)
    assert kwargs["allowed_paths"] == [str(tmp_path)]


_RELAY_USAGE = {
    "steps": 2,
    "tools_used": 1,
    "tokens": 12,
    "input_tokens": 4000,
    "cached_tokens": 3500,
}
_RELAY_MESSAGES = [
    {"role": "assistant", "tool_calls": [{"function": {"name": "read_file"}}]},
    {"role": "tool", "name": "read_file", "content": "amber-lynx-47"},
    {"role": "assistant", "content": "The second code is amber-lynx-47."},
]


def test_a_relayed_json_record_counts_steps_tools_and_tokens(relay, capsys):
    relay.outcome = _Outcome("final", final_answer="amber", usage=_RELAY_USAGE)

    assert print_mode.run("q", model="m", output_format="json") == 0

    record = json.loads(capsys.readouterr().out)
    assert record["num_steps"] == 2
    assert record["num_tool_calls"] == 1
    assert record["input_tokens"] == 4000
    assert record["cached_tokens"] == 3500
    assert record["output_tokens"] == 12
    assert "conversation" not in record
    [(_, _, kwargs)] = relay
    assert "turns" not in kwargs


def test_a_relayed_verbose_record_carries_the_conversation(relay, capsys):
    relay.outcome = _Outcome(
        "final",
        final_answer="amber",
        usage=_RELAY_USAGE,
        model_messages=_RELAY_MESSAGES,
    )

    assert print_mode.run("q", model="m", output_format="json", verbose=True) == 0

    record = json.loads(capsys.readouterr().out)
    assert record["conversation"] == _RELAY_MESSAGES
    assert record["num_tool_calls"] == 1
    # Sending turns, even empty, is how the agent is asked for model_messages.
    [(_, _, kwargs)] = relay
    assert kwargs["turns"] == []


def test_a_relayed_final_without_a_tool_count_keeps_the_conversations(relay, capsys):
    """An error-exit final carries steps and tokens but no ``tools_used``."""
    usage = {"steps": 2, "tokens": 12, "input_tokens": 4000}
    relay.outcome = _Outcome(
        "final", final_answer="amber", usage=usage, model_messages=_RELAY_MESSAGES
    )

    assert print_mode.run("q", model="m", output_format="json", verbose=True) == 0

    record = json.loads(capsys.readouterr().out)
    assert record["num_tool_calls"] == 1
    assert record["num_steps"] == 2


def test_a_relayed_final_with_no_usage_reports_zeros_not_a_crash(relay, capsys):
    relay.outcome = _Outcome("final", final_answer="amber")

    assert print_mode.run("q", model="m", output_format="json") == 0

    record = json.loads(capsys.readouterr().out)
    assert record["num_tool_calls"] == 0
    assert record["cached_tokens"] == 0


def test_a_relayed_turn_leaves_the_model_load_to_the_daemon(relay, stub_env):
    """The daemon loads the model for its agent, as it does for the Agent UI;
    loading one here would race it. Reaching the server to read its catalog
    loads nothing."""
    assert print_mode.run("q", model="m") == 0
    assert [c.kwargs["preload"] for c in stub_env.call_args_list] == [False]


def test_a_relayed_answer_reporting_a_failed_model_call_is_an_error(relay, capsys):
    relay.outcome = _Outcome(
        "final", final_answer="Couldn't reach the local LLM.", failed=True
    )
    assert print_mode.run("q", model="m") == 1
    assert "Couldn't reach the local LLM" in capsys.readouterr().err


def test_a_relayed_turn_that_errors_exits_1_with_the_reason(relay, capsys):
    relay.outcome = _Outcome("error", error_detail="model failed to load")
    assert print_mode.run("q", model="m") == 1
    assert "model failed to load" in capsys.readouterr().err


@pytest.mark.parametrize(
    "flags, named",
    [
        ({"skip_permissions": True}, "--dangerously-skip-permissions"),
        ({"full_access": True}, "--full-access"),
        ({"base_url": "http://h:1/api/v1"}, "--base-url"),
        # The relay has no way to stop the binary's turn at a deadline.
        ({"time_limit": 60}, "--time-limit"),
    ],
)
def test_flags_the_agent_binary_cannot_honour_are_refused(relay, capsys, flags, named):
    assert print_mode.run("q", model="m", **flags) == 1
    assert named in capsys.readouterr().err
    assert relay == []


# ---------------------------------------------------------------------------
# Failure reporting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["text", "json"])
def test_an_exception_mid_turn_is_reported_not_a_traceback(stub_env, capsys, fmt):
    class _Boom(_StubAgent):
        def process_query(self, text):
            raise ConnectionError("gateway reset the connection")

    sys.modules["gaia_agent.agent"].GaiaAgent = _Boom
    assert print_mode.run("q", model="m", output_format=fmt) == 1
    captured = capsys.readouterr()
    assert "ConnectionError: gateway reset the connection" in captured.err
    assert "Traceback" not in captured.err
    if fmt == "json":
        record = json.loads(captured.out)
        assert record["is_error"] is True
        assert "gateway reset" in record["error"]
    else:
        assert captured.out == ""


def test_a_failed_turn_says_why_on_stderr(stub_env, capsys):
    _StubAgent.result = {
        "status": "error",
        "result": "",
        "error_history": [{"type": "llm", "error": "HTTP 404 from the gateway"}],
    }
    assert print_mode.run("q", model="m") == 1
    captured = capsys.readouterr()
    assert "status 'error'" in captured.err
    assert "HTTP 404 from the gateway" in captured.err


@pytest.mark.parametrize(
    "result",
    [
        {"status": "error", "result": ""},
        {"status": "error"},
        {"status": "error", "result": None},
        {"status": "failed", "result": "", "error": "boom"},
    ],
)
def test_a_failed_text_turn_with_no_answer_leaves_stdout_empty(
    stub_env, capsys, result
):
    _StubAgent.result = result
    assert print_mode.run("q", model="m") == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "gaia -p" in captured.err


def test_a_failed_text_turn_still_prints_its_partial_answer(stub_env, capsys):
    _StubAgent.result = {"status": "failed", "result": "partial"}
    assert print_mode.run("q", model="m") == 1
    assert capsys.readouterr().out == "partial\n"


def test_a_successful_empty_answer_still_prints_a_line(stub_env, capsys):
    _StubAgent.result = {"status": "success", "result": ""}
    assert print_mode.run("q", model="m") == 0
    assert capsys.readouterr().out == "\n"


def test_json_error_record_carries_the_reason(stub_env, capsys):
    _StubAgent.result = {"status": "error", "error": "context overflow"}
    assert print_mode.run("q", model="m", output_format="json") == 1
    record = json.loads(capsys.readouterr().out)
    assert record["error"] == "context overflow"


def test_verbose_json_includes_the_conversation(stub_env, capsys):
    convo = [
        {"role": "user", "content": "q"},
        {"role": "tool", "name": "t", "content": "x"},
    ]
    _StubAgent.result = {"status": "success", "result": "ok", "conversation": convo}
    assert print_mode.run("q", model="m", output_format="json", verbose=True) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["conversation"] == convo


def test_cached_tokens_are_summed_from_the_conversation():
    convo = [
        {"role": "assistant", "content": "a", "stats": {"cached_tokens": 40}},
        {"role": "assistant", "content": "b", "stats": {"cached_tokens": 2}},
    ]
    record = print_mode.summarize({"status": "success", "conversation": convo}, "m", 0)
    from gaia.agents.base.agent import _sum_cached_tokens

    assert record["cached_tokens"] == _sum_cached_tokens(convo)


@pytest.mark.parametrize(
    "skip,full,approve,unguarded",
    [
        (False, False, False, False),
        (True, False, True, False),
        # Pre-approving prompts must never also unguard the shell.
        (False, True, True, True),
        (True, True, True, True),
    ],
)
def test_full_access_is_its_own_opt_in(stub_env, skip, full, approve, unguarded):
    print_mode.run("q", model="m", skip_permissions=skip, full_access=full)
    console = _agent().console
    assert console.auto_approve_gated_tools is approve
    assert console.full_access is unguarded


def _grants_outside_path(skip, full, tmp_path):
    """Whether GAIA's own path gate grants an out-of-scope path to a -p console."""
    from gaia.agents.base.agent import Agent
    from gaia.agents.base.console import SilentConsole

    console = SilentConsole()
    print_mode.configure_console(console, skip_permissions=skip, full_access=full)

    # A real Agent (not a stand-in) so the gate's own helper methods are used.
    class _Bare(Agent):
        def _register_tools(self):
            pass

    agent = object.__new__(_Bare)
    agent.console = console
    agent._turn_seq = 0
    agent._refused_paths = []
    agent._confirmation_wait_s = 0.0
    return agent._confirm_path_access(tmp_path / "outside" / "x.txt")


def test_full_access_grants_paths_outside_the_cwd(tmp_path):
    # As the TUI's /full-access does; the docs say so.
    assert _grants_outside_path(False, True, tmp_path) is True


def test_skip_permissions_alone_never_widens_the_file_scope(tmp_path):
    assert _grants_outside_path(True, False, tmp_path) is False


def test_no_permission_flag_denies_an_outside_path_with_nobody_to_ask(tmp_path):
    # pytest's captured stdio is not a terminal, so this is the headless case.
    assert _grants_outside_path(False, False, tmp_path) is False


def test_file_tools_start_in_the_cwd(stub_env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    print_mode.run("q", model="m", full_access=True)
    assert _agent().config.allowed_paths == [str(tmp_path)]


def test_full_access_is_announced_on_stderr(stub_env, capsys):
    print_mode.run("q", model="m", full_access=True)
    assert "full access is on" in capsys.readouterr().err


def test_ctrl_c_exits_130_without_a_traceback(stub_env, capsys):
    class _Interrupted(_StubAgent):
        def process_query(self, text):
            raise KeyboardInterrupt

    sys.modules["gaia_agent.agent"].GaiaAgent = _Interrupted
    assert print_mode.run("q", model="m", output_format="json") == 130
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"] == "interrupted"
    assert "Traceback" not in captured.err


def test_a_failed_turn_with_no_recorded_error_still_says_why(stub_env, capsys):
    _StubAgent.result = {"status": "incomplete", "result": "partial"}
    assert print_mode.run("q", model="m", output_format="json") == 1
    assert (
        json.loads(capsys.readouterr().out)["error"]
        == "the turn ended with status 'incomplete'"
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["-p", "x", "--ui"],
        ["-p", "x", "--cli"],
        ["-p", "x", "--ui-port", "9000"],
        ["-p", "x", "--ui-dist", "d"],
        ["-p", "x", "--verbose"],
        ["-p", "x", "--verbose", "--output-format", "text"],
    ],
)
def test_flags_p_would_ignore_are_usage_errors(monkeypatch, capsys, argv):
    run = MagicMock()
    monkeypatch.setattr(print_mode, "run", run)
    assert _run_main(monkeypatch, argv) == 2
    run.assert_not_called()


def test_main_forwards_full_access(monkeypatch):
    run = MagicMock(return_value=0)
    monkeypatch.setattr(print_mode, "run", run)
    _run_main(monkeypatch, ["-p", "hi", "--full-access"])
    assert run.call_args.kwargs["full_access"] is True
    assert run.call_args.kwargs["skip_permissions"] is False


def test_a_failure_with_no_recorded_error_says_its_status_once(stub_env, capsys):
    class _Incomplete(_StubAgent):
        def process_query(self, text):
            return {"status": "incomplete", "result": "partial", "conversation": []}

    sys.modules["gaia_agent.agent"].GaiaAgent = _Incomplete
    assert print_mode.run("q", model="m") == 1
    assert capsys.readouterr().err.count("ended with status") == 1


def test_an_unexpected_failure_does_not_log_its_traceback_at_info(
    stub_env, capsys, caplog
):
    class _Boom(_StubAgent):
        def process_query(self, text):
            raise ConnectionError("reset")

    sys.modules["gaia_agent.agent"].GaiaAgent = _Boom
    with caplog.at_level(logging.INFO):
        assert print_mode.run("q", model="m") == 1
    assert not [r for r in caplog.records if r.exc_info]


# ---------------------------------------------------------------------------
# --time-limit
# ---------------------------------------------------------------------------


def test_time_limit_parses_and_defaults_to_none(parser):
    assert parser.parse_args(["-p", "x"]).print_time_limit is None
    assert parser.parse_args(["-p", "x", "--time-limit", "600"]).print_time_limit == 600


@pytest.mark.parametrize("value", ["0", "-1", "ten", "1.5"])
def test_time_limit_must_be_a_positive_integer(parser, capsys, value):
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["-p", "x", "--time-limit", value])
    assert exc.value.code == 2
    assert "--time-limit" in capsys.readouterr().err


def test_main_forwards_time_limit(monkeypatch):
    run = MagicMock(return_value=0)
    monkeypatch.setattr(print_mode, "run", run)
    _run_main(monkeypatch, ["-p", "hi", "--time-limit", "900"])
    assert run.call_args.kwargs["time_limit"] == 900


def test_the_agent_is_told_nobody_can_answer(stub_env):
    print_mode.run("q", model="m1")
    assert _agent().no_one_to_ask is True


def test_no_time_limit_arms_nothing(stub_env):
    print_mode.run("q", model="m1")
    agent = _agent()
    assert not hasattr(agent, "time_limit_s")
    assert not hasattr(agent, "_cancel_event")


def test_a_time_limit_reaches_the_agent_and_is_disarmed_after(stub_env):
    from gaia.agents.base.agent import TurnCancel

    print_mode.run("q", model="m1", time_limit=60)

    agent = _agent()
    assert agent.time_limit_s == 60
    assert isinstance(agent._cancel_event, TurnCancel)
    # The turn finished in time: the stop never fired.
    assert not agent._cancel_event.is_set()


def test_a_turn_still_running_at_the_limit_is_stopped_and_reported(stub_env, capsys):
    """The hard stop: a tool outlived the wrap-up, so the limit ends the turn."""

    def _slow_turn(self, text):
        self.queries.append(text)
        stopped = self._cancel_event.wait(10)
        return {
            "status": "incomplete",
            "result": self._cancel_event.message if stopped else "never stopped",
            "time_limit_reached": stopped,
        }

    with patch.object(_StubAgent, "process_query", _slow_turn):
        code = print_mode.run("q", model="m1", output_format="json", time_limit=1)

    record = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 1
    assert "1-second time limit" in record["result"]
    assert record["time_limit_reached"] is True


def test_a_model_call_outliving_the_limit_is_abandoned_with_a_record(
    stub_env, capsys, monkeypatch
):
    """A call already sent ignores the stop; the run still ends and reports."""
    monkeypatch.setattr(print_mode, "HARD_STOP_GRACE_S", 0.2)
    release = threading.Event()

    def _stuck_in_a_model_call(self, text):
        self.queries.append(text)
        release.wait(30)
        return {"status": "success", "result": "too late"}

    def _abandoned(self, message):
        return {"status": "incomplete", "result": message, "time_limit_reached": True}

    try:
        with (
            patch.object(_StubAgent, "process_query", _stuck_in_a_model_call),
            patch.object(
                _StubAgent, "time_limit_abandoned_result", _abandoned, create=True
            ),
        ):
            started = time.monotonic()
            code = print_mode.run("q", model="m1", output_format="json", time_limit=1)
            elapsed = time.monotonic() - started
    finally:
        release.set()

    record = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 1
    assert elapsed < 5
    assert record["time_limit_reached"] is True
    assert "while a model call was still running" in record["result"]
    # The abandoned turn still holds the model: no memory pass waits on it.
    assert not _agent().drained


def test_summarize_reports_a_time_limit_stop():
    record = print_mode.summarize(
        {"status": "incomplete", "result": "x", "time_limit_reached": True}, "m", 1.0
    )
    assert record["time_limit_reached"] is True
    assert record["is_error"] is True


@pytest.mark.parametrize("value", ["0", "5", "two"])
def test_attempts_out_of_range_is_a_usage_error(monkeypatch, value):
    run = MagicMock(return_value=0)
    monkeypatch.setattr(print_mode, "run", run)
    assert _run_main(monkeypatch, ["-p", "hi", "--attempts", value]) == 2
    run.assert_not_called()


def _attempt_doubles(monkeypatch, tmp_path, final_raises=None):
    """Stub the server, the attempts and the final turn; record what they got."""
    from gaia import attempts

    seen = {}
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(print_mode, "_start_server", lambda m, b, preload=True: None)
    _Catalog.calls = []
    _Catalog.downloaded = True
    _Catalog.listed = True
    _Catalog.cloud = False
    _Catalog.size_gb = 5.6
    _Catalog.events = []
    _Catalog.download_error = None
    monkeypatch.setattr("gaia.llm.lemonade_client.LemonadeClient", _Catalog)

    def fake_attempts(text, count, flags):
        seen.update(text=text, count=count, flags=flags)
        records = [
            {
                "is_error": False,
                "status": "success",
                "num_steps": 2,
                "num_tool_calls": 3,
                "input_tokens": 100,
                "cached_tokens": 10,
                "output_tokens": 5,
                "duration_s": 1.0,
                "result": "draft",
            },
            {"is_error": True, "status": "error", "error": "boom"},
        ]
        return text + " +MERGE", records, scratch

    def fake_turn(text, **kw):
        seen.update(final=text, extra_paths=kw.get("extra_paths"))
        if final_raises:
            raise final_raises
        return {
            "type": "result",
            "is_error": False,
            "status": "success",
            "result": "merged",
            "num_steps": 4,
            "num_tool_calls": 1,
            "input_tokens": 1000,
            "cached_tokens": 0,
            "output_tokens": 50,
        }

    monkeypatch.setattr(attempts, "run_attempts", fake_attempts)
    monkeypatch.setattr(print_mode, "_run_turn", fake_turn)
    return seen, scratch


def test_attempts_pass_flags_merge_in_place_and_sum_usage(
    monkeypatch, tmp_path, capsys
):
    seen, scratch = _attempt_doubles(monkeypatch, tmp_path)
    code = print_mode.run(
        "Review it",
        model="m1",
        output_format="json",
        attempts=2,
        max_steps=7,
        full_access=True,
        base_url="http://h:1/api/v1",
    )
    assert code == 0
    # Full access implies skipping permissions; attempts get the same model and server.
    assert seen["flags"] == [
        "--model",
        "m1",
        "--max-steps",
        "7",
        "--full-access",
        "--base-url",
        "http://h:1/api/v1",
    ]
    assert seen["count"] == 2 and seen["final"] == "Review it +MERGE"
    # The final turn may read the drafts, and they are gone afterwards.
    assert seen["extra_paths"] == [str(scratch)] and not scratch.exists()
    out = capsys.readouterr().out.strip().splitlines()[-1]
    record = json.loads(out)
    assert record["result"] == "merged"
    assert record["input_tokens"] == 1100 and record["num_steps"] == 6
    assert record["cached_tokens"] == 10 and record["output_tokens"] == 55
    assert [a["is_error"] for a in record["attempts"]] == [False, True]
    assert "result" not in record["attempts"][0]  # drafts' text isn't repeated
    # A partly failed run still says why each failed attempt failed.
    assert record["attempts"][1]["error"] == "boom"
    assert record["attempts"][0]["error"] is None


def test_attempts_skip_permissions_only_when_asked(monkeypatch, tmp_path):
    seen, _ = _attempt_doubles(monkeypatch, tmp_path)
    _config(monkeypatch, default_model="cfg-model")
    print_mode.run("t", attempts=2)
    assert seen["flags"] == ["--model", "cfg-model"]
    (tmp_path / "scratch").mkdir()
    print_mode.run("t", attempts=2, skip_permissions=True)
    assert seen["flags"] == ["--model", "cfg-model", "--dangerously-skip-permissions"]


def test_drafts_are_removed_even_when_the_final_turn_fails(
    monkeypatch, tmp_path, capsys
):
    _, scratch = _attempt_doubles(
        monkeypatch, tmp_path, final_raises=RuntimeError("down")
    )
    assert print_mode.run("t", output_format="json", attempts=2) == 1
    assert not scratch.exists()
    out = capsys.readouterr().out.strip().splitlines()[-1]
    assert "RuntimeError: down" in json.loads(out)["error"]


def test_one_attempt_never_copies_the_folder(monkeypatch):
    from gaia import attempts

    monkeypatch.setattr(
        attempts, "run_attempts", MagicMock(side_effect=AssertionError("copied"))
    )
    monkeypatch.setattr(
        print_mode,
        "_run_turn",
        lambda text, **kw: {
            "type": "result",
            "is_error": False,
            "status": "success",
            "result": "ok",
        },
    )
    assert print_mode.run("t", attempts=1) == 0


def test_time_limit_with_attempts_is_a_usage_error_before_anything_runs(
    monkeypatch, capsys
):
    started = MagicMock()
    monkeypatch.setattr(print_mode, "_run_turn", started)
    monkeypatch.setattr(print_mode, "_run_with_attempts", started)

    assert print_mode.run("q", attempts=2, time_limit=60) == 2
    started.assert_not_called()
    assert "--time-limit can't be combined with --attempts" in capsys.readouterr().err


class _RestrictableAgent:
    def __init__(self, names):
        self.names = list(names)
        self.denied = []
        self.rebuilt = False
        self._learned_skills_enabled = True

    def get_tools_info(self):
        return {n: {} for n in self.names}

    def deny_tools(self, patterns):
        self.denied = list(patterns)
        return sorted(
            n for n in self.names if n in patterns or n.startswith("browser_")
        )

    def rebuild_system_prompt(self):
        self.rebuilt = True


def test_restrict_tools_without_flags_changes_nothing():
    agent = _RestrictableAgent(["read_file", "search_web"])
    assert print_mode.restrict_tools(agent, []) == []
    assert agent.denied == [] and agent.rebuilt is False
    assert agent._learned_skills_enabled is True


def test_no_web_withholds_only_the_web_tools_this_agent_has():
    agent = _RestrictableAgent(["read_file", "search_web", "browser_open"])
    withheld = print_mode.restrict_tools(agent, [], no_web=True)
    assert agent.denied == ["search_web", "browser_*"]
    assert withheld == ["browser_open", "search_web"]
    assert agent.rebuilt is True


def test_no_skills_rebuilds_the_prompt_even_with_no_skill_tools():
    agent = _RestrictableAgent(["read_file"])
    assert print_mode.restrict_tools(agent, [], no_skills=True) == []
    assert agent._learned_skills_enabled is False
    assert agent.rebuilt is True


def test_no_skills_withholds_skill_tools_and_learned_skills():
    agent = _RestrictableAgent(["read_file", "load_skill", "list_skills"])
    print_mode.restrict_tools(agent, ["read_file"], no_skills=True)
    assert agent.denied == ["read_file", "list_skills", "load_skill"]
    assert agent._learned_skills_enabled is False


def test_run_turn_turns_off_the_skill_catalogue_and_withholds(stub_env, capsys):
    calls = {}

    def fake_restrict(agent, disallowed, *, no_web, no_skills):
        calls.update(disallowed=disallowed, no_web=no_web, no_skills=no_skills)
        return ["search_web"]

    with patch.object(print_mode, "restrict_tools", fake_restrict):
        code = print_mode.run(
            "q", model="m", output_format="json", no_web=True, no_skills=True
        )
    assert code == 0
    assert calls == {"disallowed": (), "no_web": True, "no_skills": True}
    config = _agent().config
    assert config.skill_discovery is False and config.skill_suggestions is False
    assert json.loads(capsys.readouterr().out)["withheld_tools"] == ["search_web"]


def test_relayed_turn_refuses_tool_restrictions_naming_the_flag():
    with pytest.raises(RuntimeError, match="--no-web needs the GAIA agent"):
        print_mode._relay_turn(
            "q",
            model=None,
            verbose=False,
            max_steps=None,
            skip_permissions=False,
            full_access=False,
            base_url=None,
            restricted="--no-web",
        )


def test_no_web_tells_the_model_the_shell_is_not_a_way_around(stub_env):
    """The withheld tools only imply the rule; a model with a shell used curl."""
    with patch.object(print_mode, "restrict_tools", lambda *a, **k: []):
        assert print_mode.run("find the answer", model="m", no_web=True) == 0
    (query,) = _agent().queries
    assert query.startswith("find the answer")
    assert query.endswith(print_mode.NO_WEB_NOTE)
    assert "through the shell" in query


def test_without_no_web_the_request_is_the_users_own(stub_env):
    assert print_mode.run("find the answer", model="m") == 0
    assert _agent().queries == ["find the answer"]


def test_streamed_text_never_reaches_stdout(capsys):
    """-p streams its model calls; the console it runs with must swallow them."""
    from gaia.agents.base.console import SilentConsole

    console = SilentConsole()
    console.print_streaming_text("partial answer")
    console.print_streaming_text("", end_of_stream=True)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
