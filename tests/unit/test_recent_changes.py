# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Each audit reads what merged since its last run, from a bounded, exact file.

Built against a real throwaway git repository; only `gh` is stubbed.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "audit" / "recent_changes.py"


@pytest.fixture(scope="module")
def rc():
    spec = importlib.util.spec_from_file_location("recent_changes", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    shas = []
    for i, (path, subject) in enumerate(
        [
            ("src/gaia/a.py", "feat: start (#1)"),
            ("docs/guides/chat.mdx", "docs: chat guide (#2)"),
            ("src/gaia/llm/b.py", "fix(llm): stop a hang (#3)"),
        ]
    ):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(i), encoding="utf-8")
        git(root, "add", ".")
        git(root, "commit", "-q", "-m", subject)
        shas.append(git(root, "rev-parse", "HEAD"))
    monkeypatch.chdir(root)
    return root, shas


PRS = [
    {
        "number": 2,
        "title": "docs: chat guide",
        "body": "<!-- hidden -->The chat guide now says X.\n\n## Test plan\n- [ ] read it",
    },
    {
        "number": 3,
        "title": "fix(llm): stop a hang",
        "body": "Streams that stop now end.",
    },
]


def asked_numbers(args: list[str]) -> list[int]:
    query = next(a for a in args if a.startswith("query="))
    return [int(n) for n in re.findall(r"pullRequest\(number: (\d+)\)", query)]


def stub_gh(rc, monkeypatch, prs=PRS):
    """GitHub's GraphQL reply for the aliased PR lookups the script sends."""
    real = rc._run
    calls = []
    by_number = {p["number"]: p for p in prs}

    def fake(args):
        if args[0] == "gh":
            calls.append(args)
            repository = {f"p{n}": by_number.get(n) for n in asked_numbers(args)}
            missing = [n for n in asked_numbers(args) if n not in by_number]
            reply = {"data": {"repository": repository}}
            if missing:
                reply["errors"] = [
                    {"type": "NOT_FOUND", "path": ["repository", f"p{n}"]}
                    for n in missing
                ]
            return subprocess.CompletedProcess(
                args, 1 if missing else 0, json.dumps(reply), ""
            )
        return real(args)

    monkeypatch.setattr(rc, "_run", fake)
    return calls


def run_main(rc, tmp_path, *extra):
    out, paths, summary = tmp_path / "rc.md", tmp_path / "paths.txt", tmp_path / "s.md"
    rc.main(
        [
            "--repo",
            "amd/gaia",
            "--out",
            str(out),
            "--paths-out",
            str(paths),
            "--summary",
            str(summary),
            *extra,
        ]
    )
    return (
        out.read_text(encoding="utf-8"),
        paths.read_text(encoding="utf-8"),
        summary.read_text(encoding="utf-8"),
    )


def test_the_window_starts_exactly_at_the_last_runs_commit(
    rc, repo, tmp_path, monkeypatch, capsys
):
    _, shas = repo
    stub_gh(rc, monkeypatch)
    text, paths, summary = run_main(rc, tmp_path, "--base-sha", shas[0])
    assert (
        "2 commit(s): everything merged to main since this audit's last successful run"
        in text
    )
    assert "#1 " not in text
    assert paths.splitlines() == ["docs/guides/chat.mdx", "src/gaia/llm/b.py"]
    assert "Audit window is approximate" not in capsys.readouterr().out


def test_a_missing_last_run_falls_back_and_says_so(
    rc, repo, tmp_path, monkeypatch, capsys
):
    stub_gh(rc, monkeypatch)
    text, _, summary = run_main(rc, tmp_path, "--fallback-days", "3")
    assert "no successful run of this audit is on record" in text
    assert "no successful run of this audit is on record" in summary
    assert "Audit window is approximate" in capsys.readouterr().out


def test_an_unknown_last_run_commit_falls_back_and_says_why(
    rc, repo, tmp_path, monkeypatch
):
    stub_gh(rc, monkeypatch)
    text, _, _ = run_main(rc, tmp_path, "--base-sha", "0" * 40)
    assert "is not in this checkout's history" in text


def test_pr_bodies_are_excerpted_without_comments_or_test_plan(
    rc, repo, tmp_path, monkeypatch
):
    stub_gh(rc, monkeypatch)
    text, _, _ = run_main(rc, tmp_path)
    assert "The chat guide now says X." in text
    assert "hidden" not in text
    assert "read it" not in text


def test_the_file_never_exceeds_its_byte_cap(rc, repo, tmp_path, monkeypatch):
    stub_gh(rc, monkeypatch)
    text, _, _ = run_main(rc, tmp_path, "--max-bytes", "600")
    assert len(text.encode("utf-8")) <= 600 + 200  # header + elision line
    assert "older commit(s) omitted" in text


def test_one_gh_call_covers_every_pr(rc, repo, tmp_path, monkeypatch):
    calls = stub_gh(rc, monkeypatch)
    run_main(rc, tmp_path)
    assert len(calls) == 1


def test_only_the_prs_the_window_names_are_looked_up(rc, repo, tmp_path, monkeypatch):
    """Not every PR merged since some date: just the commits' own, by number."""
    _, shas = repo
    calls = stub_gh(rc, monkeypatch)
    run_main(rc, tmp_path, "--base-sha", shas[0])
    assert asked_numbers(calls[0]) == [3, 2]
    assert ["-f", "owner=amd"] == calls[0][5:7] and ["-f", "name=gaia"] == calls[0][7:]


def test_a_number_that_is_not_a_pr_keeps_its_commit_subject(
    rc, repo, tmp_path, monkeypatch
):
    """`(#1)` resolves to no PR (NOT_FOUND): the commit's own subject is shown."""
    stub_gh(rc, monkeypatch)
    text, _, _ = run_main(rc, tmp_path)
    assert "## #1 feat: start (#1)" in text
    assert "The chat guide now says X." in text


def test_lookups_are_batched_and_capped_and_the_cap_is_stated(rc, monkeypatch):
    commits = [
        {"sha": f"{n:040d}", "date": "", "subject": f"s (#{n})", "pr": n, "paths": []}
        for n in range(400, 0, -1)
    ]
    calls = stub_gh(
        rc,
        monkeypatch,
        prs=[{"number": n, "title": f"t{n}", "body": ""} for n in range(1, 401)],
    )
    prs, not_read = rc.fetch_prs("amd/gaia", commits, limit=120)
    assert [len(asked_numbers(c)) for c in calls] == [50, 50, 20]
    assert asked_numbers(calls[0])[0] == 400
    assert len(prs) == 120 and not_read == 280
    text = rc.render(commits, prs, "a window", 100_000, not_read)
    assert "read for the newest 120 PRs only; the 280 older PRs" in text


def test_a_lookup_error_other_than_not_found_stops_the_run(rc, monkeypatch):
    def fake(args):
        reply = {"errors": [{"type": "RATE_LIMITED", "message": "slow down"}]}
        return subprocess.CompletedProcess(args, 1, json.dumps(reply), "")

    monkeypatch.setattr(rc, "_run", fake)
    commits = [{"sha": "a", "date": "", "subject": "x (#5)", "pr": 5, "paths": []}]
    with pytest.raises(SystemExit) as excinfo:
        rc.fetch_prs("amd/gaia", commits)
    assert "RATE_LIMITED" in str(excinfo.value)


def test_a_gh_failure_stops_the_run(rc, repo, tmp_path, monkeypatch):
    real = rc._run

    def fake(args):
        if args[0] == "gh":
            return subprocess.CompletedProcess(args, 1, "", "HTTP 401")
        return real(args)

    monkeypatch.setattr(rc, "_run", fake)
    with pytest.raises(SystemExit) as excinfo:
        run_main(rc, tmp_path)
    assert "HTTP 401" in str(excinfo.value)
