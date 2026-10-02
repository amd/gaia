# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""SWE-bench Verified: instances fetched at run time, a checkout without the fix,
a prediction the official harness accepts, and its report read back.

No network and no Docker: the dataset is a stub, the repository is a small
local one with the same shape (history, a base commit, a fix on top), and the
harness and docker calls are recorded instead of run.
"""

import importlib.metadata
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from gaia import cli
from gaia.eval import flagship_tasks as ft
from gaia.eval.bench import config as bench_config
from gaia.eval.bench import gaia_child, harness, sandbox, swebench

from .conftest import gaia_tool

pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="needs git")

REQUESTS = "psf__requests-1921"
FLASK = "pallets__flask-5014"
IMAGE = "swebench/sweb.eval.x86_64.psf_1776_requests-1921:latest"


def _row(instance_id=REQUESTS, **over):
    """A dataset row as the datasets-server returns it, plus columns we drop."""
    row = {
        "instance_id": instance_id,
        "repo": "psf/requests",
        "base_commit": "3c88e520da24ae6f736929a750876e7654accc3d",
        "problem_statement": "Removing a default header of a session\nsends None.",
        "FAIL_TO_PASS": ["test_requests.py::RequestsTestCase::test_none_header"],
        "PASS_TO_PASS": ["test_requests.py::RequestsTestCase::test_basic"],
        "image": IMAGE,
        "version": "2.3",
        "patch": "diff --git a/requests/sessions.py b/requests/sessions.py\n+fix\n",
        "hints_text": "not kept",
        "test_patch": "not kept",
        "eval_script": "not kept",
    }
    row.update(over)
    return row


def _stub(*rows):
    fetched = {r["instance_id"]: r for r in rows}
    asked = []

    def fetch(ids):
        asked.append(list(ids))
        return {i: fetched[i] for i in ids if i in fetched}

    fetch.asked = asked
    return fetch


# ---------------------------------------------------------------------------
# Instances
# ---------------------------------------------------------------------------


def test_instances_are_fetched_once_cut_to_the_columns_and_cached(tmp_path):
    fetch = _stub(_row(), _row(FLASK, repo="pallets/flask"))
    cache = tmp_path / "swebench"
    first = swebench.load_instances([FLASK, REQUESTS], cache, fetch)
    assert [i["instance_id"] for i in first] == [FLASK, REQUESTS], "in the order asked"
    assert set(first[0]) == set(swebench.COLUMNS), "hints and eval script are dropped"
    assert (cache / f"{REQUESTS}.json").is_file()
    again = swebench.load_instances([REQUESTS], cache, fetch)
    assert again == [first[1]]
    assert fetch.asked == [[FLASK, REQUESTS]], "the second load never fetched"


def test_test_lists_stored_as_json_text_become_lists(tmp_path):
    fetch = _stub(_row(FAIL_TO_PASS='["t.py::a", "t.py::b"]', PASS_TO_PASS="[]"))
    (inst,) = swebench.load_instances([REQUESTS], tmp_path, fetch)
    assert inst["FAIL_TO_PASS"] == ["t.py::a", "t.py::b"]
    assert inst["PASS_TO_PASS"] == []


def test_an_unknown_instance_and_a_row_missing_the_image_are_named(tmp_path):
    with pytest.raises(swebench.SweBenchError, match=r"no instance \['nope__x-1'\]"):
        swebench.load_instances(["nope__x-1"], tmp_path, _stub(_row()))
    old = _row()
    del old["image"]
    with pytest.raises(swebench.SweBenchError, match="lacks \\['image'\\]"):
        swebench.load_instances([REQUESTS], tmp_path, _stub(old))
    with pytest.raises(swebench.SweBenchError, match="repeat"):
        swebench.load_instances([REQUESTS, REQUESTS], tmp_path, _stub(_row()))
    with pytest.raises(swebench.SweBenchError, match="no SWE-bench instances"):
        swebench.load_instances([], tmp_path, _stub())


def test_the_rest_fetcher_pages_the_split_until_every_id_is_seen(monkeypatch):
    pages = [
        {"rows": [{"row": _row("a__a-1")}], "num_rows_total": 3},
        {"rows": [{"row": _row(FLASK)}], "num_rows_total": 3},
        {"rows": [{"row": _row("z__z-9")}], "num_rows_total": 3},
    ]
    urls = []

    class Resp:
        def __init__(self, page):
            self.page = page

        def raise_for_status(self):
            pass

        def json(self):
            return self.page

    def get(url, timeout):
        urls.append(url)
        return Resp(pages[len(urls) - 1])

    import requests

    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(swebench, "ROWS_PER_PAGE", 1)
    found = swebench._fetch_with_rest([FLASK])
    assert list(found) == [FLASK]
    assert len(urls) == 2, "stops on the page that completes the set"
    assert "offset=1&length=1" in urls[1]
    assert "dataset=SWE-bench%2FSWE-bench_Verified" in urls[0]


def test_no_fetch_library_is_an_actionable_error(monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    with pytest.raises(swebench.SweBenchError, match="pip install datasets"):
        swebench.fetch_instances([REQUESTS])


# ---------------------------------------------------------------------------
# The checkout
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.com",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        message,
    )
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def upstream(tmp_path):
    """``older`` -> ``base`` -> ``fix`` on main, like the public repository."""
    repo = tmp_path / "public"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "sessions.py").write_text("def merge(a, b): return {**a, **b}\n")
    older = _commit(repo, "older")
    (repo / "README").write_text("requests\n")
    base = _commit(repo, "base")
    (repo / "sessions.py").write_text(
        "def merge(a, b): return {k: v for k, v in {**a, **b}.items() if v}\n"
    )
    fix = _commit(repo, "fix: drop None headers")
    return {"url": repo.as_uri(), "older": older, "base": base, "fix": fix}


@pytest.fixture
def instance(upstream):
    return {
        "instance_id": REQUESTS,
        "repo": "psf/requests",
        "base_commit": upstream["base"],
        "image": IMAGE,
    }


def test_the_checkout_is_the_base_with_no_fix_no_remote_and_no_stray_object(
    upstream, instance, tmp_path
):
    work = tmp_path / "work" / "requests"
    swebench.checkout(instance, work, tmp_path / "root", url=upstream["url"])
    assert _git(work, "rev-parse", "HEAD") == upstream["base"]
    assert _git(work, "remote") == ""
    assert _git(work, "branch", "--show-current") == "", "detached"
    assert upstream["fix"] not in _git(work, "log", "--all", "--format=%H")
    assert subprocess.run(
        ["git", "cat-file", "-e", upstream["fix"]], cwd=work, capture_output=True
    ).returncode
    assert upstream["older"] in _git(work, "log", "--format=%H")
    assert _git(work, "fsck", "--unreachable", "--no-reflogs") == ""
    assert "if v" not in (work / "sessions.py").read_text()
    # The cache holds the base's history and nothing after it.
    (cache,) = (tmp_path / "root" / "cache").glob("swebench-*.git")
    assert subprocess.run(
        ["git", "cat-file", "-e", upstream["fix"]], cwd=cache, capture_output=True
    ).returncode


def test_the_clone_goes_over_file_url_at_depth_200(
    upstream, instance, tmp_path, monkeypatch
):
    """A plain local path makes git ignore --depth and copy every object."""
    real, argv = swebench._git, []

    def record(args, cwd=None):
        argv.append(list(args))
        return real(args, cwd)

    monkeypatch.setattr(swebench, "_git", record)
    swebench.checkout(
        instance, tmp_path / "work" / "requests", tmp_path / "root", url=upstream["url"]
    )
    fetch = next(a for a in argv if a[0] == "fetch")
    clone = next(a for a in argv if a[0] == "clone")
    branch = f"base-{upstream['base'][:12]}"
    assert fetch == [
        "fetch",
        "--quiet",
        "--no-tags",
        "--depth=200",
        upstream["url"],
        f"{upstream['base']}:refs/heads/{branch}",
    ]
    assert clone[:6] == [
        "clone",
        "--quiet",
        "--no-tags",
        "--single-branch",
        f"--branch={branch}",
        "--depth=200",
    ]
    assert clone[6].startswith("file:///"), clone
    assert clone[6].endswith(".git")
    assert ["fsck", "--unreachable", "--no-reflogs"] in argv


def test_a_second_checkout_reuses_the_cache(upstream, instance, tmp_path, monkeypatch):
    root = tmp_path / "root"
    swebench.checkout(instance, tmp_path / "w1" / "r", root, url=upstream["url"])
    real, argv = swebench._git, []

    def record(args, cwd=None):
        argv.append(list(args))
        return real(args, cwd)

    monkeypatch.setattr(swebench, "_git", record)
    swebench.checkout(instance, tmp_path / "w2" / "r", root, url=upstream["url"])
    assert not any(a[0] == "fetch" for a in argv)
    assert _git(tmp_path / "w2" / "r", "rev-parse", "HEAD") == upstream["base"]


def test_the_github_url_comes_from_the_repo_column():
    assert swebench.repo_url({"repo": "psf/requests"}) == (
        "https://github.com/psf/requests.git"
    )


# ---------------------------------------------------------------------------
# Tasks and predictions
# ---------------------------------------------------------------------------


def test_the_task_is_the_problem_statement_plus_the_fixed_paragraph():
    raw = swebench.task_for(_row(problem_statement="  Bug.\n"))
    assert raw["prompt"] == f"Bug.\n\n{swebench.PROMPT_SUFFIX}"
    assert raw["prompt"].endswith("keep the change minimal.")
    assert (raw["id"], raw["check"], raw["closed_book"]) == (
        REQUESTS,
        "swebench",
        "instructed",
    )
    assert raw["swebench"]["image"] == IMAGE
    assert "patch" not in json.dumps(raw), "the gold patch never enters the task"
    task = ft._swebench_task(raw)
    assert (task.id, task.check, task.max_steps) == (REQUESTS, "swebench", 120)
    assert task.swebench["base_commit"] == raw["swebench"]["base_commit"]


def test_a_tasks_file_may_not_list_a_swebench_task(tmp_path):
    with pytest.raises(ValueError, match="built from the dataset"):
        ft._parse_task({"id": "x", "check": "swebench"}, tmp_path / "tasks.json")


def test_the_swebench_suite_is_built_from_the_cache(tmp_path):
    cache = swebench.cache_dir(tmp_path)
    swebench.load_instances([REQUESTS, FLASK], cache, _stub(_row(), _row(FLASK)))
    tasks = ft.load_suite("swebench", instances=[FLASK], work_root=tmp_path)
    assert [t.id for t in tasks] == [FLASK]
    assert ft.select(tasks, [FLASK]) == tasks
    with pytest.raises(ValueError, match="takes --tasks"):
        ft.load_suite("everyday", instances=[FLASK])
    with pytest.raises(ValueError, match="built from the dataset"):
        ft.load_suite("nope")


def test_the_prediction_row_is_what_the_harness_reads(upstream, instance, tmp_path):
    work = tmp_path / "work" / "requests"
    swebench.checkout(instance, work, tmp_path / "root", url=upstream["url"])
    preds = tmp_path / "out" / "predictions.jsonl"
    with pytest.raises(swebench.SweBenchError, match="changed nothing"):
        swebench.capture_prediction(work, REQUESTS, "gaia-m", preds)
    assert not preds.exists()
    (work / "sessions.py").write_text("def merge(a, b): return {}\n")
    (work / "new.py").write_text("x = 1\n")
    patch = swebench.capture_prediction(work, REQUESTS, "gaia-m", preds)
    (row,) = swebench.read_predictions(preds)
    assert row == {
        "instance_id": REQUESTS,
        "model_name_or_path": "gaia-m",
        "model_patch": patch,
    }
    assert "new.py" in patch, "a new file is in the patch"
    assert "--- a/sessions.py" in patch
    # Capturing again replaces the row rather than adding a second one.
    swebench.capture_prediction(work, REQUESTS, "gaia-m", preds)
    assert len(swebench.read_predictions(preds)) == 1
    assert ft.prediction_model_name("gaia", "fireworks/glm") == "gaia-fireworks__glm"


# ---------------------------------------------------------------------------
# The official harness
# ---------------------------------------------------------------------------


def _report(report_dir: Path, instance_id: str, **over):
    body = {
        "patch_is_None": False,
        "patch_exists": True,
        "patch_successfully_applied": True,
        "resolved": True,
        "infra_failure": False,
        "tests_status": {
            "FAIL_TO_PASS": {"success": ["t::a", "t::b"], "failure": []},
            "PASS_TO_PASS": {"success": ["t::c"], "failure": []},
            "FAIL_TO_FAIL": {"success": [], "failure": []},
            "PASS_TO_FAIL": {"success": [], "failure": []},
        },
    }
    body.update(over)
    path = (
        report_dir
        / "logs"
        / "run_evaluation"
        / swebench.RUN_ID
        / "gaia-m"
        / instance_id
        / "report.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({instance_id: body}, indent=4))
    return path


def test_the_report_is_read_the_way_swebench_5_writes_it(tmp_path):
    _report(tmp_path, REQUESTS)
    _report(
        tmp_path,
        FLASK,
        resolved=False,
        tests_status={
            "FAIL_TO_PASS": {"success": ["t::a"], "failure": ["t::b", "t::c"]},
            "PASS_TO_PASS": {"success": [], "failure": ["t::d"]},
        },
    )
    # A patch that did not apply: the harness writes no tests_status at all.
    _report(
        tmp_path, "z__z-1", patch_successfully_applied=False, resolved=False
    ).write_text(json.dumps({"z__z-1": {"patch_exists": True, "resolved": False}}))
    verdicts = swebench.parse_report(tmp_path)
    assert verdicts[REQUESTS].resolved is True
    assert verdicts[REQUESTS].why == "resolved; FAIL_TO_PASS 2/2, PASS_TO_PASS 1/1"
    assert verdicts[FLASK].why == "unresolved; FAIL_TO_PASS 1/3, PASS_TO_PASS 0/1"
    assert verdicts["z__z-1"].why == (
        "unresolved; FAIL_TO_PASS 0/0, PASS_TO_PASS 0/0; patch did not apply"
    )
    assert verdicts[FLASK].as_dict()["f2p_total"] == 3
    assert swebench.parse_report(tmp_path / "empty") == {}


def _preds(tmp_path, *ids):
    path = tmp_path / "predictions.jsonl"
    path.write_text(
        "".join(
            json.dumps(
                {"instance_id": i, "model_name_or_path": "gaia-m", "model_patch": "+x"}
            )
            + "\n"
            for i in ids
        )
    )
    return path


@pytest.fixture
def fake_tools(monkeypatch):
    """swebench importable, docker on PATH, and every subprocess recorded."""
    calls = []
    monkeypatch.setattr(swebench, "_grade_in_container", lambda: False)
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name: object() if name == "swebench" else None,
    )
    monkeypatch.setattr(
        shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None
    )

    class Proc:
        def __init__(self, args):
            self.args, self.returncode, self.stdout, self.stderr = args, 0, "", ""

    def run(args, **kwargs):
        calls.append({"args": list(args), "cwd": kwargs.get("cwd")})
        proc = Proc(list(args))
        hook = getattr(run, "on_call", None)
        if hook:
            hook(proc)
        return proc

    monkeypatch.setattr(subprocess, "run", run)
    run.calls = calls
    return run


def test_evaluate_pulls_grades_and_removes_one_image_at_a_time(tmp_path, fake_tools):
    preds = _preds(tmp_path, REQUESTS, FLASK)
    flask_image = "swebench/sweb.eval.x86_64.pallets_1776_flask-5014:latest"
    instances = [
        {"instance_id": REQUESTS, "image": IMAGE},
        {"instance_id": FLASK, "image": flask_image},
    ]
    report_dir = tmp_path / "swebench"

    def on_call(proc):
        if "swebench.harness.run_evaluation" in proc.args:
            _report(report_dir, proc.args[proc.args.index("--instance_ids") + 1])

    fake_tools.on_call = on_call
    verdicts = swebench.evaluate(preds, instances, report_dir)
    argv = [c["args"] for c in fake_tools.calls]
    assert argv[0] == ["/usr/bin/docker", "info"]
    assert argv[1] == [
        "/usr/bin/docker",
        "pull",
        "--quiet",
        "--platform",
        "linux/amd64",
        IMAGE,
    ]
    assert argv[2] == [
        sys.executable,
        "-m",
        "swebench.harness.run_evaluation",
        "--dataset_name",
        "SWE-bench/SWE-bench_Verified",
        "--split",
        "test",
        "--instance_ids",
        REQUESTS,
        "--predictions_path",
        str(preds),
        "--run_id",
        "gaia-bench",
        "--max_workers",
        "1",
        "--timeout",
        "1800",
        "--report_dir",
        str(report_dir),
    ]
    assert fake_tools.calls[2]["cwd"] == str(
        report_dir
    ), "its logs land in the report dir"
    assert argv[3] == ["/usr/bin/docker", "rmi", IMAGE]
    assert argv[4][1:] == ["pull", "--quiet", "--platform", "linux/amd64", flask_image]
    assert argv[6] == ["/usr/bin/docker", "rmi", flask_image]
    assert len(argv) == 7
    assert verdicts[REQUESTS].resolved is True and verdicts[FLASK].resolved is True
    assert (report_dir / f"{REQUESTS}.harness.log").read_text().startswith("$ ")


def test_evaluate_can_keep_images_and_change_the_platform(tmp_path, fake_tools):
    preds = _preds(tmp_path, REQUESTS)
    fake_tools.on_call = lambda proc: (
        _report(tmp_path / "r", REQUESTS)
        if "swebench.harness.run_evaluation" in proc.args
        else None
    )
    swebench.evaluate(
        preds,
        [{"instance_id": REQUESTS, "image": IMAGE}],
        tmp_path / "r",
        pull_then_remove=False,
    )
    assert [c["args"][1] for c in fake_tools.calls] == ["info", "-m"]
    fake_tools.calls.clear()
    swebench.evaluate(
        preds,
        [{"instance_id": REQUESTS, "image": IMAGE}],
        tmp_path / "r",
        docker_platform="linux/arm64",
    )
    assert fake_tools.calls[1]["args"][3:5] == ["--platform", "linux/arm64"]


def test_a_failed_harness_run_is_the_instances_verdict_not_the_runs_crash(
    tmp_path, fake_tools
):
    preds = _preds(tmp_path, REQUESTS, FLASK)

    def on_call(proc):
        if "swebench.harness.run_evaluation" not in proc.args:
            return
        instance_id = proc.args[proc.args.index("--instance_ids") + 1]
        if instance_id == REQUESTS:
            proc.returncode, proc.stderr = 1, "boom"
        else:
            _report(tmp_path / "r", instance_id)

    fake_tools.on_call = on_call
    verdicts = swebench.evaluate(
        preds,
        [
            {"instance_id": REQUESTS, "image": IMAGE},
            {"instance_id": FLASK, "image": "i2"},
            {"instance_id": "never__submitted-1", "image": "i3"},
        ],
        tmp_path / "r",
    )
    assert verdicts[REQUESTS].resolved is None
    assert "exited 1" in verdicts[REQUESTS].error
    assert verdicts[FLASK].resolved is True, "the next instance still ran"
    assert verdicts["never__submitted-1"].error == "no patch was submitted"
    # The image of the failed run was still removed.
    assert ["/usr/bin/docker", "rmi", IMAGE] in [c["args"] for c in fake_tools.calls]


def test_a_failed_pull_stops_the_grading(tmp_path, fake_tools):
    def on_call(proc):
        if proc.args[1] == "pull":
            proc.returncode, proc.stderr = 1, "no such image"

    fake_tools.on_call = on_call
    with pytest.raises(swebench.SweBenchError, match="docker pull --platform"):
        swebench.evaluate(
            _preds(tmp_path, REQUESTS),
            [{"instance_id": REQUESTS, "image": IMAGE}],
            tmp_path / "r",
        )


def test_grading_without_the_harness_or_docker_says_what_to_install(
    tmp_path, monkeypatch
):
    preds = _preds(tmp_path, REQUESTS)
    monkeypatch.setattr(swebench, "_grade_in_container", lambda: False)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    with pytest.raises(swebench.SweBenchError, match="pip install swebench"):
        swebench.evaluate(preds, [], tmp_path / "r")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(swebench.SweBenchError, match="Docker on PATH"):
        swebench.evaluate(preds, [], tmp_path / "r")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker")

    class Down:
        returncode, stdout, stderr = 1, "", "Cannot connect to the Docker daemon"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Down())
    with pytest.raises(swebench.SweBenchError, match="not answering"):
        swebench.evaluate(preds, [], tmp_path / "r")


def test_no_predictions_is_an_error_naming_the_file(tmp_path, fake_tools):
    with pytest.raises(swebench.SweBenchError, match="no predictions at"):
        swebench.evaluate(tmp_path / "none.jsonl", [], tmp_path / "r")


# ---------------------------------------------------------------------------
# Through the suite: run, grade, judge
# ---------------------------------------------------------------------------


@pytest.fixture
def pilot(upstream, tmp_path, monkeypatch, bench_env):
    """Two cached instances whose repository is the local one."""
    work_root = bench_env / "work"
    cache = swebench.cache_dir(work_root)
    swebench.load_instances(
        [REQUESTS, FLASK],
        cache,
        _stub(
            _row(base_commit=upstream["base"]),
            _row(FLASK, repo="pallets/flask", base_commit=upstream["base"]),
        ),
    )
    monkeypatch.setattr(swebench, "repo_url", lambda instance: upstream["url"])
    # Building a real venv per test is slow; one test below builds it.
    monkeypatch.setattr(
        swebench,
        "tool_interpreter",
        lambda root: _tool_python(swebench.cache_dir(root)),
    )
    return work_root


def _tool_python(root: Path) -> Path:
    venv = root / "toolchain" / "py3"
    return venv / ("Scripts" if os.name == "nt" else "bin") / "python"


def test_a_swebench_run_captures_each_patch_and_leaves_the_pass_to_the_harness(
    pilot, fake_launch, tmp_path
):
    def act(workdir):
        if workdir.name == "requests":
            (workdir / "sessions.py").write_text("def merge(a, b): return {}\n")
        return "changed sessions.py"

    fake_launch.act = act
    fake_launch.conversation = gaia_tool("edit_file", {"path": "sessions.py"}, "ok")
    out = tmp_path / "out"
    card = ft.run_suite(
        "swebench",
        "m",
        out,
        config=bench_config.resolve(work_root=str(pilot)),
        instances=[REQUESTS, FLASK],
    )
    assert card["swebench_instances"] == [REQUESTS, FLASK]
    requests_task, flask_task = card["tasks"]
    assert requests_task["passed"] is None
    assert requests_task["why"] == (
        "patch captured (1 files); the official harness decides"
    )
    assert flask_task["passed"] is False
    assert "changed nothing" in flask_task["why"]
    rows = swebench.read_predictions(out / "predictions.jsonl")
    assert [r["instance_id"] for r in rows] == [REQUESTS]
    assert rows[0]["model_name_or_path"] == "gaia-m"
    assert "--- a/sessions.py" in (out / REQUESTS / "workspace.diff").read_text()
    transcript = json.loads((out / REQUESTS / "transcript.json").read_text())
    assert transcript["prompt"].endswith(swebench.PROMPT_SUFFIX)
    assert "Removing a default header" in transcript["prompt"]
    assert fake_launch.calls[0]["cwd"].name == "requests"


def test_a_run_cut_off_at_the_cap_is_shown_but_not_submitted(
    pilot, fake_launch, tmp_path
):
    fake_launch.act = lambda workdir: (
        (workdir / "sessions.py").write_text("half\n") or "unfinished"
    )
    fake_launch.timed_out = True
    out = tmp_path / "out"
    card = ft.run_suite(
        "swebench",
        "m",
        out,
        config=bench_config.resolve(work_root=str(pilot), run_timeout=5),
        instances=[REQUESTS],
    )
    (entry,) = card["tasks"]
    assert entry["timed_out"] and "timed out" in entry["error"]
    assert not (out / "predictions.jsonl").exists()
    assert "--- a/sessions.py" in (out / REQUESTS / "workspace.diff").read_text()


# ---------------------------------------------------------------------------
# Isolation from the host's installed packages
# ---------------------------------------------------------------------------


def test_the_agents_interpreter_cannot_import_what_the_host_has_installed(tmp_path):
    """matplotlib-24870: the agent imported the host's 3.11 matplotlib, fix included."""
    python = swebench.tool_interpreter(tmp_path)
    proc = subprocess.run(
        [str(python), "-c", "import pytest"], capture_output=True, text=True
    )
    assert importlib.util.find_spec("pytest") is not None
    assert proc.returncode != 0 and "No module named 'pytest'" in proc.stderr
    pip = subprocess.run(
        [str(python), "-m", "pip", "--version"], capture_output=True, text=True
    )
    assert pip.returncode != 0, "the agent could pip install the fixed release"
    built = python.stat().st_mtime_ns
    assert swebench.tool_interpreter(tmp_path) == python
    assert python.stat().st_mtime_ns == built, "a second task rebuilt a clean venv"


def test_a_package_an_agent_installed_is_gone_by_the_next_task(tmp_path):
    python = swebench.tool_interpreter(tmp_path)
    venv = python.parent.parent
    (site_packages,) = list(venv.glob("Lib/site-packages")) + list(
        venv.glob("lib/python*/site-packages")
    )
    (site_packages / "matplotlib").mkdir()
    (site_packages / "__editable__.checkout.pth").write_text("/elsewhere")
    assert swebench.tool_interpreter(tmp_path) == python
    assert sorted(p.name for p in site_packages.iterdir()) == []


def test_the_installed_copy_is_the_packages_dirs_and_its_metadata():
    copy = swebench.installed_copy("pytest-dev/pytest")
    names = {p.name for p in copy}
    assert {"pytest", "_pytest"} <= names
    assert any(n.startswith("pytest-") and n.endswith(".dist-info") for n in names)
    assert all(p.parent.name in ("site-packages", "dist-packages") for p in copy)
    assert swebench.installed_copy("nobody/no-such-distribution") == ()


def test_host_site_packages_holds_the_running_interpreters_packages():
    trees = swebench.host_site_packages()
    pytest_home = Path(importlib.util.find_spec("pytest").origin).resolve().parents[1]
    assert pytest_home in trees
    assert all(t.name in ("site-packages", "dist-packages") for t in trees)


def test_gaias_imports_follow_requirements_but_not_extras(monkeypatch):
    graph = {
        "root": ["Needed_Pkg>=1", "only-for-docs; extra == 'docs'"],
        "needed-pkg": ["deep.dep ; python_version >= '3.8'"],
    }

    def requires(name):
        if name not in graph and name != "deep-dep":
            raise importlib.metadata.PackageNotFoundError(name)
        return graph.get(name, [])

    monkeypatch.setattr(swebench.importlib.metadata, "requires", requires)
    assert swebench.gaia_imports.__wrapped__(("root",)) == {
        "root",
        "needed-pkg",
        "deep-dep",
    }


def test_the_fence_leaves_gaia_the_packages_it_imports_itself(monkeypatch):
    monkeypatch.setattr(swebench, "gaia_imports", lambda: frozenset({"requests"}))
    monkeypatch.setattr(swebench, "installed_copy", lambda repo: (Path(repo),))
    assert swebench.deniable_copy("psf/requests") == ()
    assert swebench.deniable_copy("matplotlib/matplotlib") == (
        Path("matplotlib/matplotlib"),
    )


@pytest.mark.parametrize("harness_name", [harness.GAIA, harness.CLAUDE_CODE])
def test_both_harnesses_get_only_the_bare_interpreter(
    pilot, fake_launch, tmp_path, monkeypatch, harness_name
):
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "host-src"))
    monkeypatch.setenv(
        "PATH", os.pathsep.join([harness.toolchain_dir(), os.environ["PATH"]])
    )
    specs = []

    def act(workdir):
        spec = workdir.parent / "harness" / "gaia-spec.json"
        if spec.is_file():
            specs.append(json.loads(spec.read_text()))
        return "done"

    fake_launch.act = act
    ft.run_suite(
        "swebench",
        "m",
        tmp_path / "out",
        config=bench_config.resolve(work_root=str(pilot), harness=harness_name),
        instances=[REQUESTS],
    )
    call = fake_launch.calls[0]
    tools = _tool_python(swebench.cache_dir(pilot))
    path = call["env"]["PATH"].split(os.pathsep)
    assert path[1] == str(tools.parent)
    if sys.prefix != sys.base_prefix:
        assert harness.toolchain_dir() not in path
    assert call["env"]["VIRTUAL_ENV"] == str(tools.parent.parent)
    if harness_name == harness.GAIA:
        assert [spec["tool_python"] for spec in specs] == [str(tools)]
        assert str(tmp_path / "host-src") not in call["env"]["PYTHONPATH"]
    else:
        assert "PYTHONPATH" not in call["env"]


@pytest.mark.parametrize(
    "harness_name, denied",
    [
        (harness.CLAUDE_CODE, "every site-packages"),
        (harness.GAIA, "the package under test"),
    ],
)
def test_the_fence_denies_the_hosts_packages(pilot, harness_name, denied, monkeypatch):
    monkeypatch.setattr(
        swebench, "host_site_packages", lambda: (Path("every site-packages"),)
    )
    monkeypatch.setattr(
        swebench, "deniable_copy", lambda repo: (Path("the package under test"),)
    )
    config = bench_config.BenchConfig(harness=harness_name, work_root=pilot, fence=True)
    ctx = ft.RunContext(
        config=config, gateway_url="http://x", scrubber=None, fenced=(pilot,)
    )
    task = ft.Task(
        id=REQUESTS,
        check=swebench.CHECK,
        prompt="p",
        max_steps=1,
        swebench={"repo": "psf/requests"},
    )
    fenced, read_write, read_only = ft._conditions(
        ctx, pilot / "root", pilot / "root" / "requests", task
    ).fence
    assert Path(denied) in fenced
    venv = _tool_python(swebench.cache_dir(pilot)).parent.parent
    assert read_only == (venv,)
    # Later SBPL rules win: the venv must be reopened after the work root's deny.
    rules = sandbox.profile(fenced, read_write, read_only).splitlines()

    def quoted(path):
        return f"(subpath {sandbox._q(path)})"

    deny = next(
        i for i, r in enumerate(rules) if r.startswith("(deny") and quoted(pilot) in r
    )
    allow = next(i for i, r in enumerate(rules) if quoted(venv) in r)
    assert deny < allow and "file-read-data" in rules[allow]


def test_the_gaia_child_runs_snippets_with_the_bare_interpreter(tmp_path, monkeypatch):
    seen = {}

    def fake_run_agent(*args, **kwargs):
        seen.update(executable=sys.executable, pythonpath=os.environ.get("PYTHONPATH"))
        return {}, "", ""

    monkeypatch.setattr(sys, "executable", sys.executable)
    monkeypatch.setenv("PYTHONPATH", "host-src")
    monkeypatch.setattr(ft, "_run_agent", fake_run_agent)
    gaia_child.run(
        {
            "prompt": "p",
            "model": "m",
            "max_steps": 1,
            "workdir": str(tmp_path),
            "memory_db": str(tmp_path / "m.db"),
            "progress": str(tmp_path / "progress.jsonl"),
            "tool_python": str(tmp_path / "bare" / "python"),
        }
    )
    assert seen == {"executable": str(tmp_path / "bare" / "python"), "pythonpath": None}


def test_grading_a_run_writes_the_harness_verdicts_into_the_scorecard(
    pilot, fake_launch, tmp_path, monkeypatch
):
    fake_launch.act = lambda workdir: (
        (workdir / "sessions.py").write_text("x\n") or "done"
    )
    out = tmp_path / "out"
    ft.run_suite(
        "swebench",
        "m",
        out,
        config=bench_config.resolve(work_root=str(pilot)),
        instances=[REQUESTS, FLASK],
    )
    seen = {}

    def fake_evaluate(preds_path, instances, report_dir, **kwargs):
        seen.update(
            preds=preds_path,
            ids=[i["instance_id"] for i in instances],
            report_dir=report_dir,
            **kwargs,
        )
        return {
            REQUESTS: swebench.Verdict(
                REQUESTS, resolved=True, patch_applied=True, f2p_passed=2, f2p_total=2
            ),
            FLASK: swebench.Verdict(FLASK, error="the harness exited 1"),
        }

    monkeypatch.setattr(swebench, "evaluate", fake_evaluate)
    progress = []
    card = ft.swebench_grade_run(
        out,
        pilot,
        docker_platform="linux/amd64",
        pull_then_remove=False,
        on_progress=lambda task_id, v: progress.append(task_id),
    )
    assert seen["preds"] == out / "predictions.jsonl"
    assert seen["ids"] == [REQUESTS, FLASK]
    assert seen["report_dir"] == out / "swebench"
    assert seen["pull_then_remove"] is False
    requests_task, flask_task = card["tasks"]
    assert requests_task["passed"] is True
    assert requests_task["why"] == "resolved; FAIL_TO_PASS 2/2, PASS_TO_PASS 0/0"
    assert requests_task["swebench"]["f2p_passed"] == 2
    assert flask_task["passed"] is None
    assert flask_task["why"] == "not graded: the harness exited 1"
    assert progress == [REQUESTS, FLASK]
    assert ft.read_scorecard(out)["tasks"][0]["passed"] is True
    assert ft.summarize(card)["passed"] == 1
    other = tmp_path / "other"
    other.mkdir()
    (other / "scorecard.json").write_text(
        json.dumps({"suite": "everyday", "tasks": []})
    )
    with pytest.raises(ValueError, match="only a swebench run"):
        ft.swebench_grade_run(other, pilot)


def test_a_regrade_replaces_an_earlier_grade(pilot, fake_launch, tmp_path, monkeypatch):
    """A broken grading (Windows CRLF: "patch did not apply") must be correctable."""
    fake_launch.act = lambda workdir: (
        (workdir / "sessions.py").write_text("x\n") or "done"
    )
    out = tmp_path / "out"
    ft.run_suite(
        "swebench",
        "m",
        out,
        config=bench_config.resolve(work_root=str(pilot)),
        instances=[REQUESTS],
    )
    verdicts = iter(
        [
            swebench.Verdict(REQUESTS, resolved=False, patch_applied=False),
            swebench.Verdict(
                REQUESTS, resolved=True, patch_applied=True, f2p_passed=1, f2p_total=1
            ),
        ]
    )
    monkeypatch.setattr(
        swebench, "evaluate", lambda *a, **k: {REQUESTS: next(verdicts)}
    )
    assert ft.swebench_grade_run(out, pilot)["tasks"][0]["passed"] is False
    assert ft.swebench_grade_run(out, pilot)["tasks"][0]["passed"] is True


def test_the_judge_grades_a_swebench_attempt_against_the_gold_patch(
    pilot, fake_launch, tmp_path, monkeypatch
):
    fake_launch.act = lambda workdir: (
        (workdir / "sessions.py").write_text("x\n") or "done"
    )
    out = tmp_path / "out"
    ft.run_suite(
        "swebench",
        "m",
        out,
        config=bench_config.resolve(work_root=str(pilot)),
        instances=[REQUESTS],
    )
    sent = []

    def fake_run(cmd, **kwargs):
        sent.append(kwargs["input"])

        class Proc:
            returncode, stderr = 0, ""
            stdout = json.dumps(
                {
                    "result": json.dumps(
                        {
                            REQUESTS: {
                                "instruction_compliance": 4,
                                "work_quality": 3,
                                "reasoning": 4,
                                "fabrication_free": 5,
                                "one_line": "plausible",
                                "solves_problem": False,
                                "right_place": True,
                                "updated_tests": False,
                                "approach": "wrong",
                            }
                        }
                    ),
                    "total_cost_usd": 0.1,
                }
            )

        return Proc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(ft, "judge_command", lambda model, env: ["claude"])
    monkeypatch.setattr(ft.shutil, "which", lambda name: "/bin/claude")
    card = ft.judge_run(out, "judge-m", {})
    (payload,) = sent
    assert "SWE-bench Verified" in payload and "TheRock" not in payload
    assert "THE PULL REQUEST THAT FIXED IT UPSTREAM" in payload
    assert "+fix" in payload, "the gold patch is the reference"
    assert (out / REQUESTS / "reference.diff").read_text().endswith("+fix\n")
    (entry,) = card["tasks"]
    assert entry["judge"]["approach"] == "wrong"
    assert (
        entry["passed"] is None
    ), "the harness, not the judge, decides a SWE-bench task"


def test_swebench_and_therock_attempts_are_not_mixed_in_one_call():
    tr = next(t for t in ft.load_suite("therock") if t.id == "tr-8319")
    sb = ft._swebench_task(swebench.task_for(_row()))
    transcript = {"prompt": "p", "answer": "a", "conversation": []}
    rock = ft.attempt_from("tr-8319", transcript, "d", tr, reference="ref")
    swe = ft.attempt_from(REQUESTS, transcript, "d", sb, reference="ref")
    assert rock.upstream_fix and swe.upstream_fix
    assert rock.batch_key != swe.batch_key
    with pytest.raises(ValueError, match="own batch"):
        ft.judge_batch([rock, swe], "m", {})


# ---------------------------------------------------------------------------
# The CLI
# ---------------------------------------------------------------------------

EVAL_TASKS = ["eval", "tasks"]


def _parse(*args):
    return cli.build_parser().parse_args([*EVAL_TASKS, *args])


@pytest.fixture
def run_cli(monkeypatch, tmp_path):
    def _run(*args):
        monkeypatch.setattr(sys, "argv", ["gaia", *EVAL_TASKS, *args])
        try:
            cli.main()
        except SystemExit as exit_code:
            return exit_code.code or 0
        return 0

    monkeypatch.setenv("GAIA_BENCH_WORK_ROOT", str(tmp_path / "work"))
    return _run


def test_the_parser_takes_instances_and_the_swebench_action():
    args = _parse("run", "--suite", "swebench", "--instances", "a__a-1,b__b-2")
    assert (args.instances, args.no_evaluate, args.keep_images) == (
        "a__a-1,b__b-2",
        False,
        False,
    )
    assert args.docker_platform == "linux/amd64"
    args = _parse(
        "swebench", "runs/x", "--keep-images", "--docker-platform", "linux/arm64"
    )
    assert (args.tasks_action, args.run_dir, args.keep_images) == (
        "swebench",
        "runs/x",
        True,
    )


def test_run_builds_the_suite_from_the_instances_then_grades_it(
    monkeypatch, tmp_path, run_cli
):
    seen = {}

    def fake_load_suite(name, *args, **kwargs):
        seen["loaded"] = (name, kwargs)
        return []

    monkeypatch.setattr(ft, "load_suite", fake_load_suite)
    monkeypatch.setattr(ft, "select", lambda tasks, only: tasks)

    def fake_run_suite(suite, model, out_dir, **kwargs):
        seen["run"] = kwargs
        return {"suite": suite, "model": model, "tasks": [], "harness": "gaia"}

    def fake_grade(run_dir, work_root, **kwargs):
        seen["graded"] = (run_dir, work_root, kwargs)
        return {"suite": "swebench", "model": "m", "tasks": [], "harness": "gaia"}

    monkeypatch.setattr(ft, "run_suite", fake_run_suite)
    monkeypatch.setattr(ft, "swebench_grade_run", fake_grade)
    monkeypatch.setattr(ft, "render_report", lambda card, checks: "")
    code = run_cli(
        "run",
        "--suite",
        "swebench",
        "--instances",
        f"{REQUESTS},{FLASK}",
        "--no-judge",
        "--keep-images",
        "--out",
        str(tmp_path / "out"),
    )
    assert code == 0
    assert seen["loaded"][1]["instances"] == [REQUESTS, FLASK]
    assert seen["run"]["instances"] == [REQUESTS, FLASK]
    run_dir, work_root, kwargs = seen["graded"]
    assert run_dir == tmp_path / "out"
    assert work_root == tmp_path / "work"
    assert kwargs["pull_then_remove"] is False
    assert kwargs["docker_platform"] == "linux/amd64"


def test_no_evaluate_leaves_the_predictions_for_later(monkeypatch, tmp_path, run_cli):
    monkeypatch.setattr(ft, "load_suite", lambda name, *a, **k: [])
    monkeypatch.setattr(ft, "select", lambda tasks, only: tasks)
    monkeypatch.setattr(
        ft,
        "run_suite",
        lambda suite, model, out_dir, **k: {
            "suite": suite,
            "model": model,
            "tasks": [],
            "harness": "gaia",
        },
    )
    graded = []
    monkeypatch.setattr(ft, "swebench_grade_run", lambda *a, **k: graded.append(a))
    monkeypatch.setattr(ft, "render_report", lambda card, checks: "")
    code = run_cli(
        "run",
        "--suite",
        "swebench",
        "--no-judge",
        "--no-evaluate",
        "--out",
        str(tmp_path / "o"),
    )
    assert code == 0
    assert graded == []


def test_the_swebench_action_grades_a_finished_run(monkeypatch, tmp_path, run_cli):
    out = tmp_path / "out"
    out.mkdir()
    (out / "scorecard.json").write_text(json.dumps({"suite": "swebench", "tasks": []}))
    seen = {}

    def fake_grade(run_dir, work_root, **kwargs):
        seen.update(run_dir=run_dir, work_root=work_root, **kwargs)
        return {"suite": "swebench", "model": "m", "tasks": [], "harness": "gaia"}

    monkeypatch.setattr(ft, "swebench_grade_run", fake_grade)
    monkeypatch.setattr(ft, "render_report", lambda card, checks: "")
    assert run_cli("swebench", str(out), "--work-root", str(tmp_path / "w")) == 0
    assert seen["run_dir"] == out and seen["work_root"] == tmp_path / "w"
    assert seen["pull_then_remove"] is True


def test_a_missing_harness_fails_the_command_naming_the_fix(
    monkeypatch, tmp_path, run_cli, capsys
):
    out = tmp_path / "out"
    out.mkdir()
    (out / "scorecard.json").write_text(json.dumps({"suite": "swebench", "tasks": []}))

    def fake_grade(*a, **k):
        raise swebench.SweBenchError("grading needs the official harness")

    monkeypatch.setattr(ft, "swebench_grade_run", fake_grade)
    assert run_cli("swebench", str(out)) == 1
    assert "grading needs the official harness" in capsys.readouterr().out


def test_an_instance_the_dataset_lacks_fails_at_startup(
    monkeypatch, tmp_path, run_cli, capsys
):
    monkeypatch.setattr(swebench, "fetch_instances", lambda ids: {})
    code = run_cli(
        "run", "--suite", "swebench", "--instances", "nope__x-1", "--no-judge"
    )
    assert code == 2
    assert "no instance ['nope__x-1']" in capsys.readouterr().out


def test_on_windows_the_harness_runs_in_a_linux_container(
    tmp_path, fake_tools, monkeypatch
):
    """Run on Windows, the harness wrote its eval script with CRLF endings and
    bash in the instance container ran `cd $'/testbed\r'`: every patch
    "failed to apply" and no test ran."""
    monkeypatch.setattr(swebench, "_grade_in_container", lambda: True)
    preds = _preds(tmp_path, REQUESTS)
    report_dir = tmp_path / "swebench"

    def on_call(proc):
        if "swebench.harness.run_evaluation" in proc.args:
            _report(report_dir, proc.args[proc.args.index("--instance_ids") + 1])
        if proc.args[1:3] == ["image", "inspect"]:
            proc.returncode = 1

    fake_tools.on_call = on_call
    verdicts = swebench.evaluate(
        preds, [{"instance_id": REQUESTS, "image": IMAGE}], report_dir
    )
    argv = [c["args"] for c in fake_tools.calls]
    assert argv[1] == ["/usr/bin/docker", "image", "inspect", swebench.GRADER_IMAGE]
    assert argv[2] == ["/usr/bin/docker", "build", "-t", swebench.GRADER_IMAGE, "-"]
    grade = next(a for a in argv if "swebench.harness.run_evaluation" in a)
    assert grade[:3] == ["/usr/bin/docker", "run", "--rm"]
    assert "/var/run/docker.sock:/var/run/docker.sock" in grade
    assert f"{report_dir.resolve()}:/work" in grade
    assert f"{preds.resolve().parent}:/preds:ro" in grade
    assert swebench.GRADER_IMAGE in grade
    # Paths inside the container are POSIX, whatever the host is.
    assert grade[grade.index("--predictions_path") + 1] == f"/preds/{preds.name}"
    assert grade[grade.index("--report_dir") + 1] == "/work"
    assert verdicts[REQUESTS].resolved is True


def test_a_built_grader_image_is_reused(tmp_path, fake_tools, monkeypatch):
    monkeypatch.setattr(swebench, "_grade_in_container", lambda: True)
    preds = _preds(tmp_path, REQUESTS)
    swebench.evaluate(
        preds, [{"instance_id": REQUESTS, "image": IMAGE}], tmp_path / "swebench"
    )
    argv = [c["args"] for c in fake_tools.calls]
    assert not any(a[1:2] == ["build"] for a in argv)


def test_the_grader_container_needs_no_host_install_of_swebench(monkeypatch):
    monkeypatch.setattr(swebench, "_grade_in_container", lambda: True)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker")

    class Up:
        returncode, stdout, stderr = 0, "", ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Up())
    swebench._require_harness()


def test_a_sample_is_seeded_sorted_and_reproducible():
    pool = [f"repo__pkg-{i}" for i in range(40)]
    first = swebench.sample_ids(5, seed=7, ids=pool)
    assert first == swebench.sample_ids(5, seed=7, ids=list(reversed(pool)))
    assert first == sorted(first) and len(set(first)) == 5
    assert first != swebench.sample_ids(5, seed=8, ids=pool)


def test_a_sample_larger_than_the_split_is_refused():
    with pytest.raises(swebench.SweBenchError, match="between 1 and 3"):
        swebench.sample_ids(4, ids=["a", "b", "c"])


def test_every_id_is_listed_over_rest_when_datasets_is_missing(monkeypatch):
    import requests

    rows = [{"row": {"instance_id": f"r__p-{i}"}} for i in range(3)]

    class Resp:
        def __init__(self, offset):
            self.offset = offset

        def raise_for_status(self):
            pass

        def json(self):
            return {"rows": rows[self.offset : self.offset + 1], "num_rows_total": 3}

    monkeypatch.setattr(swebench, "ROWS_PER_PAGE", 1)
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name: None if name == "datasets" else 1
    )
    monkeypatch.setattr(
        requests,
        "get",
        lambda url, timeout: Resp(int(url.split("offset=")[1].split("&")[0])),
    )
    assert swebench.all_instance_ids() == ["r__p-0", "r__p-1", "r__p-2"]
