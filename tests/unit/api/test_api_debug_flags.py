# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""``gaia api start`` debug flags must each change server behaviour (#3254).

``--step-through`` only printed "Step-through mode" and set an env var nothing
read, so it is gone from both entry points. The flags that remain still reach
``agent_registry`` through their ``GAIA_API_*`` env vars.
"""

from __future__ import annotations

import inspect
import sys

import pytest

from gaia.api import app as api_app

_DEBUG_ENV = (
    "GAIA_API_DEBUG",
    "GAIA_API_SHOW_PROMPTS",
    "GAIA_API_STREAMING",
    "GAIA_API_STEP_THROUGH",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _DEBUG_ENV:
        monkeypatch.delenv(name, raising=False)


def _parse_gaia(argv):
    from gaia.cli import build_parser

    return build_parser().parse_args(argv)


class TestStepThroughRemoved:
    @pytest.mark.parametrize(
        "argv",
        [
            ["api", "start", "--step-through"],
            ["api", "--step-through", "start"],
            ["api", "start", "--debug", "--step-through"],
            ["api", "start", "--step-through=1"],
        ],
    )
    def test_gaia_api_rejects_step_through(self, capsys, argv):
        with pytest.raises(SystemExit) as exc:
            _parse_gaia(argv)
        assert exc.value.code == 2
        assert "--step-through" in capsys.readouterr().err

    def test_gaia_api_rejects_abbreviation(self):
        # argparse prefix-matching must not resolve "--step" to anything.
        with pytest.raises(SystemExit) as exc:
            _parse_gaia(["api", "start", "--step"])
        assert exc.value.code == 2

    def test_module_entry_point_rejects_step_through(self, mocker, monkeypatch, capsys):
        mocker.patch.object(api_app, "warn_if_unconfirmed_tools_allowed")
        mocker.patch.object(api_app, "assert_bind_is_authenticated")
        mocker.patch("uvicorn.run")
        monkeypatch.setattr(sys, "argv", ["gaia.api.app", "start", "--step-through"])
        with pytest.raises(SystemExit) as exc:
            api_app.main()
        assert exc.value.code == 2
        assert "--step-through" in capsys.readouterr().err

    def test_start_server_has_no_step_through_parameter(self, mocker):
        # Stub the server so a regression fails the assertion instead of serving.
        mocker.patch.object(api_app, "warn_if_unconfirmed_tools_allowed")
        mocker.patch.object(api_app, "assert_bind_is_authenticated")
        run = mocker.patch("uvicorn.run")
        params = inspect.signature(api_app.start_server).parameters
        assert "step_through" not in params
        with pytest.raises(TypeError):
            # pylint: disable-next=unexpected-keyword-arg
            api_app.start_server(step_through=True)
        run.assert_not_called()


class TestRemainingFlags:
    def test_gaia_api_parses_the_remaining_debug_flags(self):
        args = _parse_gaia(["api", "start", "--debug", "--show-prompts", "--streaming"])
        assert args.debug and args.show_prompts and args.streaming
        assert not hasattr(args, "step_through")

    def test_start_server_sets_env_and_lists_only_real_features(self, mocker, capsys):
        import os

        mocker.patch.object(api_app, "warn_if_unconfirmed_tools_allowed")
        mocker.patch.object(api_app, "assert_bind_is_authenticated")
        run = mocker.patch("uvicorn.run")

        api_app.start_server(
            "localhost", 8080, False, debug=True, show_prompts=True, streaming=True
        )

        run.assert_called_once()
        assert run.call_args.args[0] == "gaia.api.openai_server:app"
        assert run.call_args.kwargs == {
            "host": "localhost",
            "port": 8080,
            "log_level": "debug",
        }
        assert os.environ["GAIA_API_DEBUG"] == "1"
        assert os.environ["GAIA_API_SHOW_PROMPTS"] == "1"
        assert os.environ["GAIA_API_STREAMING"] == "1"
        assert "GAIA_API_STEP_THROUGH" not in os.environ
        out = capsys.readouterr().out
        assert "Step-through" not in out
        assert "Debug logging" in out

    def test_no_debug_flags_prints_no_debug_banner(self, mocker, capsys):
        mocker.patch.object(api_app, "warn_if_unconfirmed_tools_allowed")
        mocker.patch.object(api_app, "assert_bind_is_authenticated")
        mocker.patch("uvicorn.run")

        api_app.start_server("localhost", 8080)

        assert "Debug features enabled" not in capsys.readouterr().out
