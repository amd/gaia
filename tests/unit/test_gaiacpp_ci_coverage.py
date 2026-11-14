# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""CI keeps exercising the real gaiacpp binary, on every platform it ships to.

The unit tests link the agent's code into a test program. These checks hold
the steps that run the shipped executable itself: the chaos test against a
misbehaving model server, and the smoke test on each release platform. A
workflow edit that drops one, lets it fail quietly or moves it after packaging
fails here.
"""

import importlib.util
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
CHAOS = REPO_ROOT / "cpp" / "tests" / "chaos" / "chaos_gaiacpp.py"


def _job(workflow: str, job: str) -> dict:
    text = (WORKFLOWS / workflow).read_text(encoding="utf-8")
    return yaml.safe_load(text)["jobs"][job]


def _index(job: dict, name_part: str) -> int:
    hits = [i for i, s in enumerate(job["steps"]) if name_part in s.get("name", "")]
    assert len(hits) == 1, f"expected one step matching {name_part!r}, got {hits}"
    return hits[0]


@pytest.fixture(scope="module")
def chaos():
    spec = importlib.util.spec_from_file_location("chaos_gaiacpp", CHAOS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_scripted_reply_has_a_handler(chaos):
    missing = [name for name in chaos.FAULTS if not hasattr(chaos.Handler, "_" + name)]
    assert not missing, f"FAULTS names replies the server cannot send: {missing}"


def test_the_script_is_reproducible_from_its_seed(chaos):
    assert chaos.Script(42).steps == chaos.Script(42).steps
    assert chaos.Script(42).steps != chaos.Script(43).steps


def test_a_missing_binary_is_an_error_not_a_pass(chaos, tmp_path, capsys):
    assert chaos.main(["--binary", str(tmp_path / "no-such-gaiacpp")]) == 2
    assert "no gaiacpp binary" in capsys.readouterr().err


@pytest.mark.parametrize(
    "workflow, job, after, runs",
    [
        ("build_cpp.yml", "build-and-test", "Build", 200),
        ("build_agents.yml", "build", "Build (Release)", 100),
    ],
)
def test_the_chaos_test_runs_on_the_built_binary(workflow, job, after, runs):
    steps = _job(workflow, job)
    chaos_step = steps["steps"][_index(steps, "Chaos test gaiacpp")]
    assert _index(steps, after) < _index(steps, "Chaos test gaiacpp")
    assert "cpp/tests/chaos/chaos_gaiacpp.py" in chaos_step["run"]
    assert f"--runs {runs}" in chaos_step["run"]
    assert not chaos_step.get("continue-on-error") and "if" not in chaos_step


def test_release_binaries_face_chaos_before_they_are_packaged():
    job = _job("build_agents.yml", "build")
    smoke = _index(job, "Smoke-test gaiacpp")
    chaos_step = _index(job, "Chaos test gaiacpp")
    assert smoke < chaos_step < _index(job, "Package agents for")
    assert chaos_step < _index(job, "Package gaiacpp for Lemonade")


def test_the_unit_and_chaos_tests_cover_windows_and_linux():
    matrix = _job("build_cpp.yml", "build-and-test")["strategy"]["matrix"]["os"]
    assert {"ubuntu-latest", "windows-latest"} <= set(matrix)


def test_release_binaries_are_tested_on_every_platform_they_ship_to():
    legs = _job("build_agents.yml", "build")["strategy"]["matrix"]["include"]
    assert {leg["lemonade_os"] for leg in legs} == {"windows", "linux", "darwin"}


def test_a_cpp_change_triggers_both_workflows():
    for workflow in ("build_cpp.yml", "build_agents.yml"):
        text = (WORKFLOWS / workflow).read_text(encoding="utf-8")
        # PyYAML reads the key `on` as True.
        on = yaml.safe_load(text)[True]
        assert "cpp/**" in on["pull_request"]["paths"], workflow
