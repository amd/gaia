# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""CI keeps running the real gaiacpp, where a person would meet it.

gaiacpp shipped broken in the desktop app while every lane was green, because
no lane started it the way the app does. These checks read the workflows as
data (jobs, matrices, the commands their steps run) and fail when a lane stops
running one of the suites that would have caught it, lets it fail quietly, or
goes back to a stand-in for the binary:

- the chaos test on the built binary (a misbehaving model server)
- the harness contract suite: both agents behind the real daemon, finding
  GAIA's own model server (tests/integration/test_harness_surface_contract.py)
- the terminal UI's tests on the real gaiacpp, never the mock agent
- first-run setup with gaiacpp chosen (the Playwright setup journey)
- both agents on a real model on the Strix Halo pool
"""

import importlib.util
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
CHAOS = REPO_ROOT / "cpp" / "tests" / "chaos" / "chaos_gaiacpp.py"
CONTRACT_SUITE = "tests/integration/test_harness_surface_contract.py"
HARDWARE_SUITE = "tests/hardware/test_halo_harness_surfaces.py"
FETCH = "util/fetch_ci_gaiacpp.py"
FIND_BUILT_GAIACPP = "find cpp/build -type f"


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _job(workflow: str, job: str) -> dict:
    return _workflow(workflow)["jobs"][job]


def _triggers(workflow: str) -> dict:
    # PyYAML reads the key `on` as True.
    return _workflow(workflow)[True]


def _running(job: dict, command: str) -> int:
    """Index of the one step of *job* whose script runs *command*."""
    hits = [i for i, step in enumerate(job["steps"]) if command in step.get("run", "")]
    assert len(hits) == 1, f"expected one step running {command!r}, got steps {hits}"
    return hits[0]


def _loud(job: dict, step: dict) -> None:
    """Neither the step nor its job may turn a failure into a pass."""
    assert not job.get("continue-on-error"), "the job is continue-on-error"
    assert not step.get("continue-on-error"), f"{step.get('name')} is continue-on-error"
    assert "|| true" not in step.get(
        "run", ""
    ), f"{step.get('name')} swallows a failure"


def _matrix_values(job: dict, key: str) -> set:
    matrix = job["strategy"]["matrix"]
    if key in matrix:
        return set(matrix[key])
    return {leg[key] for leg in matrix.get("include", [])}


@pytest.fixture(scope="module")
def chaos():
    spec = importlib.util.spec_from_file_location("chaos_gaiacpp", CHAOS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def fetch():
    spec = importlib.util.spec_from_file_location("fetch_ci_gaiacpp", REPO_ROOT / FETCH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── the chaos test ──


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
    "workflow, job_name, runs",
    [("build_cpp.yml", "build-and-test", 200), ("build_agents.yml", "build", 100)],
)
def test_the_chaos_test_runs_on_the_built_binary(workflow, job_name, runs):
    job = _job(workflow, job_name)
    index = _running(job, "cpp/tests/chaos/chaos_gaiacpp.py")
    step = job["steps"][index]
    assert _running(job, "cmake --build cpp/build") < index
    assert FIND_BUILT_GAIACPP in step["run"], "it must run the binary this job built"
    assert f"--runs {runs}" in step["run"]
    assert "if" not in step
    _loud(job, step)


def test_release_binaries_are_tested_on_every_platform_they_ship_to():
    job = _job("build_agents.yml", "build")
    assert _matrix_values(job, "lemonade_os") == {"windows", "linux", "darwin"}
    package = _running(job, "cpp/packaging/package_agents.py")
    for check in (
        "--mcp < init.json",
        "cpp/tests/chaos/chaos_gaiacpp.py",
        CONTRACT_SUITE,
    ):
        assert _running(job, check) < package, f"{check} runs after packaging"


def test_the_unit_and_chaos_tests_cover_every_desktop_os():
    job = _job("build_cpp.yml", "build-and-test")
    assert {"ubuntu-latest", "windows-latest", "macos-latest"} <= _matrix_values(
        job, "os"
    )


def test_a_cpp_change_triggers_both_workflows():
    for workflow in ("build_cpp.yml", "build_agents.yml"):
        assert "cpp/**" in _triggers(workflow)["pull_request"]["paths"], workflow


# ── the harness contract suite ──


def test_the_contract_suite_exists_and_covers_both_agents_on_every_surface():
    suite = (REPO_ROOT / CONTRACT_SUITE).read_text(encoding="utf-8")
    assert '"gaiapy,gaiacpp"' in suite, "both agents must run by default"
    surfaces = (
        REPO_ROOT / "tests" / "integration" / "harness_contract" / "surfaces.py"
    ).read_text(encoding="utf-8")
    assert 'SURFACES = ("ui", "cli", "stdio")' in surfaces
    # Its whole point: no address for the model server reaches the agents.
    world = (
        REPO_ROOT / "tests" / "integration" / "harness_contract" / "world.py"
    ).read_text(encoding="utf-8")
    assert "LEMONADE_BASE_URL=" not in world and '"LEMONADE_BASE_URL"' not in world


def test_the_contract_suite_runs_on_the_gaiacpp_each_platform_built():
    job = _job("build_agents.yml", "build")
    index = _running(job, CONTRACT_SUITE)
    step = job["steps"][index]
    assert _running(job, "cmake --build cpp/build") < index
    assert "python -m pytest" in step["run"]
    # The binary this leg built, not a download or a stand-in.
    assert FIND_BUILT_GAIACPP in step["run"]
    assert 'GAIA_TEST_GAIACPP="${bin}"' in step["run"]
    assert "GAIA_TEST_HARNESSES" not in step["run"] + str(step.get("env", {}))
    # Linux on a pull request; all three once the commit is on main.
    condition = " ".join(step["if"].split())
    assert "matrix.lemonade_os == 'linux'" in condition
    assert "github.event_name != 'pull_request'" in condition
    _loud(job, step)
    for event in ("push", "pull_request"):
        paths = _triggers("build_agents.yml")[event]["paths"]
        assert CONTRACT_SUITE in paths and "cpp/**" in paths


def test_a_change_on_the_python_side_of_the_seam_runs_the_contract_suite():
    job = _job("test_distributed_seams.yml", "distributed-seams")
    index = _running(job, CONTRACT_SUITE)
    step = job["steps"][index]
    fetched = job["steps"][_running(job, FETCH)]
    assert _running(job, FETCH) < index
    assert step["env"]["GAIA_TEST_GAIACPP"] == "${{ steps.gaiacpp.outputs.binary }}"
    assert fetched["id"] == "gaiacpp"
    # Without a cpp/ change in the pull request, gaiacpp is always part of the run.
    assert "harnesses=gaiapy,gaiacpp" in fetched["run"]
    _loud(job, step)
    _loud(job, fetched)
    assert _workflow("test_distributed_seams.yml")["permissions"]["actions"] == "read"
    paths = _triggers("test_distributed_seams.yml")["pull_request"]["paths"]
    for path in (
        "src/gaia/daemon/**",
        "src/gaia/print_mode.py",
        "src/gaia/llm/lemonade_*.py",
        "src/gaia/ui/sidecar/**",
        "hub/agents/gaia/python/gaiapy/**",
        "installer/headless/fake_model_server.py",
        CONTRACT_SUITE,
        "tests/integration/harness_contract/**",
    ):
        assert path in paths, f"{path} no longer triggers the contract suite"


# ── the terminal UI ──


def test_the_tui_tests_run_the_real_gaiacpp_on_every_os():
    job = _job("build_tui.yml", "test")
    assert _matrix_values(job, "os") == {
        "ubuntu-latest",
        "macos-latest",
        "windows-latest",
    }
    fetched = job["steps"][_running(job, FETCH)]
    tests = job["steps"][_running(job, "go test ./...")]
    assert _running(job, FETCH) < _running(job, "go test ./...")
    assert "GAIA_TEST_GAIACPP=${binary}" in fetched["run"]
    # With this set, a test that would fall back to the mock agent fails instead.
    assert "GAIA_TEST_REQUIRE_GAIACPP=1" in fetched["run"]
    assert "if" not in fetched and "if" not in tests
    _loud(job, fetched)
    _loud(job, tests)
    assert _workflow("build_tui.yml")["permissions"]["actions"] == "read"


def test_the_tui_tests_refuse_to_stand_the_mock_agent_in_for_gaiacpp():
    go_tests = REPO_ROOT / "tui" / "test"
    switch = (go_tests / "agent_program_e2e_test.go").read_text(encoding="utf-8")
    native = (go_tests / "native_agent_e2e_test.go").read_text(encoding="utf-8")
    assert 'requireNativeEnv = "GAIA_TEST_REQUIRE_GAIACPP"' in native
    assert re.search(r"os\.Getenv\(requireNativeEnv\) == \"1\" \{\s+t\.Fatalf", switch)
    assert re.search(r"os\.Getenv\(requireNativeEnv\) == \"1\" \{\s+t\.Fatalf", native)
    assert "func TestNativeAgentUsesGaiasOwnServerAndAsksForAFolder(" in native
    # That test may not hand gaiacpp the server's address.
    assert 't.Setenv("LEMONADE_BASE_URL", lemonadeStub' not in native


# ── setup, and the real model ──


def test_the_setup_journey_chooses_gaiacpp_on_every_os():
    job = _job("test_setup_journey.yml", "journey")
    assert _matrix_values(job, "os") == {
        "windows-latest",
        "macos-latest",
        "ubuntu-24.04",
    }
    fetched = job["steps"][_running(job, FETCH)]
    walk = job["steps"][_running(job, "npx playwright test")]
    assert _running(job, FETCH) < _running(job, "npx playwright test")
    assert "GAIA_E2E_GAIACPP=${binary}" in fetched["run"]
    assert "--grep" not in walk["run"] and "-g " not in walk["run"]
    _loud(job, fetched)
    _loud(job, walk)


def test_the_hardware_lane_runs_both_agents_on_a_real_model():
    workflow = _workflow("test_linux_strix_halo.yml")
    job = workflow["jobs"]["user-issues"]
    assert job["runs-on"] == ["self-hosted", "Linux", "xsj-dispatch", "strix-halo"]
    index = _running(job, HARDWARE_SUITE)
    step = job["steps"][index]
    assert _running(job, FETCH) < index
    assert "--junitxml=" in step["run"]
    assert step["timeout-minutes"] <= 15
    _loud(job, step)
    # It rides the lane's own triggers: release and RC tags, and dispatch.
    assert set(workflow[True]) == {"push", "workflow_dispatch"}
    suite = (REPO_ROOT / HARDWARE_SUITE).read_text(encoding="utf-8")
    assert 'HARNESSES = ("gaiapy", "gaiacpp")' in suite
    assert "LEMONADE_BASE_URL" not in suite


def test_the_fetched_gaiacpp_is_the_one_build_agents_packages(fetch):
    """The fetch helper and build_agents.yml agree on platforms and file names."""
    job = _job("build_agents.yml", "build")
    built = _matrix_values(job, "platform")
    assert set(fetch.PLATFORMS.values()) == built
    upload = next(s for s in job["steps"] if "upload-artifact" in str(s.get("uses")))
    assert upload["with"]["name"] == "cpp-agents-${{ matrix.platform }}"
    manifest = yaml.safe_load(
        (REPO_ROOT / "cpp" / "agents" / "gaiacpp" / "gaia-agent.yaml").read_text(
            encoding="utf-8"
        )
    )
    for platform_id in built:
        assert manifest["cpp"]["binaries"][platform_id] == fetch.packaged_name(
            platform_id
        )


def test_an_expired_artifact_on_the_newest_run_falls_back_to_an_older_run(
    fetch, monkeypatch
):
    runs = [{"id": 3}, {"id": 2}, {"id": 1}]
    listings = {
        3: [{"expired": True, "archive_download_url": "u3"}],
        2: [],
        1: [{"expired": False, "archive_download_url": "u1"}],
    }

    def fake_json(url, token):
        if "/workflows/" in url:
            assert "per_page=10" in url
            return {"workflow_runs": runs}
        run_id = int(url.split("/runs/")[1].split("/")[0])
        return {"artifacts": listings[run_id]}

    monkeypatch.setattr(fetch, "_json", fake_json)
    run, artifact = fetch.newest_with_artifact(
        "amd/gaia", "main", "cpp-agents-linux-x64", "t"
    )
    assert run["id"] == 1 and artifact["archive_download_url"] == "u1"

    listings[1] = []
    with pytest.raises(fetch.FetchError, match="none of the last 3 green"):
        fetch.newest_with_artifact("amd/gaia", "main", "cpp-agents-linux-x64", "t")
