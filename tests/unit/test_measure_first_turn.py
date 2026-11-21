# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The gates ``util/measure_first_turn.py`` applies to a real-model run.

The measurement needs a GPU, so it runs on the eval pool; a gate that quietly
stopped gating would not show up until a regression shipped. These pin each one
on hand-built legs.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

from gaia.llm import lemonade_client

_PATH = Path(__file__).parents[2] / "util" / "measure_first_turn.py"
_spec = importlib.util.spec_from_file_location("measure_first_turn", _PATH)
measure = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(measure)


def _turn(task, reads, cached, ok=True):
    return {
        "task": task,
        "ok": ok,
        "steps": len(reads),
        "reads": reads,
        "cached": cached,
        "wall_s": 1.0,
    }


def _off():
    return [_turn("hello", [21000], [0]), _turn("math", [100], [21000])]


def _on():
    return [_turn("hello", [13500], [0]), _turn("math", [100], [13500])]


def test_a_healthy_run_passes():
    assert measure.judge(_on(), _off()) == []


def test_a_grown_prefix_fails():
    on = [_turn("hello", [20000], [0]), _turn("math", [100], [20000])]
    assert any("first turn" in p for p in measure.judge(on, _off()))


def test_a_broken_cache_fails_even_when_the_first_turn_is_fine():
    on = [_turn("hello", [13500], [0]), _turn("math", [13600], [0])]
    assert any("prompt cache broke" in p for p in measure.judge(on, _off()))


def test_extra_model_calls_fail():
    on = [
        _turn("hello", [13500], [0]),
        _turn("math", [100, 100, 100, 100], [13500, 13600, 13700, 13800]),
    ]
    assert any("more model calls" in p for p in measure.judge(on, _off()))


def test_one_extra_call_is_normal_wobble():
    on = [_turn("hello", [13500], [0]), _turn("math", [100, 100], [13500, 13600])]
    assert measure.judge(on, _off()) == []


def test_missing_timings_fail_instead_of_passing_every_gate():
    on = [_turn("hello", [0], [0]), _turn("math", [0], [0])]
    assert any("no server timings" in p for p in measure.judge(on, _off()))
    assert any("no server timings" in p for p in measure.judge(_on(), on))


def test_a_turn_with_no_model_call_fails():
    on = [_turn("hello", [13500], [0]), _turn("math", [], [])]
    assert any("no server timings" in p for p in measure.judge(on, _off()))


def test_a_wrong_answer_fails():
    on = [_turn("hello", [13500], [0]), _turn("math", [100], [13500], ok=False)]
    assert any("wrong answer" in p for p in measure.judge(on, _off()))


_PORT = 64454
_KEY = "embedded-key"


@pytest.fixture
def embedded_home(tmp_path, monkeypatch):
    """The CI job's state: GAIA's own server on a private port, env file loaded."""
    home = tmp_path / "gaia-home"
    (home / "lemonade").mkdir(parents=True)
    (home / "lemonade" / "state.json").write_text(
        json.dumps({"port": _PORT, "api_key": _KEY, "pid": os.getpid()}),
        encoding="utf-8",
    )
    monkeypatch.setenv("GAIA_HOME", str(home))
    monkeypatch.setenv("LEMONADE_BASE_URL", f"http://localhost:{_PORT}/api/v1")
    monkeypatch.setenv("LEMONADE_API_KEY", _KEY)
    monkeypatch.setenv("GAIA_LEMONADE_EMBEDDED", "1")
    monkeypatch.setattr(
        lemonade_client.LemonadeClient, "health_check", lambda self, **_: {}
    )
    return home


def _leg_env(monkeypatch, lemonade):
    """The environment ``_run_leg`` hands its child process."""
    seen = {}

    def fake_run(cmd, env, **_):
        seen.update(env)
        Path(cmd[cmd.index("--leg") + 1]).write_text("[]", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(measure.subprocess, "run", fake_run)
    measure._run_leg("m", dynamic=True, lemonade=lemonade)
    return seen


def _resolve_in(env, monkeypatch):
    """Where Lemonade resolves to, and with which key, in *env*."""
    for key in list(os.environ):
        monkeypatch.delenv(key)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    url = lemonade_client.resolve_lemonade_base_url()
    return url, lemonade_client.resolve_lemonade_api_key(base_url=url)


def test_a_leg_under_an_empty_home_reaches_the_embedded_server(
    embedded_home, monkeypatch
):
    env = _leg_env(monkeypatch, measure._lemonade_env())

    assert env["GAIA_HOME"] != str(embedded_home)
    assert "GAIA_LEMONADE_EMBEDDED" not in env
    assert _resolve_in(env, monkeypatch) == (
        f"http://localhost:{_PORT}/api/v1",
        _KEY,
    )


def test_without_the_pin_the_leg_lands_on_the_default_port(embedded_home, monkeypatch):
    env = _leg_env(monkeypatch, {})
    env["GAIA_LEMONADE_EMBEDDED"] = "1"
    env["LEMONADE_BASE_URL"] = f"http://localhost:{_PORT}/api/v1"

    url, _ = _resolve_in(env, monkeypatch)
    assert f":{_PORT}/" not in url


def test_a_stale_key_is_not_handed_to_a_leg(embedded_home, monkeypatch):
    (embedded_home / "lemonade" / "state.json").unlink()
    monkeypatch.setenv("LEMONADE_API_KEY", "stale-key-from-old-env-file")

    lemonade = measure._lemonade_env()
    env = _leg_env(monkeypatch, lemonade)

    assert "LEMONADE_API_KEY" not in env


def test_an_unreachable_server_stops_the_run_before_any_leg(embedded_home, monkeypatch):
    def down(self, **_):
        raise lemonade_client.LemonadeServerDownError(self.base_url)

    monkeypatch.setattr(lemonade_client.LemonadeClient, "health_check", down)
    monkeypatch.setattr(
        measure, "_run_leg", lambda *a, **k: pytest.fail("ran a leg anyway")
    )

    with pytest.raises(SystemExit, match=f"localhost:{_PORT}.*not answering"):
        measure.main(["--model", "m"])
