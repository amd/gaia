# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Weekly-audit fixes for documented-but-stubbed CLI surfaces.

Covers #2004 (`gaia api status` real /health check), #2009 (dead
``--reset-between-scenarios`` / ``--lemonade-model`` / ``--lemonade-ctx-size``
eval flags removed from the parser), and the #2103 schedule finding
(``gaia schedule add --skill`` rejected loudly at add time instead of
registering a schedule that raises on every fire).
"""

from argparse import Namespace
from types import SimpleNamespace

import pytest
import requests

from gaia.api.app import check_status
from gaia.cli import _handle_schedule, build_parser, handle_api_command

# ---------------------------------------------------------------------------
# #2004 — gaia api status
# ---------------------------------------------------------------------------


def _health_response(status_code=200, payload=None, json_error=False):
    resp = SimpleNamespace(status_code=status_code)
    if json_error:
        resp.json = lambda: (_ for _ in ()).throw(ValueError("not json"))
    else:
        resp.json = lambda: payload
    return resp


class TestApiStatus:
    def test_running_server_reports_success(self, mocker, capsys):
        mocker.patch(
            "requests.get",
            return_value=_health_response(
                payload={"status": "ok", "service": "gaia-api"}
            ),
        )
        check_status("localhost", 8080)
        out = capsys.readouterr().out
        assert "✅ GAIA API server is running at http://localhost:8080" in out
        assert "/v1/chat/completions" in out

    def test_queries_health_endpoint(self, mocker):
        get = mocker.patch(
            "requests.get",
            return_value=_health_response(
                payload={"status": "ok", "service": "gaia-api"}
            ),
        )
        check_status("myhost", 9999)
        get.assert_called_once_with("http://myhost:9999/health", timeout=5)

    def test_unreachable_server_exits_nonzero(self, mocker, capsys):
        mocker.patch(
            "requests.get", side_effect=requests.exceptions.ConnectionError("refused")
        )
        with pytest.raises(SystemExit) as exc:
            check_status("localhost", 8080)
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "not running" in out
        assert "gaia api start" in out

    def test_timeout_exits_nonzero(self, mocker, capsys):
        mocker.patch(
            "requests.get", side_effect=requests.exceptions.Timeout("timed out")
        )
        with pytest.raises(SystemExit) as exc:
            check_status("localhost", 8080)
        assert exc.value.code == 1
        assert "did not respond" in capsys.readouterr().out

    def test_foreign_service_on_port_exits_nonzero(self, mocker, capsys):
        mocker.patch(
            "requests.get",
            return_value=_health_response(payload={"status": "ok", "service": "other"}),
        )
        with pytest.raises(SystemExit) as exc:
            check_status("localhost", 8080)
        assert exc.value.code == 1
        assert "not the GAIA API server" in capsys.readouterr().out

    def test_non_json_responder_exits_nonzero(self, mocker, capsys):
        mocker.patch("requests.get", return_value=_health_response(json_error=True))
        with pytest.raises(SystemExit) as exc:
            check_status("localhost", 8080)
        assert exc.value.code == 1
        assert "not the GAIA API server" in capsys.readouterr().out

    def test_cli_dispatch_passes_host_and_port(self, mocker):
        checker = mocker.patch("gaia.api.app.check_status")
        handle_api_command(Namespace(subcommand="status", host="127.0.0.1", port=8123))
        checker.assert_called_once_with("127.0.0.1", 8123)


# ---------------------------------------------------------------------------
# #2009 — reserved eval flags removed from the parser
# ---------------------------------------------------------------------------


class TestEvalReservedFlagsRemoved:
    @pytest.mark.parametrize(
        "flag_args",
        [
            ["--reset-between-scenarios", "fast"],
            ["--lemonade-model", "some-model"],
            ["--lemonade-ctx-size", "4096"],
        ],
    )
    def test_parser_rejects_removed_flags(self, flag_args, capsys):
        parser = build_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["eval", "agent"] + flag_args)
        assert exc.value.code == 2
        assert "unrecognized arguments" in capsys.readouterr().err

    def test_eval_agent_still_parses(self):
        args = build_parser().parse_args(["eval", "agent"])
        assert not hasattr(args, "reset_between_scenarios")
        assert not hasattr(args, "lemonade_model")
        assert not hasattr(args, "lemonade_ctx_size")


# ---------------------------------------------------------------------------
# #2103 — gaia schedule add --skill rejected at add time
# ---------------------------------------------------------------------------


class TestScheduleAddSkillRejected:
    def _parse_add(self, *extra):
        return build_parser().parse_args(
            ["schedule", "add", "--name", "s", "--cron", "* * * * *", *extra]
        )

    def test_skill_add_exits_nonzero_with_actionable_error(self, mocker, capsys):
        store_add = mocker.patch("gaia.schedule.store.TomlScheduleStore.add")
        args = self._parse_add("--skill", "my-skill")
        with pytest.raises(SystemExit) as exc:
            _handle_schedule(args)
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "--skill is not supported yet" in err
        assert "--prompt" in err
        assert "888" in err
        store_add.assert_not_called()

    def test_prompt_add_still_works(self, mocker, capsys):
        store_add = mocker.patch("gaia.schedule.store.TomlScheduleStore.add")
        args = self._parse_add("--prompt", "hello")
        _handle_schedule(args)
        store_add.assert_called_once()
        assert "✅ Added schedule 's'" in capsys.readouterr().out
