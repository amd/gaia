# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A wheel install has no eval/ assets; eval must say a checkout is needed (#3198)."""

import pytest

from gaia.eval import runner
from gaia.eval.runner import AgentEvalRunner


@pytest.fixture
def wheel_install(tmp_path, monkeypatch):
    missing = tmp_path / "site-packages" / "eval" / "scenarios"
    monkeypatch.setattr(runner, "SCENARIOS_DIR", missing)
    return missing


def test_checkout_present_is_accepted(tmp_path, monkeypatch):
    scenarios = tmp_path / "eval" / "scenarios"
    scenarios.mkdir(parents=True)
    monkeypatch.setattr(runner, "SCENARIOS_DIR", scenarios)
    assert runner.eval_checkout_error() is None


def test_missing_scenarios_dir_names_the_fix(wheel_install):
    message = runner.eval_checkout_error()
    assert message is not None
    assert "source checkout" in message
    assert str(wheel_install) in message
    assert "git clone https://github.com/amd/gaia" in message
    assert "uv pip install -e" in message
    assert "https://amd-gaia.ai/docs/guides/eval" in message


def test_run_exits_before_searching_for_scenarios(wheel_install, monkeypatch, capsys):
    def no_search(*args, **kwargs):
        raise AssertionError("find_scenarios must not run without a checkout")

    monkeypatch.setattr(runner, "find_scenarios", no_search)
    with pytest.raises(SystemExit) as exc:
        AgentEvalRunner().run(scenario_id="simple_factual_rag")
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "source checkout" in err
    assert "No scenarios found" not in err
