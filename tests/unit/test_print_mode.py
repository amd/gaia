# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""`gaia -p`: one headless turn, the answer (or one JSON object) on stdout."""

import io
import json
import os
import sys
import types
from unittest.mock import MagicMock

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
    # const="" is what tells run() to read the prompt from stdin.
    assert parser.parse_args(["-p"]).print_prompt == ""


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
        skip_permissions=True,
        full_access=False,
        base_url="http://h:1/api/v1",
    )


def test_main_defaults_output_format_to_text(monkeypatch):
    run = MagicMock(return_value=1)
    monkeypatch.setattr(print_mode, "run", run)
    assert _run_main(monkeypatch, ["-p", "hi"]) == 1
    assert run.call_args.kwargs["output_format"] == "text"
    assert run.call_args.kwargs["skip_permissions"] is False


@pytest.mark.parametrize(
    "argv",
    [
        ["--model", "m"],
        ["--output-format", "json"],
        ["--verbose"],
        ["--max-steps", "3"],
        ["--dangerously-skip-permissions"],
        ["--full-access"],
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
    assert print_mode.read_prompt("") == "line one\nline two"


def test_read_prompt_rejects_a_terminal(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _FakeStdin("", tty=True))
    with pytest.raises(ValueError, match="needs a prompt"):
        print_mode.read_prompt("")


def test_read_prompt_rejects_missing_stdin(monkeypatch):
    monkeypatch.setattr(sys, "stdin", None)
    with pytest.raises(ValueError, match="needs a prompt"):
        print_mode.read_prompt(None)


@pytest.mark.parametrize("text", ["", "   \n\t\n"])
def test_read_prompt_rejects_empty_stdin(monkeypatch, text):
    monkeypatch.setattr(sys, "stdin", _FakeStdin(text))
    with pytest.raises(ValueError, match="empty prompt"):
        print_mode.read_prompt("")


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

    from gaia.logger import log_manager

    monkeypatch.setattr(log_manager, "configure_agent_console", MagicMock())
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
    assert cfg.streaming is False
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


def test_no_model_uses_config_default_model(stub_env, monkeypatch):
    cfg = MagicMock(default_model="cfg-model")
    monkeypatch.setattr("gaia.config.GaiaConfig.load", MagicMock(return_value=cfg))
    print_mode.run("q")
    assert stub_env.call_args.kwargs["model"] == "cfg-model"
    assert _agent().config.model_id == "cfg-model"


def test_no_model_and_no_config_default_leaves_it_to_the_agent(stub_env, monkeypatch):
    cfg = MagicMock(default_model="")
    monkeypatch.setattr("gaia.config.GaiaConfig.load", MagicMock(return_value=cfg))
    print_mode.run("q")
    assert _agent().config.model_id is None


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
    assert print_mode.run("", model="m") == 0
    assert _agent().queries == ["piped prompt"]


def test_run_rejects_an_unknown_output_format_before_starting_anything(stub_env):
    with pytest.raises(ValueError, match="--output-format"):
        print_mode.run("q", model="m", output_format="yaml")
    stub_env.assert_not_called()


def test_run_with_no_prompt_on_a_terminal_starts_nothing(stub_env, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", _FakeStdin("", tty=True))
    assert print_mode.run("", model="m") == 1
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


def test_missing_flagship_wheel_is_an_actionable_runtime_error(
    stub_env, monkeypatch, capsys
):
    monkeypatch.setitem(sys.modules, "gaia_agent.agent", None)
    assert print_mode.run("q", model="m") == 1
    assert "re-run `gaia -p`" in capsys.readouterr().err


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
    agent = types.SimpleNamespace(console=console, _turn_seq=0, _refused_paths={})
    return Agent._confirm_path_access(agent, tmp_path / "outside" / "x.txt")


def test_full_access_grants_paths_outside_the_cwd(tmp_path):
    # As the TUI's /full-access does; the docs say so.
    assert _grants_outside_path(False, True, tmp_path) is True


def test_skip_permissions_alone_never_widens_the_file_scope(tmp_path):
    assert _grants_outside_path(True, False, tmp_path) is False


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
