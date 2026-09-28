# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Behavioural tests for the gaia-agent eval fixtures (tests/fixtures/gaia/).

The authoritative values are eval/scenarios/GAIA_FIXTURE_VALUES.md; these
tests pin the fixtures to that contract: the fake ``gh`` shim (valid JSON for
served commands, canned CONFIRM-tier comment success, loud nonzero for
everything else), the routed serve layout the scenario URLs assume, the
per-run signed hub (search → clean install → refusal), the gate manifests,
the CSV aggregates, and the planted absences the honest-miss and
hallucination probes depend on.
"""

import csv
import hashlib
import json
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gaia"
FAKE_GH = FIXTURES / "fake_gh" / "gh.py"
REPO = "acme-labs/widgetworks"


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


def test_fake_gh_issue_list_matches_the_contract_newest_first():
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
    assert [i["number"] for i in issues] == [142, 139, 137]
    assert issues[0]["title"] == "Crash on startup when config file is missing"
    assert issues[1]["title"] == "Add dark mode to the settings page"
    assert issues[2]["title"] == "Quickstart guide links to a 404"
    assert all(set(i) == {"number", "title", "labels", "createdAt"} for i in issues)


def test_fake_gh_issue_list_honours_limit_and_label_filters():
    limited = json.loads(
        _gh("issue", "list", "--repo", REPO, "--limit", "2", "--json", "number").stdout
    )
    assert [i["number"] for i in limited] == [142, 139]

    bugs = json.loads(
        _gh(
            "issue", "list", "--repo", REPO, "--label", "bug", "--json", "number"
        ).stdout
    )
    assert [i["number"] for i in bugs] == [142]


def test_fake_gh_issue_view_returns_the_recorded_issue():
    result = _gh("issue", "view", "139", "--repo", REPO, "--json", "title,body")
    assert result.returncode == 0, result.stderr
    issue = json.loads(result.stdout)
    assert issue["title"] == "Add dark mode to the settings page"
    assert "Settings page only" in issue["body"]


def test_fake_gh_issue_comment_returns_canned_success():
    """CONFIRM tier: under GAIA_AUTO_APPROVE_TOOLS=1 this write executes, and
    scenarios assert the outcome against this deterministic response."""
    result = _gh(
        "issue", "comment", "142", "--repo", REPO, "--body", "Triage: needs repro"
    )
    assert result.returncode == 0, result.stderr
    url = result.stdout.strip()
    assert url.startswith(f"https://github.com/{REPO}/issues/142#issuecomment-")
    # Deterministic: the same command yields the same URL.
    assert (
        _gh(
            "issue", "comment", "142", "--repo", REPO, "--body", "Triage: needs repro"
        ).stdout.strip()
        == url
    )


@pytest.mark.parametrize(
    "argv",
    [
        ("issue", "comment", "142", "--repo", REPO),  # no --body
        ("issue", "comment", "999", "--repo", REPO, "--body", "x"),  # unknown issue
    ],
)
def test_fake_gh_issue_comment_fails_loudly_on_bad_input(argv):
    result = _gh(*argv)
    assert result.returncode != 0
    assert result.stderr.strip()


def test_fake_gh_issue_comment_refuses_leaked_denied_flags():
    """--body-file is refused by policy BEFORE the shell; a leak must be loud."""
    result = _gh("issue", "comment", "142", "--repo", REPO, "--body-file", "notes.txt")
    assert result.returncode != 0
    assert "REFUSE" in result.stderr


def test_fake_gh_api_notifications_json_and_jq_tsv():
    raw = _gh("api", "notifications?all=false&per_page=50")
    assert raw.returncode == 0
    feed = json.loads(raw.stdout)
    assert len(feed) == 2
    assert {n["subject"]["title"] for n in feed} == {
        "Crash on startup when config file is missing",
        "Fix flaky sync test in CI",
    }
    assert all(n["repository"]["full_name"] == REPO for n in feed)

    tsv = _gh(
        "api",
        "notifications?all=false&per_page=50",
        "--jq",
        ".[]|[.reason,.repository.full_name,.subject.type,.updated_at[0:10],.subject.title]|@tsv",
    )
    assert tsv.returncode == 0
    lines = tsv.stdout.strip().splitlines()
    assert len(lines) == 2
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
        ("issue", "close", "142", "--repo", REPO),
        ("pr", "merge", "140", "--repo", REPO),
        ("api", "repos/x/y/issues", "-X", "POST"),
    ],
)
def test_fake_gh_never_fakes_refuse_tier_commands(argv):
    """These must be refused by GAIA's policy BEFORE any shell runs; reaching
    the shim means the gate leaked, and the shim says exactly that."""
    result = _gh(*argv)
    assert result.returncode != 0
    assert "REFUSE" in result.stderr or "unrecognized" in result.stderr


# ── serve helper (routed layout) + fixture hub ───────────────────────────────


def _fixtures_import(name: str):
    sys.path.insert(0, str(FIXTURES))
    try:
        return __import__(name)
    finally:
        sys.path.remove(str(FIXTURES))


def _serve(directory: Path | None):
    make_server = _fixtures_import("serve_fixtures").make_server
    server = make_server(0, directory)  # port 0: never collides, never 4001
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_address[1]}"


def _stop(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture()
def prepared_hub(tmp_path):
    """A per-run signed hub (experimental-notes left unsigned, per contract)."""
    prepare = _fixtures_import("prepare_fixture_hub").prepare
    skills_root = tmp_path / "skills"
    hub_dir = tmp_path / "prepared_hub"
    summaries = prepare(skills_root, hub_dir, frozenset({"experimental-notes"}))
    return skills_root, hub_dir, summaries


@pytest.mark.allow_network  # loopback socket only (ephemeral port, never 4001)
def test_routed_layout_matches_the_scenario_urls(prepared_hub, monkeypatch):
    """Scenario URLs are root-relative: /atlas.html, /rss/feed.xml, and
    GAIA_HUB_URL=<base>/fixture_hub (GAIA_FIXTURE_VALUES.md)."""
    serve_fixtures = _fixtures_import("serve_fixtures")
    _, hub_dir, _ = prepared_hub
    # Point the hub route at this test's prepared hub instead of _prepared.
    monkeypatch.setattr(
        serve_fixtures,
        "ROUTES",
        (
            ("/rss", FIXTURES / "rss"),
            ("/fixture_hub", hub_dir),
            ("", FIXTURES / "web"),
        ),
    )
    server, thread, base = _serve(None)
    try:
        with urllib.request.urlopen(f"{base}/atlas.html") as response:
            page = response.read().decode("utf-8")
        assert "1.9 kg" in page and "$249" in page and "2-person" in page

        with urllib.request.urlopen(f"{base}/price_nimbusbook.html") as response:
            assert "$899" in response.read().decode("utf-8")

        with urllib.request.urlopen(f"{base}/rss/feed.xml") as response:
            feed = response.read().decode("utf-8")
        assert "Widgetworks Release Notes" in feed
        assert feed.count("<item>") == 3

        with urllib.request.urlopen(f"{base}/fixture_hub/index.json") as response:
            index = json.loads(response.read())
        ids = {e["id"] for e in index["agents"] if e.get("type") == "skill"}
        assert ids == {"github-triage", "rss-digest", "experimental-notes"}

        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(f"{base}/no_such_page.html")
        assert excinfo.value.code == 404
    finally:
        _stop(server, thread)


def test_prepared_hub_signs_per_the_contract(prepared_hub):
    skills_root, hub_dir, summaries = prepared_hub

    assert set(summaries) == {"github-triage", "rss-digest", "experimental-notes"}
    assert summaries["github-triage"]["signed"] is True
    assert summaries["rss-digest"]["signed"] is True
    assert summaries["experimental-notes"]["signed"] is False

    gt = hub_dir / "skills" / "github-triage" / "2.1.0" / "github-triage-2.1.0.zip"
    rd = hub_dir / "skills" / "rss-digest" / "1.0.0" / "rss-digest-1.0.0.zip"
    en = (
        hub_dir
        / "skills"
        / "experimental-notes"
        / "0.0.1"
        / "experimental-notes-0.0.1.zip"
    )
    assert "SIGNATURE.json" in zipfile.ZipFile(gt).namelist()
    signed_rd = zipfile.ZipFile(rd).namelist()
    assert "SIGNATURE.json" in signed_rd and "tools.py" in signed_rd
    assert "SIGNATURE.json" not in zipfile.ZipFile(en).namelist()

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
def test_signed_rss_digest_installs_cleanly_and_experimental_notes_refuses(
    prepared_hub, monkeypatch
):
    """The corpus's install scenarios rest on exactly these two outcomes."""
    from gaia.skills.errors import SkillError
    from gaia.skills.install import SkillInstallError, install_skill
    from gaia.skills.manager import SkillManager

    skills_root, hub_dir, _ = prepared_hub
    server, thread, base = _serve(hub_dir)
    monkeypatch.setenv("GAIA_HUB_URL", base)
    try:
        mgr = SkillManager(user_skills_root=skills_root, include_claude_roots=False)

        # No flags, no prompts: network:read is not a dangerous grant.
        result = install_skill("rss-digest", manager=mgr)
        assert result.installed_tier == "community"
        assert result.signature is not None and result.signature.trusted

        with pytest.raises(SkillInstallError) as excinfo:
            install_skill("experimental-notes", manager=mgr)
        assert "--allow-experimental" in str(excinfo.value)
        assert isinstance(excinfo.value, SkillError)
    finally:
        _stop(server, thread)


# ── gate thresholds ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "filename, required_keys, expected_enforce",
    [
        (
            "quality_gate_thresholds.json",
            {"min_judged_pass_rate", "min_avg_score"},
            # Quality blocks, on deliberately loose absolute floors. Pinned
            # per-manifest rather than "all report-only": that blanket is what
            # let the docs and the job summary call a blocking gate advisory.
            True,
        ),
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
            # Perf stays report-only until the first runner baseline calibrates
            # it — there is no measured basis for a perf bar yet.
            False,
        ),
    ],
)
def test_gate_threshold_manifests_parse_and_pin_their_enforce_mode(
    filename, required_keys, expected_enforce
):
    manifest = json.loads((FIXTURES / filename).read_text(encoding="utf-8"))
    assert required_keys <= set(manifest)
    assert manifest["enforce"] is expected_enforce, (
        f"{filename} changed its enforce mode. Flipping it is a data change "
        "that decides whether a breach fails the build — review it on its own, "
        "and update the docs and job-summary footer with it."
    )
    for key in required_keys:
        assert isinstance(manifest[key], (int, float)), f"{key} must be numeric"


# ── CSV ground truth (the six contract aggregates) ───────────────────────────


def test_csv_matches_the_six_contract_aggregates():
    with open(FIXTURES / "csv" / "sales.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    truth = json.loads(
        (FIXTURES / "csv" / "ground_truth.json").read_text(encoding="utf-8")
    )

    assert len(rows) == truth["row_count"] == 12
    assert sum(int(r["revenue"]) for r in rows) == truth["total_revenue"] == 18600

    by_product: dict = {}
    for r in rows:
        by_product[r["product"]] = by_product.get(r["product"], 0) + int(r["revenue"])
    top = max(by_product, key=by_product.get)
    assert truth["top_product_by_revenue"] == {"product": "Gadget Pro", "revenue": 7200}
    assert (top, by_product[top]) == ("Gadget Pro", 7200)
    assert len(by_product) == truth["distinct_products"] == 3

    north = sum(int(r["revenue"]) for r in rows if r["region"] == "North")
    assert north == truth["north_region_revenue"] == 6150

    by_month: dict = {}
    for r in rows:
        month = r["date"][:7]
        by_month[month] = by_month.get(month, 0) + int(r["revenue"])
    peak = max(by_month, key=by_month.get)
    assert truth["top_month_by_revenue"] == {"month": "2026-03", "revenue": 7050}
    assert (peak, by_month[peak]) == ("2026-03", 7050)


# ── planted absences (honest-miss / hallucination probes) ────────────────────


def test_mini_repo_has_no_email_alerting_or_notification_code():
    """code_honest_miss scenarios depend on this absence (contract)."""
    for path in sorted((FIXTURES / "mini_repo").rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8").lower()
        for banned in ("email", "alert", "notif", "smtp"):
            assert banned not in text, f"{path.name} mentions {banned!r}"


def test_web_pages_hold_the_contract_absences():
    """Solarium: no executives; headlines: nothing about the stock market."""
    for page in ("solarium_a.html", "solarium_b.html"):
        text = (FIXTURES / "web" / page).read_text(encoding="utf-8").lower()
        for banned in ("cfo", "ceo", "chief", "executive"):
            assert banned not in text, f"{page} mentions {banned!r}"

    headlines = (FIXTURES / "web" / "headlines.html").read_text(encoding="utf-8")
    assert "stock" not in headlines.lower()
    assert headlines.count("<li>") == 3
