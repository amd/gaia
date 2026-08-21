# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Behavioural tests for the gaia-agent eval fixtures (tests/fixtures/gaia/).

Three fixtures have behaviour worth pinning: the fake ``gh`` shim (valid JSON
for allowed commands, loud nonzero for everything else), the serve helper
(the fixture hub must be reachable exactly where ``gaia.skills.hub`` looks),
and the gate-threshold manifests (parse, carry ``enforce: false``). Plus two
drift guards: manifest sha256 vs the committed zip bytes, and the CSV ground
truth vs the CSV itself.
"""

import csv
import hashlib
import json
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gaia"
FAKE_GH = FIXTURES / "fake_gh" / "gh.py"
REPO = "gaia-fixtures/widget-factory"


def _gh(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(FAKE_GH), *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


# ── fake gh ──────────────────────────────────────────────────────────────────


def test_fake_gh_version_and_auth_status():
    version = _gh("--version")
    assert version.returncode == 0
    assert "gh version" in version.stdout

    status = _gh("auth", "status")
    assert status.returncode == 0
    assert "Logged in" in status.stdout


def test_fake_gh_issue_list_returns_valid_json_with_selected_fields():
    result = _gh(
        "issue",
        "list",
        "--repo",
        REPO,
        "--limit",
        "30",
        "--json",
        "number,title,labels,createdAt",
    )
    assert result.returncode == 0, result.stderr
    issues = json.loads(result.stdout)
    assert len(issues) == 8
    assert all(set(i) == {"number", "title", "labels", "createdAt"} for i in issues)
    numbers = {i["number"] for i in issues}
    assert numbers == set(range(101, 109))


def test_fake_gh_issue_list_honours_limit_and_label_filters():
    limited = json.loads(
        _gh("issue", "list", "--repo", REPO, "--limit", "3", "--json", "number").stdout
    )
    assert len(limited) == 3

    bugs = json.loads(
        _gh(
            "issue", "list", "--repo", REPO, "--label", "bug", "--json", "number"
        ).stdout
    )
    assert {i["number"] for i in bugs} == {101, 102, 103, 106, 107}


def test_fake_gh_issue_view_returns_the_recorded_issue():
    result = _gh(
        "issue", "view", "106", "--repo", REPO, "--json", "title,body,comments"
    )
    assert result.returncode == 0, result.stderr
    issue = json.loads(result.stdout)
    assert "network share" in issue["title"]
    assert len(issue["comments"]) == 2


def test_fake_gh_api_notifications_json_and_jq_tsv():
    raw = _gh("api", "notifications?all=false&per_page=50")
    assert raw.returncode == 0
    feed = json.loads(raw.stdout)
    assert len(feed) == 5
    assert {n["reason"] for n in feed} >= {"review_requested", "assign", "mention"}

    tsv = _gh(
        "api",
        "notifications?all=false&per_page=50",
        "--jq",
        ".[]|[.reason,.repository.full_name,.subject.type,.updated_at[0:10],.subject.title]|@tsv",
    )
    assert tsv.returncode == 0
    lines = tsv.stdout.strip().splitlines()
    assert len(lines) == 5
    assert all(len(line.split("\t")) == 5 for line in lines)


@pytest.mark.parametrize(
    "argv",
    [
        (),  # bare gh
        ("issue", "list", "--repo", "someone-else/repo", "--json", "number"),
        ("issue", "view", "999", "--repo", REPO),
        ("pr", "list", "--repo", REPO),  # not recorded
        ("api", "repos/amd/gaia"),  # unrecorded api path
        ("issue", "list", "--repo", REPO),  # missing --json
    ],
)
def test_fake_gh_fails_loudly_on_unrecorded_commands(argv):
    result = _gh(*argv)
    assert result.returncode != 0
    assert result.stderr.strip(), "an unrecorded command must explain itself"
    assert result.stdout.strip() == "", "never emit data alongside a failure"


@pytest.mark.parametrize(
    "argv",
    [
        ("auth", "token"),
        ("alias", "set", "x", "y"),
        ("extension", "install", "foo"),
        ("issue", "close", "101", "--repo", REPO),
        ("pr", "merge", "1", "--repo", REPO),
        ("api", "repos/x/y/issues", "-X", "POST"),
    ],
)
def test_fake_gh_never_fakes_refuse_tier_commands(argv):
    """These must be refused by GAIA's policy BEFORE any shell runs; reaching
    the shim means the gate leaked, and the shim says exactly that."""
    result = _gh(*argv)
    assert result.returncode != 0
    combined = result.stderr
    assert "REFUSE" in combined or "unrecognized" in combined


# ── serve helper + fixture hub ───────────────────────────────────────────────


@pytest.fixture()
def fixture_server():
    sys.path.insert(0, str(FIXTURES))
    try:
        from serve_fixtures import make_server
    finally:
        sys.path.remove(str(FIXTURES))

    server = make_server(0, FIXTURES)  # port 0: never collides, never 4001
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.allow_network  # loopback socket only (ephemeral port, never 4001)
def test_serve_fixtures_serves_the_hub_manifest_and_catalog(fixture_server):
    with urllib.request.urlopen(
        f"{fixture_server}/fixture_hub/skills/github-triage/manifest.json"
    ) as response:
        manifest = json.loads(response.read())
    assert manifest["name"] == "github-triage"
    version = manifest["latest_version"]
    artifact = manifest["versions"][version]["artifact"]
    assert artifact["filename"] and artifact["sha256"]

    with urllib.request.urlopen(f"{fixture_server}/fixture_hub/index.json") as response:
        index = json.loads(response.read())
    skill_ids = {e["id"] for e in index["agents"] if e.get("type") == "skill"}
    assert skill_ids == {"github-triage", "data-explore"}


@pytest.mark.allow_network  # loopback socket only (ephemeral port, never 4001)
def test_serve_fixtures_serves_web_and_rss(fixture_server):
    with urllib.request.urlopen(f"{fixture_server}/web/price_watch.html") as response:
        assert "$1,299.00" in response.read().decode("utf-8")
    with urllib.request.urlopen(f"{fixture_server}/rss/feed.xml") as response:
        assert response.read().decode("utf-8").count("<item>") == 4


def test_fixture_hub_artifact_sha256_matches_manifest():
    """Drift guard: a hand-edited zip or manifest breaks install checksums."""
    for name in ("github-triage", "data-explore"):
        manifest = json.loads(
            (FIXTURES / "fixture_hub" / "skills" / name / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
        version = manifest["latest_version"]
        artifact = manifest["versions"][version]["artifact"]
        payload = (
            FIXTURES / "fixture_hub" / "skills" / name / version / artifact["filename"]
        ).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == artifact["sha256"], (
            f"{name}: committed zip does not match its manifest sha256 — "
            "regenerate with fixture_hub/_build_fixture_hub.py"
        )
        assert len(payload) == artifact["size_bytes"]


# ── gate thresholds ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "filename, required_keys",
    [
        ("quality_gate_thresholds.json", {"min_judged_pass_rate", "min_avg_score"}),
        (
            "perf_gate_thresholds.json",
            {
                "max_input_tokens_per_scenario",
                "max_output_tokens_per_scenario",
                "min_cache_hit_ratio",
                "max_elapsed_s",
                "max_llm_calls_per_turn",
                "max_tool_calls_per_turn",
            },
        ),
    ],
)
def test_gate_threshold_manifests_parse_and_ship_report_mode(filename, required_keys):
    manifest = json.loads((FIXTURES / filename).read_text(encoding="utf-8"))
    assert required_keys <= set(manifest)
    assert manifest["enforce"] is False, (
        f"{filename} must ship enforce:false until the first runner baseline "
        "is committed (flipping it is a data change, reviewed on its own)"
    )
    for key in required_keys:
        assert isinstance(manifest[key], (int, float)), f"{key} must be numeric"


# ── CSV ground truth ─────────────────────────────────────────────────────────


def test_csv_ground_truth_matches_the_csv():
    """Drift guard: the aggregates scenarios judge against must be the CSV's."""
    with open(FIXTURES / "csv" / "sales.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    truth = json.loads(
        (FIXTURES / "csv" / "ground_truth.json").read_text(encoding="utf-8")
    )

    assert len(rows) == truth["row_count"]
    assert sum(int(r["units"]) for r in rows) == truth["total_units"]
    total_revenue = round(sum(float(r["revenue"]) for r in rows), 2)
    assert total_revenue == truth["total_revenue"]
    for row in rows:
        assert round(int(row["units"]) * float(row["unit_price"]), 2) == float(
            row["revenue"]
        )
