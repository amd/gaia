# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "util") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "util"))

import gh_retry  # noqa: E402

from scripts.audit import audit_dispatch as dispatch  # noqa: E402

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "claude-audit-rotation.yml"


def _gh_response(
    status: int,
    body: str = "",
    stderr: str = "",
    **headers: str,
) -> subprocess.CompletedProcess:
    head = "\r\n".join(
        [f"HTTP/2.0 {status}", *(f"{k}: {v}" for k, v in headers.items())]
    )
    code = 0 if 200 <= status < 300 else 1
    return subprocess.CompletedProcess([], code, f"{head}\r\n\r\n{body}", stderr)


def _runs_body(confirmed: bool) -> str:
    runs = [
        {
            "display_title": title,
            "created_at": f"{year}-01-01T00:00:00Z",
            "html_url": url,
        }
        for title, year, url in (
            ("Audit — rotation night 1", "2000", "https://example.test/old"),
            ("Audit — rotation night 11", "2099", "https://example.test/similar"),
            ("Audit", "2099", "https://example.test/manual"),
        )
    ]
    if confirmed:
        runs.append(
            {
                "display_title": "Audit — rotation night 1",
                "created_at": "2099-01-01T00:00:00Z",
                "html_url": "https://example.test/verified",
            }
        )
    return json.dumps({"workflow_runs": runs})


def _install_clock(monkeypatch: pytest.MonkeyPatch, budget: int = 50):
    now = [1_000.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(dispatch.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(dispatch.time, "time", lambda: now[0])
    monkeypatch.setattr(dispatch.time, "sleep", sleep)
    monkeypatch.setattr(dispatch, "DISPATCH_BUDGET", budget)
    monkeypatch.setattr(dispatch, "CONFIRM_BUDGET", budget)
    return sleeps


@pytest.mark.parametrize(
    (
        "rejection",
        "budget",
        "accepted",
        "confirmed",
        "expected_posts",
        "expected_first_sleeps",
    ),
    [
        (None, 50, True, True, 1, []),
        (
            _gh_response(
                403,
                json.dumps({"message": "API rate limit exceeded for installation"}),
                "gh: API rate limit exceeded for installation (HTTP 403)",
                **{
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": "1040",
                },
            ),
            120,
            True,
            True,
            2,
            [40 + gh_retry._RESET_SLACK_SECONDS, 4, 5],
        ),
        (
            _gh_response(
                403,
                json.dumps({"message": "You have exceeded a secondary rate limit"}),
                "gh: HTTP 403",
                **{
                    "Retry-After": "7",
                    "X-RateLimit-Remaining": "4999",
                    "X-RateLimit-Reset": "9999",
                },
            ),
            50,
            True,
            True,
            2,
            [7, 4, 5],
        ),
        (
            _gh_response(
                403,
                "",
                "You have exceeded a secondary rate limit",
                **{
                    "X-RateLimit-Remaining": "4999",
                    "X-RateLimit-Reset": "9999",
                },
            ),
            50,
            True,
            True,
            2,
            [15, 4, 5],
        ),
        (
            _gh_response(429, "", "Too many requests"),
            50,
            True,
            True,
            2,
            [15, 4, 5],
        ),
        (
            _gh_response(
                403,
                json.dumps({"message": "Resource not accessible by integration"}),
                "gh: Resource not accessible by integration (HTTP 403)",
                **{
                    "X-RateLimit-Remaining": "4999",
                    "X-RateLimit-Reset": "9999",
                },
            ),
            50,
            False,
            False,
            1,
            [],
        ),
        (
            _gh_response(429, "", "Too many requests"),
            50,
            False,
            False,
            3,
            [15, 30],
        ),
        (
            _gh_response(
                403,
                "",
                "API rate limit exceeded for installation",
                **{
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": "2000",
                },
            ),
            120,
            False,
            False,
            1,
            [],
        ),
        (None, 25, True, False, 1, [4, 5]),
    ],
    ids=[
        "immediate-success",
        "installation-403-primary-reset",
        "secondary-403-retry-after",
        "secondary-403-backoff",
        "http-429-backoff",
        "permission-denied-403",
        "exhausted-429-budget",
        "exhausted-primary-reset-budget",
        "confirmation-timeout",
    ],
)
def test_dispatch(
    monkeypatch,
    tmp_path,
    rejection,
    budget,
    accepted,
    confirmed,
    expected_posts,
    expected_first_sleeps,
):
    summary = tmp_path / "summary"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    sleeps = _install_clock(monkeypatch, budget=budget)

    replies: list[subprocess.CompletedProcess] = []
    if rejection is not None:
        replies.append(rejection)
    if accepted:
        replies.append(_gh_response(204, **{"X-RateLimit-Remaining": "4998"}))
        if rejection is None and confirmed:
            replies.append(
                _gh_response(
                    200,
                    _runs_body(True),
                    **{"Content-Type": "application/json; charset=utf-8"},
                )
            )
        else:
            replies.append(_gh_response(429, "", "throttled", **{"Retry-After": "4"}))
            replies.append(
                _gh_response(
                    200,
                    _runs_body(False),
                    **{"Content-Type": "application/json; charset=utf-8"},
                )
            )
            for _ in range(6):
                replies.append(
                    _gh_response(
                        200,
                        _runs_body(confirmed),
                        **{"Content-Type": "application/json; charset=utf-8"},
                    )
                )
    else:
        replies.extend([rejection] * 4)

    for reply in replies:
        assert "\n\n" not in reply.stdout

    gh = Mock(side_effect=replies)
    monkeypatch.setattr(dispatch.subprocess, "run", gh)

    if confirmed:
        dispatch.main("amd/gaia", "audit.yml", "1")
        assert (
            summary.read_text(encoding="utf-8").strip()
            == "Started `audit.yml` for rotation night 1: https://example.test/verified"
        )
    else:
        err_type = TimeoutError if accepted else RuntimeError
        with pytest.raises(err_type):
            dispatch.main("amd/gaia", "audit.yml", "1")
        assert not summary.exists()

    post_cmd = [
        "gh",
        "api",
        "--include",
        "repos/amd/gaia/actions/workflows/audit.yml/dispatches",
        "-f",
        "ref=main",
        "-f",
        "inputs[rotation_night]=1",
    ]
    get_cmd = [
        "gh",
        "api",
        "--include",
        "repos/amd/gaia/actions/workflows/audit.yml/runs"
        "?branch=main&event=workflow_dispatch&per_page=100",
    ]
    calls = [call.args[0] for call in gh.call_args_list]
    posts = [cmd for cmd in calls if cmd == post_cmd]
    assert len(posts) == expected_posts
    if accepted:
        assert any(cmd == get_cmd for cmd in calls)
    assert sleeps[: len(expected_first_sleeps)] == expected_first_sleeps
    if not expected_first_sleeps:
        assert sleeps == []


def test_workflow_wires_failure_notice_and_dry_run_guards():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow.get("on") or workflow.get(True)
    dry_run_input = triggers["workflow_dispatch"]["inputs"]["dry_run"]
    assert dry_run_input["type"] == "boolean"
    assert dry_run_input["default"] is False

    job = workflow["jobs"]["dispatch"]
    assert job["permissions"]["actions"] == "write"
    assert job["permissions"]["issues"] == "write"

    steps = job["steps"]
    by_name = {step["name"]: step for step in steps if "name" in step}
    start = by_name["Start it"]
    report = by_name["Report missed audit"]
    assert steps.index(start) < steps.index(report)

    assert start["id"] == "start"
    assert start["if"] == "github.event.inputs.dry_run != 'true'"
    assert (
        'python3 scripts/audit/audit_dispatch.py "$GITHUB_REPOSITORY" '
        '"$WORKFLOW" "$NIGHT"'
    ) in start["run"]

    guard = report["if"]
    assert "failure()" in guard
    assert "steps.start.outcome == 'failure'" in guard
    assert "github.event.inputs.dry_run != 'true'" in guard
    assert report.get("continue-on-error") is not True

    run = report["run"]
    assert 'gh issue create --repo "$GITHUB_REPOSITORY"' in run
    assert "$WORKFLOW" in run and "$NIGHT" in run
    assert "$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID" in run
    assert "::error::" in run
    assert "exit 1" in run


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="the fake gh is a shebang script; the rotation runs on ubuntu-latest",
)
def test_cli_entrypoint_runs_standalone_with_crlf_gh_output(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_gh = bin_dir / "gh"
    fake_gh.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "if 'dispatches' in ' '.join(sys.argv):\n"
        "    sys.stdout.buffer.write(b'HTTP/2.0 204 No Content\\r\\n\\r\\n')\n"
        "else:\n"
        "    body = json.dumps({'workflow_runs': [{\n"
        "        'display_title': 'Claude Code Audit \\u2014 rotation night 7',\n"
        "        'created_at': '2099-01-01T00:00:00Z',\n"
        "        'html_url': 'https://github.com/amd/gaia/actions/runs/42',\n"
        "    }]}).encode('utf-8')\n"
        "    sys.stdout.buffer.write(\n"
        "        b'HTTP/2.0 200 OK\\r\\nContent-Type: application/json\\r\\n\\r\\n' + body\n"
        "    )\n",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    summary = tmp_path / "summary.md"
    done = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "audit" / "audit_dispatch.py"),
            "amd/gaia",
            "claude-nightly-audit.yml",
            "7",
        ],
        env={
            "PATH": str(bin_dir),
            "GITHUB_STEP_SUMMARY": str(summary),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert summary.read_text(encoding="utf-8").strip() == (
        "Started `claude-nightly-audit.yml` for rotation night 7: "
        "https://github.com/amd/gaia/actions/runs/42"
    )


def _fixed_now(monkeypatch: pytest.MonkeyPatch, iso: str) -> None:
    fixed = datetime.fromisoformat(iso)

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(dispatch, "datetime", _Clock)


def _runs(*runs: tuple[str, str]) -> str:
    return json.dumps(
        {
            "workflow_runs": [
                {
                    "display_title": title,
                    "created_at": created,
                    "html_url": f"https://example.test/{i}",
                }
                for i, (title, created) in enumerate(runs)
            ]
        }
    )


def test_dispatch_waits_out_an_installation_reset_near_an_hour_away(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary"))
    sleeps = _install_clock(monkeypatch, budget=dispatch.DISPATCH_BUDGET)
    monkeypatch.setattr(dispatch, "CONFIRM_BUDGET", 300)
    limited = _gh_response(
        403,
        "",
        "API rate limit exceeded for installation",
        **{"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(1_000 + 3_500)},
    )
    gh = Mock(
        side_effect=[
            limited,
            _gh_response(204),
            _gh_response(200, _runs_body(True)),
        ]
    )
    monkeypatch.setattr(dispatch.subprocess, "run", gh)

    dispatch.main("amd/gaia", "audit.yml", "1")

    assert sleeps == [3_500 + gh_retry._RESET_SLACK_SECONDS]


@pytest.mark.parametrize(
    ("created_at", "confirmed"),
    [
        ("2026-11-12T10:20:00Z", True),
        ("2026-11-12T10:19:30Z", True),
        ("2026-11-12T10:19:00Z", True),
        ("2026-11-12T10:18:59Z", False),
        ("2026-11-11T10:20:00Z", False),
    ],
    ids=[
        "same-second",
        "runner-30s-ahead",
        "runner-60s-ahead",
        "just-past-slack",
        "previous-day",
    ],
)
def test_confirmation_tolerates_runner_clock_skew_but_not_older_runs(
    monkeypatch, tmp_path, created_at, confirmed
):
    summary = tmp_path / "summary"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    _install_clock(monkeypatch, budget=12)
    _fixed_now(monkeypatch, "2026-11-12T10:20:00.900+00:00")
    listing = _gh_response(
        200,
        _runs(
            ("Audit — rotation night 11", "2026-11-12T10:20:05Z"),
            ("Audit — rotation night 1", created_at),
        ),
    )
    gh = Mock(side_effect=[_gh_response(204)] + [listing] * 10)
    monkeypatch.setattr(dispatch.subprocess, "run", gh)

    if confirmed:
        dispatch.main("amd/gaia", "audit.yml", "1")
        assert (
            summary.read_text(encoding="utf-8")
            .strip()
            .endswith("https://example.test/1")
        )
    else:
        with pytest.raises(TimeoutError):
            dispatch.main("amd/gaia", "audit.yml", "1")
        assert not summary.exists()


def test_failure_issue_does_not_claim_the_audit_failed():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["dispatch"]["steps"]
    report = next(s for s in steps if s.get("name") == "Report missed audit")
    assert "not confirmed" in report["run"]
    assert "may still be running" in report["run"]
    assert "${{" not in report["run"]
