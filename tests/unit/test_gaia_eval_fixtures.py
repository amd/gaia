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


def _fixtures_import(name: str):
    sys.path.insert(0, str(FIXTURES))
    try:
        return __import__(name)
    finally:
        sys.path.remove(str(FIXTURES))


def _serve(directory: Path):
    make_server = _fixtures_import("serve_fixtures").make_server
    server = make_server(0, directory)  # port 0: never collides, never 4001
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_address[1]}"


@pytest.fixture()
def fixture_server():
    server, thread, base = _serve(FIXTURES)
    try:
        yield base
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def prepared_hub(tmp_path):
    """A per-run signed hub (github-triage left unsigned) + its skills root."""
    prepare = _fixtures_import("prepare_fixture_hub").prepare
    skills_root = tmp_path / "skills"
    hub_dir = tmp_path / "prepared_hub"
    summaries = prepare(skills_root, hub_dir, frozenset({"github-triage"}))
    return skills_root, hub_dir, summaries


@pytest.mark.allow_network  # loopback socket only (ephemeral port, never 4001)
def test_serve_fixtures_serves_web_and_rss(fixture_server):
    with urllib.request.urlopen(f"{fixture_server}/web/price_watch.html") as response:
        assert "$1,299.00" in response.read().decode("utf-8")
    with urllib.request.urlopen(f"{fixture_server}/rss/feed.xml") as response:
        assert response.read().decode("utf-8").count("<item>") == 4


def test_source_watch_versions_are_two_distinct_urls():
    """A static server can't swap one URL's content mid-scenario."""
    v1 = (FIXTURES / "web" / "source_watch_v1.html").read_text(encoding="utf-8")
    v2 = (FIXTURES / "web" / "source_watch_v2.html").read_text(encoding="utf-8")
    assert "October 14, 2026" in v1
    assert "November 2, 2026" in v2 and "beta" in v2.lower()


@pytest.mark.allow_network  # loopback socket only (ephemeral port, never 4001)
def test_serve_fixtures_serves_the_prepared_hub_manifest_and_catalog(prepared_hub):
    _, hub_dir, _ = prepared_hub
    server, thread, base = _serve(hub_dir)
    try:
        with urllib.request.urlopen(
            f"{base}/skills/github-triage/manifest.json"
        ) as response:
            manifest = json.loads(response.read())
        assert manifest["name"] == "github-triage"
        artifact = manifest["versions"][manifest["latest_version"]]["artifact"]
        assert artifact["filename"] and artifact["sha256"]

        with urllib.request.urlopen(f"{base}/index.json") as response:
            index = json.loads(response.read())
        skill_ids = {e["id"] for e in index["agents"] if e.get("type") == "skill"}
        assert skill_ids == {"github-triage", "data-explore"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_prepared_hub_signs_all_but_the_unsigned_list(prepared_hub):
    skills_root, hub_dir, summaries = prepared_hub

    assert summaries["data-explore"]["signed"] is True
    assert summaries["github-triage"]["signed"] is False

    # The signed bundle carries SIGNATURE.json; the unsigned one must not.
    import zipfile

    de = hub_dir / "skills" / "data-explore" / "1.0.0" / "data-explore-1.0.0.zip"
    gt = hub_dir / "skills" / "github-triage" / "2.1.0" / "github-triage-2.1.0.zip"
    assert "SIGNATURE.json" in zipfile.ZipFile(de).namelist()
    assert "SIGNATURE.json" not in zipfile.ZipFile(gt).namelist()

    # The throwaway key is trusted (role publisher) in the run's skills root,
    # and its private half exists ONLY there — never in the committed tree.
    trust = json.loads((skills_root / "trusted-keys.json").read_text(encoding="utf-8"))
    assert any(k["role"] == "publisher" for k in trust["keys"])
    assert (skills_root / "keys" / "eval-test-publisher.key").is_file()
    assert not list((FIXTURES / "fixture_hub").rglob("*.key"))


def test_prepared_hub_artifact_sha256_matches_manifest(prepared_hub):
    """Drift guard: install verifies these checksums before unpacking."""
    _, hub_dir, summaries = prepared_hub
    for name, summary in summaries.items():
        manifest = json.loads(
            (hub_dir / "skills" / name / "manifest.json").read_text(encoding="utf-8")
        )
        artifact = manifest["versions"][summary["version"]]["artifact"]
        payload = (
            hub_dir / "skills" / name / summary["version"] / artifact["filename"]
        ).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == artifact["sha256"]
        assert len(payload) == artifact["size_bytes"]


@pytest.mark.allow_network  # loopback socket only (ephemeral port, never 4001)
def test_signed_data_explore_installs_cleanly_and_unsigned_github_triage_refuses(
    prepared_hub, monkeypatch
):
    """The corpus's install scenarios rest on exactly these two outcomes."""
    from gaia.skills.errors import SkillPermissionError
    from gaia.skills.install import install_skill
    from gaia.skills.manager import SkillManager

    skills_root, hub_dir, _ = prepared_hub
    server, thread, base = _serve(hub_dir)
    monkeypatch.setenv("GAIA_HUB_URL", base)
    try:
        mgr = SkillManager(user_skills_root=skills_root, include_claude_roots=False)

        result = install_skill("data-explore", manager=mgr)
        assert result.installed_tier == "community"
        assert result.signature is not None and result.signature.trusted

        with pytest.raises(SkillPermissionError) as excinfo:
            install_skill("github-triage", manager=mgr, allow_experimental=True)
        assert "shell:execute:gh" in str(excinfo.value)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


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
