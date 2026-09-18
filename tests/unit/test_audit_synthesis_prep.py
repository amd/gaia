# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
`prepare_synthesis.py` is the reason the nightly audit stopped filing the same
defect fifteen times — these tests pin the behaviours that make that true.

Three regressions motivated the script, and each has tests below:

* One defect spanning N files arrived as N findings with N ``dedup_key``s, so
  the synthesis model filed N issues for one fix. Clustering by ``cluster_key``
  is the fix, so a cluster that loses its members is a silent relapse.
* Dedup only searched issues already carrying the audit's own label, so a
  four-month-old human-filed issue was invisible and the same finding got
  re-discovered 14 times in one night (#1077). The backlog search now covers
  every open issue — including ones with unrelated labels.
* The doc walkthrough runs one judge per guide and those judges cannot see each
  other, so five guides quoting the same dead command invented five different
  cluster keys and produced five issues. ``find_sibling_clusters`` catches what
  the keys miss.

Three supporting properties matter as much as the three above, because all of
them fail silently:

* A cluster must report EVERY already-open issue it matches. The real backlog
  has one defect spread over five issues; naming one orphans four.
* Both scorers must weigh terms against the same stable backlog corpus. Scoring
  siblings against the run's own findings measured rarity against however many
  findings happened to arrive, so the 106-finding walkthrough of 2026-08-31
  found ZERO siblings — nine of those findings were one executor crash.
* When almost nothing collapses, the dossier has to SAY the lenses mis-keyed.
  A silent 20-findings-20-keys run reads exactly like a genuine 20-defect night.

The failure mode for all of this is silent: the script still exits 0 and still
writes a dossier, it just quietly files duplicates again. Hence assertions on
behaviour (clusters collapse, keys are read in both spellings, receipts never
match) rather than on function signatures.

The script is stdlib-only and not an installed package, so it is loaded by path.
Everything here is offline: ``--backlog-file`` exists precisely so no test ever
shells out to `gh`.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "audit" / "prepare_synthesis.py"

#: Severity markers, written as escapes so the file stays pure-ASCII and the
#: tests pass on a Windows console with a legacy code page.
HIGH = "\U0001f534"
MEDIUM = "\U0001f7e0"
LOW = "\U0001f7e1"

#: U+2014, the dash the audit workflows actually put in run-receipt titles.
EM_DASH = "—"


@pytest.fixture(scope="module")
def prep():
    """The script under test, loaded from its path (it is not importable)."""
    assert SCRIPT.is_file(), f"{SCRIPT} is missing"
    spec = importlib.util.spec_from_file_location("audit_prepare_synthesis", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_finding(**overrides: Any) -> dict[str, Any]:
    """A minimal valid finding; override whatever the test is about."""
    finding = {
        "severity": MEDIUM,
        "title": "Guides skip prerequisites",
        "why": "A reader following the guide hits a missing dependency.",
        "evidence": "No prerequisites section.",
        "cluster_key": "docs-guide-prerequisites-missing",
        "path": "docs/guides/chat.mdx",
        "symbol": "",
    }
    finding.update(overrides)
    return finding


def write_findings(directory: Path, name: str, findings: list[dict[str, Any]]) -> Path:
    """Write one lens artifact the way the workflow's lens jobs do."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps({"findings": findings}), encoding="utf-8")
    return path


# ----------------------------------------------------------------------
# Clustering: one defect is one entry, however many files it touches
# ----------------------------------------------------------------------


def test_one_defect_across_fourteen_files_collapses_to_one_cluster(prep, tmp_path):
    """The #1077 case: 14 findings, one root cause, one issue to file."""
    findings = [
        make_finding(path=f"docs/guides/guide-{i:02d}.mdx", evidence=f"guide {i}")
        for i in range(14)
    ]
    write_findings(tmp_path, "findings-docs.json", findings)

    loaded, rejects = prep.load_findings(tmp_path)
    clusters = prep.cluster_findings(loaded)

    assert rejects == []
    assert len(clusters) == 1
    assert len(clusters[0]["locations"]) == 14
    assert clusters[0]["member_count"] == 14


def test_the_same_defect_seen_by_two_lenses_merges_into_one_cluster(prep, tmp_path):
    """Lens jobs run in parallel and cannot see each other's findings."""
    write_findings(tmp_path / "docs", "findings-docs.json", [make_finding()])
    write_findings(
        tmp_path / "correctness",
        "findings-correctness.json",
        [make_finding(path="src/gaia/cli.py", evidence="argparse help omits it")],
    )

    clusters = prep.cluster_findings(prep.load_findings(tmp_path)[0])

    assert len(clusters) == 1
    assert clusters[0]["dimensions"] == ["correctness", "docs"]
    assert len(clusters[0]["locations"]) == 2


def test_a_cluster_takes_the_severity_of_its_most_severe_member(prep, tmp_path):
    """A high-severity location must not be buried under a low-severity lead."""
    write_findings(
        tmp_path,
        "findings-docs.json",
        [
            make_finding(severity=LOW, path="docs/a.mdx", title="low one"),
            make_finding(severity=HIGH, path="docs/b.mdx", title="high one"),
        ],
    )

    clusters = prep.cluster_findings(prep.load_findings(tmp_path)[0])

    assert len(clusters) == 1
    assert clusters[0]["severity"] == HIGH
    assert clusters[0]["title"] == "high one"


def test_two_findings_at_the_same_location_produce_one_location(prep, tmp_path):
    write_findings(
        tmp_path,
        "findings-docs.json",
        [
            make_finding(path="docs/guides/chat.mdx", symbol="Prerequisites"),
            make_finding(path="docs/guides/chat.mdx", symbol="Prerequisites"),
        ],
    )

    clusters = prep.cluster_findings(prep.load_findings(tmp_path)[0])

    assert clusters[0]["member_count"] == 2
    assert len(clusters[0]["locations"]) == 1


# ----------------------------------------------------------------------
# Loading: a malformed finding is quarantined, not fatal
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "field", ["severity", "title", "why", "evidence", "cluster_key"]
)
def test_a_finding_missing_a_required_field_is_quarantined_not_defaulted(
    prep, tmp_path, field
):
    """A dropped field must be loud — a guessed cluster key re-files duplicates.

    Loud, but not fatal: the lenses emit one finding per LOCATION, so a big run
    is a hundred independent chances to drop a field. Aborting the batch over one
    bad record throws away every other lens's night of work.
    """
    assert field in prep.REQUIRED_FINDING_FIELDS
    broken = make_finding()
    del broken[field]
    write_findings(tmp_path, "findings-docs.json", [make_finding(), broken])

    findings, rejects = prep.load_findings(tmp_path)

    assert len(findings) == 1
    assert findings[0]["cluster_key"] == "docs-guide-prerequisites-missing"
    assert len(rejects) == 1
    # The reject has to name the field, or nobody can fix the lens prompt.
    assert field in rejects[0]
    assert "findings-docs.json" in rejects[0]


def test_a_lens_file_without_a_findings_list_is_quarantined_not_fatal(prep, tmp_path):
    """A lens that emitted the wrong SHAPE loses only its own file."""
    good = tmp_path / "docs"
    good.mkdir()
    write_findings(good, "findings-docs.json", [make_finding()])
    bad = tmp_path / "correctness"
    bad.mkdir()
    (bad / "findings-correctness.json").write_text(
        json.dumps({"findings": "not a list"}), encoding="utf-8"
    )

    findings, rejects = prep.load_findings(tmp_path)

    assert len(findings) == 1
    assert len(rejects) == 1
    assert "no top-level 'findings' list" in rejects[0]


def test_every_finding_being_malformed_aborts_the_run(prep, tmp_path):
    """Zero survivors means the schema and the lens prompt have actually drifted.

    That is not one flaky record — it is a run with nothing left to synthesize,
    and continuing would report an all-clear audit.
    """
    broken = make_finding()
    del broken["cluster_key"]
    write_findings(tmp_path, "findings-docs.json", [broken, broken])

    with pytest.raises(SystemExit) as excinfo:
        prep.load_findings(tmp_path)

    assert "cluster_key" in str(excinfo.value)


def test_an_empty_findings_directory_aborts_the_run(prep, tmp_path):
    """Every lens writes `{"findings": []}` even when clean, so zero files means
    no lens ran at all — synthesizing that would report an all-clear audit."""
    with pytest.raises(SystemExit) as excinfo:
        prep.load_findings(tmp_path)

    assert "no lens ran" in str(excinfo.value).lower()


def test_an_unreadable_findings_file_aborts_the_run(prep, tmp_path):
    """Truncated JSON is a lost artifact, not a bad record — nothing to quarantine.

    Quarantine needs to know WHAT it dropped; an unparseable file could hold one
    finding or a hundred, so continuing would silently under-report the run.
    """
    (tmp_path / "findings-docs.json").write_text('{"findings": [', encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        prep.load_findings(tmp_path)

    assert "Cannot read findings file" in str(excinfo.value)


def test_a_lens_with_nothing_to_report_is_not_an_error(prep, tmp_path):
    write_findings(tmp_path, "findings-docs.json", [])

    assert prep.load_findings(tmp_path) == ([], [])


# ----------------------------------------------------------------------
# Key markers: both spellings, or the schema change re-files everything
# ----------------------------------------------------------------------


def test_both_key_marker_spellings_are_read(prep):
    """~130 open issues carry `audit-key`; new ones carry `audit-cluster`.

    Reading only the new spelling would make every already-filed finding look
    new on the first run after the schema change.
    """
    issues = [
        {"number": 1077, "body": "text\n<!-- audit-key: docs-prereqs -->\n"},
        {"number": 2001, "body": "text\n<!-- audit-cluster: cli-serve-flag -->\n"},
    ]

    keys = prep.extract_keys(issues)

    assert keys["docs-prereqs"] == [1077]
    assert keys["cli-serve-flag"] == [2001]


def test_key_markers_are_matched_case_and_space_insensitively(prep):
    issues = [{"number": 42, "body": "<!--audit-cluster:   Docs  Prereqs   -->"}]

    assert prep.extract_keys(issues) == {"docs prereqs": [42]}


def test_two_issues_sharing_one_key_are_both_reachable(prep):
    """Keeping only the first made the second unreachable forever.

    The backlog holds several open issues per defect from before clustering
    existed, and they frequently carry the SAME key. Mapping a key to one issue
    number silently orphaned the rest — nothing would ever point at them again.
    """
    issues = [
        {"number": 900, "body": "<!-- audit-key: lemonade-serve-dead -->"},
        {"number": 950, "body": "<!-- audit-key: lemonade-serve-dead -->"},
    ]

    assert prep.extract_keys(issues) == {"lemonade-serve-dead": [900, 950]}


def test_one_issue_repeating_a_key_is_recorded_once(prep):
    issues = [
        {
            "number": 900,
            "body": "<!-- audit-key: k --> text <!-- audit-cluster: k -->",
        }
    ]

    assert prep.extract_keys(issues) == {"k": [900]}


# ----------------------------------------------------------------------
# Classification against the audit's own filed / suppressed keys
# ----------------------------------------------------------------------


def _cluster(prep, findings: list[dict[str, Any]], tmp_path: Path):
    write_findings(tmp_path, "findings-docs.json", findings)
    return prep.cluster_findings(prep.load_findings(tmp_path)[0])


def test_a_suppressed_cluster_key_silences_the_whole_defect(prep, tmp_path):
    """A cluster-key `audit-wontfix` is a verdict on the DEFECT, not one file.

    That key only ever gets written by the post-clustering scheme, so a human
    who applied it was looking at every location at once and said no.
    """
    clusters = _cluster(
        prep,
        [
            make_finding(path="docs/a.mdx", dedup_key="docs-a-prereqs"),
            make_finding(path="docs/b.mdx", dedup_key="docs-b-prereqs"),
        ],
        tmp_path,
    )
    key = clusters[0]["cluster_key"]

    prep.classify(clusters, {}, {key: [700]})

    assert clusters[0]["status"] == "suppressed"
    assert clusters[0]["existing_issues"] == [700]
    # Nothing survives: the verdict covered every location, so none are dropped
    # individually and none are left for the reader to file.
    assert clusters[0]["suppressed_locations"] == 0


def test_a_filed_cluster_key_marks_the_whole_defect_already_filed(prep, tmp_path):
    """A cluster-key hit means this exact defect is already on the tracker."""
    clusters = _cluster(
        prep,
        [
            make_finding(path="docs/a.mdx", dedup_key="docs-a-prereqs"),
            make_finding(path="docs/b.mdx", dedup_key="docs-b-prereqs"),
        ],
        tmp_path,
    )
    key = clusters[0]["cluster_key"]

    prep.classify(clusters, {key: [1077]}, {})

    assert clusters[0]["status"] == "already-filed"
    assert clusters[0]["existing_issues"] == [1077]
    assert clusters[0]["existing_issue"] == 1077


def test_one_suppressed_location_does_not_silence_the_other_locations(prep, tmp_path):
    """A location-key `audit-wontfix` covers ONE FILE, and only that file.

    The ~130 pre-clustering issues carry one location key each, so treating any
    of them as a whole-defect verdict let a single months-old per-file wontfix
    delete a 39-location defect from the worklist entirely.
    """
    clusters = _cluster(
        prep,
        [
            make_finding(path="docs/a.mdx", dedup_key="docs-a-prereqs"),
            make_finding(path="docs/b.mdx", dedup_key="docs-b-prereqs"),
            make_finding(path="docs/c.mdx", dedup_key="docs-c-prereqs"),
        ],
        tmp_path,
    )

    prep.classify(clusters, {}, {"docs-b-prereqs": [700]})

    assert clusters[0]["status"] == "new"
    assert clusters[0]["suppressed_locations"] == 1
    assert [loc["path"] for loc in clusters[0]["locations"]] == [
        "docs/a.mdx",
        "docs/c.mdx",
    ]


def test_a_cluster_whose_every_location_is_suppressed_becomes_suppressed(
    prep, tmp_path
):
    """Nothing left to file is the one case where per-file wontfixes add up."""
    clusters = _cluster(
        prep,
        [
            make_finding(path="docs/a.mdx", dedup_key="docs-a-prereqs"),
            make_finding(path="docs/b.mdx", dedup_key="docs-b-prereqs"),
        ],
        tmp_path,
    )

    prep.classify(clusters, {}, {"docs-a-prereqs": [700], "docs-b-prereqs": [701, 702]})

    assert clusters[0]["status"] == "suppressed"
    assert clusters[0]["suppressed_locations"] == 2
    # Every issue that voted to suppress, not just the first one found.
    assert clusters[0]["existing_issues"] == [700, 701, 702]


def test_a_filed_location_key_leaves_the_cluster_new_as_a_consolidation_target(
    prep, tmp_path
):
    """One old per-file issue must not swallow 38 locations found tonight.

    A location-key hit means part of this defect is tracked under the old
    one-issue-per-file scheme. That is a COMMENT on the existing issue plus the
    remaining locations — never a reason to drop the defect, which is how 38
    newly-found locations used to vanish behind one months-old issue.
    """
    clusters = _cluster(
        prep,
        [
            make_finding(path="docs/a.mdx", dedup_key="docs-a-prereqs"),
            make_finding(path="docs/b.mdx", dedup_key="docs-b-prereqs"),
        ],
        tmp_path,
    )
    filed = {"docs-b-prereqs": [1077]}

    prep.classify(clusters, filed, {})

    assert clusters[0]["cluster_key"] not in filed
    assert clusters[0]["status"] == "new"
    assert clusters[0]["existing_issues"] == [1077]
    assert clusters[0]["existing_issue"] == 1077
    # The defect keeps BOTH locations — the filed one is the consolidation
    # target, not a location to forget.
    assert len(clusters[0]["locations"]) == 2


def test_every_matching_issue_is_recorded_not_just_the_first(prep, tmp_path):
    """One defect was filed under N keys before clustering existed.

    So a cluster routinely matches several already-open issues. Reporting only
    the first leaves the rest open as permanent orphans that nothing will ever
    point at again — the real backlog has one defect spread over five issues.
    """
    clusters = _cluster(
        prep,
        [
            make_finding(path="docs/chat.mdx", dedup_key="docs-chat-prereqs"),
            make_finding(path="docs/talk.mdx", dedup_key="docs-talk-prereqs"),
        ],
        tmp_path,
    )

    prep.classify(
        clusters, {"docs-chat-prereqs": [900], "docs-talk-prereqs": [950]}, {}
    )

    assert clusters[0]["status"] == "new"
    assert clusters[0]["existing_issues"] == [900, 950]
    assert clusters[0]["existing_issue"] == 900


def test_two_issues_under_one_key_both_reach_the_cluster(prep, tmp_path):
    """The other half of the `extract_keys` fix, end to end through `classify`.

    One key, two open issues — both must land in `existing_issues`, or the
    second stays open with nothing pointing at it.
    """
    clusters = _cluster(prep, [make_finding()], tmp_path)
    key = clusters[0]["cluster_key"]

    prep.classify(clusters, {key: [900, 950]}, {})

    assert clusters[0]["status"] == "already-filed"
    assert clusters[0]["existing_issues"] == [900, 950]
    assert clusters[0]["existing_issue"] == 900


def test_suppression_wins_over_already_filed(prep, tmp_path):
    """audit-wontfix is permanent; a stale open issue must not resurrect it."""
    clusters = _cluster(prep, [make_finding()], tmp_path)
    key = clusters[0]["cluster_key"]

    prep.classify(clusters, {key: [1077]}, {key: [900]})

    assert clusters[0]["status"] == "suppressed"
    assert clusters[0]["existing_issue"] == 900
    assert clusters[0]["existing_issues"] == [900]


def test_an_unseen_cluster_is_new(prep, tmp_path):
    clusters = _cluster(prep, [make_finding()], tmp_path)

    prep.classify(clusters, {}, {})

    assert clusters[0]["status"] == "new"
    assert clusters[0]["existing_issue"] is None
    assert clusters[0]["existing_issues"] == []


# ----------------------------------------------------------------------
# Backlog search: the whole backlog, not just our own label
# ----------------------------------------------------------------------


@pytest.fixture
def backlog_issues() -> list[dict[str, Any]]:
    """A small open backlog: one real duplicate, a receipt, and distractors.

    The duplicate deliberately carries only `documentation` — under the old
    label-scoped dedup it was invisible, which is how #1077 got re-discovered
    14 times in a single night.
    """
    return [
        {
            "number": 1077,
            "title": "Guide prerequisites missing across ~14 guides",
            "labels": [{"name": "documentation"}],
        },
        {
            "number": 3300,
            "title": f"Nightly audit {EM_DASH} normal {EM_DASH} 33620854963",
            "labels": [{"name": "weekly-audit"}],
        },
        {"number": 10, "title": "NPU inference stalls on Strix Halo", "labels": []},
        {
            "number": 11,
            "title": "Electron installer fails on Windows ARM",
            "labels": [],
        },
        {
            "number": 12,
            "title": "Telegram adapter drops media attachments",
            "labels": [],
        },
        {
            "number": 13,
            "title": "Voice pipeline latency regression in Kokoro",
            "labels": [],
        },
        {"number": 14, "title": "Scratchpad tables leak SQLite handles", "labels": []},
        {
            "number": 15,
            "title": "Blender agent removed from the registry",
            "labels": [],
        },
        {"number": 16, "title": "Bump pytest to 8.x in dev extras", "labels": []},
        {
            "number": 17,
            "title": "OAuth token refresh loops for GitHub connector",
            "labels": [],
        },
        {
            "number": 18,
            "title": "Stable Diffusion turbo model download is slow",
            "labels": [],
        },
    ]


def test_a_matching_issue_without_the_audit_label_is_surfaced(
    prep, tmp_path, backlog_issues
):
    """THE #1077 fix: the search covers every open issue, not just our label."""
    clusters = _cluster(
        prep,
        [
            make_finding(
                title="Guide prerequisites missing",
                why="Guides do not list prerequisites before the first command.",
                cluster_key="guide-prerequisites-missing",
                path="docs/guides/chat.mdx",
            )
        ],
        tmp_path,
    )

    prep.find_backlog_candidates(clusters, backlog_issues)

    numbers = [c["number"] for c in clusters[0]["backlog_candidates"]]
    assert 1077 in numbers, clusters[0]["backlog_candidates"]
    match = next(c for c in clusters[0]["backlog_candidates"] if c["number"] == 1077)
    assert match["labels"] == ["documentation"]


def test_run_receipts_are_never_offered_as_duplicates(prep, tmp_path, backlog_issues):
    """The audits file issues about themselves; those match nothing."""
    clusters = _cluster(
        prep,
        [
            make_finding(
                title="Nightly audit workflow uploads no artifact",
                why="The nightly audit job finishes without uploading findings.",
                cluster_key="nightly-audit-artifact-missing",
                path=".github/workflows/audit.yml",
            )
        ],
        tmp_path,
    )

    prep.find_backlog_candidates(clusters, backlog_issues)

    assert 3300 not in [c["number"] for c in clusters[0]["backlog_candidates"]]


@pytest.mark.parametrize(
    "title",
    [
        f"Nightly audit {EM_DASH} normal {EM_DASH} 33620854963",
        f"Weekly audit {EM_DASH} deep {EM_DASH} 33620854963",
        f"Doc walkthrough {EM_DASH} 33620854963",
    ],
)
def test_receipt_titles_are_recognised(prep, title):
    assert prep.RECEIPT_TITLE_RE.match(title), title


def test_a_real_finding_title_is_not_mistaken_for_a_receipt(prep):
    assert not prep.RECEIPT_TITLE_RE.match("Nightly audit workflow uploads no artifact")


# ----------------------------------------------------------------------
# Tokenization
# ----------------------------------------------------------------------


def test_camel_case_and_kebab_case_spellings_share_tokens(prep):
    """`LemonadeServer.serve()` and `lemonade-server serve` are the same defect."""
    assert {"lemonade", "server"} <= prep.tokenize("LemonadeServer")
    assert {"lemonade", "server"} <= prep.tokenize("lemonade-server serve")


def test_snake_case_is_split_too(prep):
    assert {"lemonade", "server"} <= prep.tokenize("lemonade_server")


def test_stopwords_carry_no_signal(prep):
    tokens = prep.tokenize("the server should not have been used")
    assert "server" in tokens
    assert tokens & {"the", "should", "not", "have", "been", "used"} == set()


# ----------------------------------------------------------------------
# Scoring weights: the threshold must mean the same thing at any backlog size
# ----------------------------------------------------------------------


def test_a_token_absent_from_the_backlog_is_weighted_as_maximally_rare(prep):
    """An unseen token is rarer than any seen one, so it cannot weigh less.

    Defaulting it to 1.0 made the score depend on backlog size: against 1000
    open issues a rare corpus token weighs ~7, so under-weighting novel query
    tokens shrank the query norm and inflated every score. The threshold then
    meant something different in CI than in a unit test.
    """
    corpus = [prep.tokenize(f"issue number {i} about widgets") for i in range(40)]

    idf, unseen = prep._idf(corpus)

    assert unseen >= max(idf.values())


def test_the_unseen_weight_grows_with_the_backlog(prep):
    """Sanity check on the direction — a bigger corpus makes rarity worth more."""
    _, small = prep._idf([prep.tokenize("alpha beta") for _ in range(5)])
    _, large = prep._idf([prep.tokenize("alpha beta") for _ in range(500)])

    assert large > small


def test_scores_do_not_drift_when_the_backlog_grows(prep, tmp_path):
    """The same cluster and the same duplicate must score alike at any scale.

    This is what the unseen-token weight buys. Distractors are unrelated, so
    they should move the score barely at all; before the fix they moved it a lot.
    """
    clusters = _cluster(
        prep,
        [
            make_finding(
                title="Guide prerequisites missing",
                why="Guides do not list prerequisites before the first command.",
                cluster_key="guide-prerequisites-missing",
                path="docs/guides/chat.mdx",
            )
        ],
        tmp_path,
    )
    duplicate = {
        "number": 1077,
        "title": "Guide prerequisites missing across ~14 guides",
        "labels": [],
    }
    distractors = [
        {
            "number": 500 + i,
            "title": f"Unrelated defect {i} in module {i}",
            "labels": [],
        }
        for i in range(200)
    ]

    def score_against(issues: list[dict[str, Any]]) -> float:
        import copy

        local = copy.deepcopy(clusters)
        prep.find_backlog_candidates(local, issues)
        return next(
            c["score"] for c in local[0]["backlog_candidates"] if c["number"] == 1077
        )

    small = score_against([duplicate] + distractors[:5])
    large = score_against([duplicate] + distractors)

    assert abs(small - large) < 0.1, (small, large)


# ----------------------------------------------------------------------
# Sibling clusters: one defect that five blind judges keyed five ways
# ----------------------------------------------------------------------


def make_cluster(
    key: str, title: str, path: str, why: str, status: str = "new"
) -> dict:
    """A cluster shaped the way `cluster_findings` emits them."""
    return {
        "cluster_key": key,
        "title": title,
        "why": why,
        "severity": MEDIUM,
        "dimensions": ["walkthrough"],
        "auto_fixable": False,
        "member_count": 1,
        "status": status,
        "locations": [
            {
                "path": path,
                "symbol": "",
                "evidence": "The guide's first command exits 127.",
                "dedup_key": key,
                "dimension": "walkthrough",
            }
        ],
    }


#: What the per-guide judges actually wrote for ONE dead command. Five guides,
#: five judges that cannot see each other, five different cluster keys — and
#: five issues in a single night. Cluster keys only catch this when independent
#: judges happen to agree on a slug; here they did not.
DEAD_COMMAND_WHY = (
    "The guide tells the reader to run lemonade-server serve, which exits 127 "
    "on a current Lemonade install."
)
DEAD_COMMAND_CLUSTERS = [
    (
        "lemonade-serve-command-missing",
        "lemonade-server serve does not exist on a current Lemonade install",
        "docs/guides/chat.mdx",
    ),
    (
        "docs-stale-lemonade-start-command",
        "The documented command to start Lemonade Server does not exist on a "
        "modern Lemonade install",
        "docs/guides/talk.mdx",
    ),
    (
        "walkthrough-lemonade-serve-127",
        "lemonade-server serve is not a command on a current Lemonade install "
        "- it exits 127",
        "docs/guides/npu.mdx",
    ),
    (
        "quickstart-lemonade-server-serve-dead",
        "Quickstart tells the reader to run lemonade-server serve, which no "
        "current Lemonade install provides",
        "docs/guides/install.mdx",
    ),
    (
        "rag-guide-lemonade-serve-invalid",
        "The Lemonade Server start command in the RAG guide does not exist on "
        "a current install",
        "docs/sdk/sdks/rag.mdx",
    ),
]


def dead_command_clusters_of(count: int) -> list[dict[str, Any]]:
    return [
        make_cluster(key, title, path, DEAD_COMMAND_WHY)
        for key, title, path in DEAD_COMMAND_CLUSTERS[:count]
    ]


@pytest.fixture
def dead_command_clusters() -> list[dict[str, Any]]:
    return dead_command_clusters_of(len(DEAD_COMMAND_CLUSTERS))


@pytest.fixture
def unrelated_clusters() -> list[dict[str, Any]]:
    return [
        make_cluster(
            "telegram-media-dropped",
            "Telegram adapter silently drops media attachments",
            "src/gaia/messaging/telegram.py",
            "Inbound photos never reach the agent at all.",
        ),
        make_cluster(
            "sd-model-download-progress",
            "Stable Diffusion turbo model download shows no progress",
            "src/gaia/sd/mixin.py",
            "The user sees a blank screen for several minutes.",
        ),
    ]


def _sibling_keys(cluster: dict[str, Any]) -> set[str]:
    return {s["cluster_key"] for s in cluster["sibling_clusters"]}


def _reachable(clusters: list[dict[str, Any]], start: str) -> set[str]:
    """Every cluster key connected to *start* through sibling links."""
    by_key = {c["cluster_key"]: c for c in clusters}
    seen, stack = {start}, [start]
    while stack:
        for key in _sibling_keys(by_key[stack.pop()]):
            if key not in seen:
                seen.add(key)
                stack.append(key)
    return seen


#: Sibling scores are measured against the BACKLOG's term weights, never
#: weights derived from the run's own findings. Twenty titles stand in for the
#: open backlog: enough that `lemonade` and `server` read as ordinary terms
#: rather than maximally rare ones.
BACKLOG_TITLES = [
    "Guide prerequisites missing across ~14 guides",
    "lemonade-server serve fails to start on Windows",
    "Lemonade Server install docs are out of date",
    "NPU inference stalls on Strix Halo",
    "Electron installer fails on Windows ARM",
    "Telegram adapter drops media attachments",
    "Voice pipeline latency regression in Kokoro",
    "Scratchpad tables leak SQLite handles",
    "Blender agent removed from the registry",
    "Bump pytest to 8.x in dev extras",
    "OAuth token refresh loops for GitHub connector",
    "Stable Diffusion turbo model download is slow",
    "RAG chunking drops the final page of a PDF",
    "Agent UI session list does not refresh",
    "MCP bridge tools time out after 30 seconds",
    "Whisper ASR mis-detects silence as speech",
    "Docker build fails on arm64 runners",
    "Email triage agent ignores the Google connector grant",
    "Hub agent install leaves a stale sidecar",
    "CLI help text omits the schedule subcommand",
]


@pytest.fixture
def weights(prep) -> tuple[dict[str, float], float]:
    """`(idf, unseen)` from a stable backlog corpus, as `main` passes them."""
    return prep._idf([prep.tokenize(title) for title in BACKLOG_TITLES])


def test_the_backlog_search_hands_its_term_weights_to_sibling_detection(
    prep, tmp_path, backlog_issues
):
    """`find_backlog_candidates` returns the weights so both passes share a scale.

    It still fills in `backlog_candidates` in place; the return value is the new
    part, and `main` feeds it straight into `find_sibling_clusters`.
    """
    clusters = _cluster(prep, [make_finding()], tmp_path)

    idf, unseen = prep.find_backlog_candidates(clusters, backlog_issues)

    assert isinstance(idf, dict) and idf
    assert unseen >= max(idf.values())
    assert "backlog_candidates" in clusters[0]


def test_five_differently_keyed_findings_for_one_defect_become_siblings(
    prep, dead_command_clusters, unrelated_clusters, weights
):
    """The doc-walkthrough fix: five keys, one dead command, one issue to file."""
    clusters = dead_command_clusters + unrelated_clusters

    prep.find_sibling_clusters(clusters, *weights)

    dead_keys = {key for key, _, _ in DEAD_COMMAND_CLUSTERS}
    for cluster in dead_command_clusters:
        # Every one of the five sees the other four — not merely a chain.
        assert _sibling_keys(cluster) == dead_keys - {cluster["cluster_key"]}
    for key in dead_keys:
        assert _reachable(clusters, key) == dead_keys


def test_sibling_scores_do_not_depend_on_how_many_findings_arrived(
    prep, dead_command_clusters, weights
):
    """The regression that forced weights out of the run and onto the backlog.

    Deriving term weights from the run's own findings measured a term's rarity
    against however many findings happened to arrive, so the same duplicate pair
    scored 0.65 in a 5-finding run and 0.35 in a 106-finding one. The real
    2026-08-31 walkthrough (106 findings) found ZERO siblings that way, nine of
    them one executor crash.
    """
    pair = dead_command_clusters[:2]
    filler = [
        make_cluster(
            f"unrelated-defect-{i:02d}",
            f"Subsystem {i} mishandles its own teardown path",
            f"src/gaia/module_{i:02d}/core.py",
            f"Component {i} leaves state behind when it exits.",
        )
        for i in range(38)
    ]

    def score_in_a_run_of(clusters: list[dict[str, Any]]) -> float:
        prep.find_sibling_clusters(clusters, *weights)
        return next(
            s["score"]
            for s in clusters[0]["sibling_clusters"]
            if s["cluster_key"] == clusters[1]["cluster_key"]
        )

    small = score_in_a_run_of(
        [make_cluster(*c, DEAD_COMMAND_WHY) for c in DEAD_COMMAND_CLUSTERS[:3]]
    )
    large = score_in_a_run_of(
        [make_cluster(*c, DEAD_COMMAND_WHY) for c in DEAD_COMMAND_CLUSTERS[:2]] + filler
    )

    assert small == pytest.approx(large), (small, large)
    assert small >= prep.SIBLING_THRESHOLD


def test_the_sibling_relation_is_symmetric(prep, dead_command_clusters, weights):
    """A appears in B's list if and only if B appears in A's. No exceptions.

    The dossier is read section by section, and a section says "where ONE fix
    closes a group, file ONE issue and skip their sections". A one-way link
    means the reader merges a group from A's section while B still has a live
    section of its own — and files the same defect twice.
    """
    clusters = dead_command_clusters + [
        make_cluster(
            "whisper-download-fails",
            "The Whisper ASR model download fails midway",
            "src/gaia/audio/whisper.py",
            "The Whisper ASR model download fails midway through.",
        )
    ]

    prep.find_sibling_clusters(clusters, *weights)

    by_key = {c["cluster_key"]: c for c in clusters}
    for left in by_key:
        for right in by_key:
            if left == right:
                continue
            assert (right in _sibling_keys(by_key[left])) == (
                left in _sibling_keys(by_key[right])
            ), (left, right)


def test_unrelated_defects_do_not_become_siblings(prep, unrelated_clusters, weights):
    """A false sibling tells the model to merge two real, separate defects."""
    prep.find_sibling_clusters(unrelated_clusters, *weights)

    for cluster in unrelated_clusters:
        assert cluster["sibling_clusters"] == []


def test_only_new_clusters_are_offered_as_siblings(
    prep, dead_command_clusters, unrelated_clusters, weights
):
    """A suppressed or already-filed cluster must never pull a new one along."""
    settled = dead_command_clusters[0]
    settled["status"] = "suppressed"
    already = dead_command_clusters[1]
    already["status"] = "already-filed"
    clusters = dead_command_clusters + unrelated_clusters

    prep.find_sibling_clusters(clusters, *weights)

    assert "sibling_clusters" not in settled
    assert "sibling_clusters" not in already
    still_new = {c["cluster_key"] for c in dead_command_clusters[2:]}
    for cluster in dead_command_clusters[2:]:
        # Non-empty, or the subset assertion below would hold vacuously.
        assert _sibling_keys(cluster)
        assert _sibling_keys(cluster) <= still_new


def test_a_large_duplicate_group_is_never_truncated(prep, weights):
    """Every member of a nine-way duplicate group sees the other eight.

    Capping each list at the top N silently broke symmetry, and it broke it
    worst exactly where it mattered most: in a group of nine, A listed five, so
    a reader merged those six and moved on while the other three kept live
    sections and got filed separately.
    """
    clusters = [
        make_cluster(
            f"lemonade-serve-dead-{i:02d}",
            f"lemonade-server serve does not exist on a current Lemonade install "
            f"(guide {i})",
            f"docs/guides/guide-{i:02d}.mdx",
            DEAD_COMMAND_WHY,
        )
        for i in range(9)
    ]
    all_keys = {c["cluster_key"] for c in clusters}

    prep.find_sibling_clusters(clusters, *weights)

    for cluster in clusters:
        assert _sibling_keys(cluster) == all_keys - {cluster["cluster_key"]}
    assert not hasattr(prep, "MAX_SIBLINGS"), "the cap was removed on purpose"


def test_siblings_are_ordered_by_descending_score(prep, weights):
    """Best match first — the reader works top-down and may stop early."""
    clusters = dead_command_clusters_of(3) + [
        make_cluster(
            "whisper-download-fails",
            "lemonade-server serve is gone and the Whisper ASR model download fails",
            "src/gaia/audio/whisper.py",
            "The Whisper ASR model download fails midway through.",
        )
    ]

    prep.find_sibling_clusters(clusters, *weights)

    for cluster in clusters:
        keyed = [(-s["score"], s["cluster_key"]) for s in cluster["sibling_clusters"]]
        assert keyed == sorted(keyed), cluster["cluster_key"]


def test_a_sibling_of_my_sibling_is_not_made_a_sibling_of_mine(prep, weights):
    """Direct pairs only — deliberately NOT a transitive closure.

    Union-ing the pairs into connected components was tried and measured: single
    linkage chained loosely-related pairs until a real 106-finding run collapsed
    into ONE component of 46 defects, which tells a reader nothing actionable.
    A links to B and B links to C, but A and C are not alike, so A must not list
    C.
    """
    bridge = [
        make_cluster(
            "a-lemonade-serve",
            "lemonade-server serve does not exist on a current Lemonade install",
            "docs/guides/chat.mdx",
            "The quickstart tells the reader to run lemonade-server serve on a "
            "current Lemonade install.",
        ),
        make_cluster(
            "b-bridge",
            "lemonade-server serve does not exist, and the Whisper ASR model "
            "download fails",
            "docs/guides/talk.mdx",
            "On a current Lemonade install the lemonade-server serve command is "
            "gone; the Whisper ASR model download fails too.",
        ),
        make_cluster(
            "c-whisper",
            "The Whisper ASR model download fails midway",
            "src/gaia/audio/whisper.py",
            "The Whisper ASR model download fails midway through.",
        ),
    ]

    prep.find_sibling_clusters(bridge, *weights)

    a, b, c = bridge
    assert _sibling_keys(b) == {"a-lemonade-serve", "c-whisper"}
    assert _sibling_keys(a) == {"b-bridge"}
    assert _sibling_keys(c) == {"b-bridge"}


def test_sibling_detection_before_classify_raises_rather_than_finding_nothing(
    prep, tmp_path
):
    """`classify` must run first, and the ordering contract enforces itself.

    Sibling detection only considers clusters whose status is `new`. Reading the
    status with `.get()` made an un-classified run find zero siblings and exit 0
    — a reordering would silently disable the whole doc-walkthrough fix.
    """
    clusters = _cluster(prep, [make_finding()], tmp_path)
    assert "status" not in clusters[0]
    idf, unseen = prep._idf([prep.tokenize(title) for title in BACKLOG_TITLES])

    with pytest.raises(KeyError):
        prep.find_sibling_clusters(clusters, idf, unseen)


# ----------------------------------------------------------------------
# The dossier's warnings
# ----------------------------------------------------------------------

#: The two warnings a multi-location cluster must carry. The first stops the
#: model copying a heading that names one file; the second stops it filing one
#: issue per location.
HEADING_WARNING = "the heading above is ONE member"
SPAN_WARNING = "this ONE defect spans"


def _render(
    prep, clusters: list[dict[str, Any]], rejects: list[str] | None = None
) -> str:
    counts = {
        "raw_findings": sum(c["member_count"] for c in clusters),
        "clusters": len(clusters),
        "new": sum(1 for c in clusters if c["status"] == "new"),
        "already_filed": sum(1 for c in clusters if c["status"] == "already-filed"),
        "suppressed": sum(1 for c in clusters if c["status"] == "suppressed"),
        "rejected_findings": len(rejects or []),
    }
    return prep.render_dossier(clusters, counts, rejects)


def test_a_multi_location_cluster_carries_both_warnings(prep, tmp_path):
    clusters = _cluster(
        prep,
        [
            make_finding(path="docs/guides/chat.mdx", title="chat.mdx has no prereqs"),
            make_finding(path="docs/guides/talk.mdx"),
        ],
        tmp_path,
    )
    prep.classify(clusters, {}, {})

    text = _render(prep, clusters)

    assert HEADING_WARNING in text
    assert f"{SPAN_WARNING} 2 locations" in text


def test_a_single_location_cluster_carries_neither_warning(prep, tmp_path):
    """Both warnings are noise for a one-file defect, and noise gets ignored."""
    clusters = _cluster(prep, [make_finding()], tmp_path)
    prep.classify(clusters, {}, {})

    text = _render(prep, clusters)

    assert HEADING_WARNING not in text
    assert SPAN_WARNING not in text


def test_the_dossier_renders_a_cluster_without_a_backlog_search(prep, tmp_path):
    """`render_dossier` must not require `find_backlog_candidates` to have run.

    They are independent passes; a reorder used to raise KeyError at the very
    end of an otherwise complete run.
    """
    clusters = _cluster(prep, [make_finding()], tmp_path)
    prep.classify(clusters, {}, {})

    text = _render(prep, clusters)

    assert "**already-open issues:** none above threshold" in text


def test_the_dossier_shows_the_sibling_block(prep, dead_command_clusters, weights):
    prep.find_sibling_clusters(dead_command_clusters, *weights)

    text = _render(prep, dead_command_clusters)

    # Four siblings each, and the header states the count.
    assert "may be the same defect as these 4 from this run:" in text
    assert "lemonade-serve-command-missing" in text


def test_the_dossier_labels_the_backlog_candidates(prep, tmp_path, backlog_issues):
    clusters = _cluster(
        prep,
        [
            make_finding(
                title="Guide prerequisites missing",
                why="Guides do not list prerequisites before the first command.",
                cluster_key="guide-prerequisites-missing",
            )
        ],
        tmp_path,
    )
    prep.classify(clusters, {}, {})
    prep.find_backlog_candidates(clusters, backlog_issues)

    text = _render(prep, clusters)

    assert "**already-open issues that may be this defect:**" in text
    assert "#1077" in text


def test_the_three_sections_are_explained_once_not_per_defect(prep, tmp_path):
    """The preamble was per-cluster boilerplate — 71KB of a 200KB worst case."""
    clusters = _cluster(
        prep,
        [
            make_finding(path=f"docs/guides/guide-{i:02d}.mdx", cluster_key=f"key-{i}")
            for i in range(5)
        ],
        tmp_path,
    )
    prep.classify(clusters, {}, {})

    text = _render(prep, clusters)

    assert text.count("One section per NEW defect below") == 1
    assert text.count("they rank, they do not decide") == 1


# ----------------------------------------------------------------------
# Truncation: the dossier is a worklist, the JSON is the archive
# ----------------------------------------------------------------------


def test_long_evidence_is_trimmed_in_the_dossier_but_kept_whole_in_the_json(
    prep, tmp_path
):
    """That split IS the contract — trimming the archive would lose the quote."""
    evidence = "E" * 500
    findings_dir = tmp_path / "findings"
    write_findings(
        findings_dir, "findings-docs.json", [make_finding(evidence=evidence)]
    )
    backlog_file = tmp_path / "backlog.json"
    backlog_file.write_text(
        json.dumps({"open_issues": [], "filed": [], "suppressed": []}), encoding="utf-8"
    )
    out = tmp_path / "worklist.json"
    dossier = tmp_path / "dossier.md"

    assert (
        prep.main(
            [
                "--findings-dir",
                str(findings_dir),
                "--repo",
                "amd/gaia",
                "--out",
                str(out),
                "--dossier",
                str(dossier),
                "--backlog-file",
                str(backlog_file),
            ]
        )
        == 0
    )

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["clusters"][0]["locations"][0]["evidence"] == evidence

    text = dossier.read_text(encoding="utf-8")
    assert evidence not in text
    # No filename baked in — the archive is whatever `--out` was called.
    assert "…[truncated; full text in the synthesis JSON]" in text
    assert "synthesis-input.json" not in text
    assert "E" * prep.MAX_EVIDENCE_CHARS in text


def test_a_short_quote_is_left_exactly_as_written(prep):
    assert prep._abbreviate("lemonade-server serve exits 127") == (
        "lemonade-server serve exits 127"
    )


def test_a_long_issue_title_is_shortened_for_the_reference_line(prep):
    """Candidate titles only identify an issue the reader will go and open."""
    assert prep._shorten("T" * 200).endswith("…")
    assert len(prep._shorten("T" * 200)) == 111
    assert prep._shorten("short title") == "short title"


# ----------------------------------------------------------------------
# The mis-keying alarm
# ----------------------------------------------------------------------

MISKEY_WARNING = "The lenses barely clustered anything"


def _distinct_key_clusters(prep, tmp_path, findings_per_key: int, keys: int):
    return _cluster(
        prep,
        [
            make_finding(
                cluster_key=f"key-{k:02d}",
                path=f"docs/guides/guide-{k:02d}-{n}.mdx",
            )
            for k in range(keys)
            for n in range(findings_per_key)
        ],
        tmp_path,
    )


def test_a_lens_that_keyed_per_location_sets_off_the_alarm(prep, tmp_path):
    """20 findings, 20 keys — the lens keyed by LOCATION, not by root cause.

    That is the original bug: one dead command became five issues. When nothing
    collapses, the sibling groups are the only dedup signal left, so the dossier
    has to say so rather than let the model file 20 issues.
    """
    clusters = _distinct_key_clusters(prep, tmp_path, findings_per_key=1, keys=20)
    prep.classify(clusters, {}, {})

    assert len(clusters) == 20
    assert MISKEY_WARNING in _render(prep, clusters)


def test_a_run_that_clustered_properly_does_not_set_off_the_alarm(prep, tmp_path):
    """20 findings collapsing to 3 defects is the system working."""
    clusters = _distinct_key_clusters(prep, tmp_path, findings_per_key=7, keys=3)
    prep.classify(clusters, {}, {})

    assert len(clusters) == 3
    assert MISKEY_WARNING not in _render(prep, clusters)


def test_a_tiny_run_does_not_set_off_the_alarm(prep, tmp_path):
    """Three unrelated findings are three defects, not evidence of mis-keying.

    Below the 10-finding floor the ratio is noise — every clean small night
    would cry wolf and the warning would stop being read.
    """
    clusters = _distinct_key_clusters(prep, tmp_path, findings_per_key=1, keys=3)
    prep.classify(clusters, {}, {})

    assert len(clusters) == 3
    assert MISKEY_WARNING not in _render(prep, clusters)


def test_the_dossier_names_every_duplicate_issue_for_a_skipped_cluster(prep, tmp_path):
    """Five open issues for one defect must all be named, or four stay orphaned."""
    clusters = _cluster(prep, [make_finding()], tmp_path)
    key = clusters[0]["cluster_key"]
    prep.classify(clusters, {key: [900, 950]}, {})

    text = _render(prep, clusters)

    assert "#900, #950" in text
    assert "several open issues track this one defect" in text


# ----------------------------------------------------------------------
# The dossier's markers: the synthesis prompt copies these verbatim
# ----------------------------------------------------------------------


def test_the_dossier_carries_both_keys_labelled_by_the_marker_they_become(
    prep, tmp_path
):
    """The prompt writes two markers, so the dossier must carry two keys.

    `audit-cluster` is the root-cause key and `audit-key` is the first location's
    — the second is what the ~130 already-open issues are matched on. Showing
    only the cluster key made the model invent an `audit-key`, and an invented
    key matches nothing on the next run.
    """
    clusters = _cluster(
        prep,
        [make_finding(path="docs/a.mdx", dedup_key="docs-a-prereqs")],
        tmp_path,
    )
    prep.classify(clusters, {}, {})

    text = _render(prep, clusters)

    assert (
        "- **cluster key:** `docs-guide-prerequisites-missing` (use as `audit-cluster`)"
        in text
    )
    assert "- **first location key:** `docs-a-prereqs` (use as `audit-key`)" in text


def test_the_dossier_tells_the_reader_to_comment_on_a_partly_tracked_defect(
    prep, tmp_path
):
    """A location-key hit is a consolidation instruction, not a skip.

    The cluster stays NEW — so it keeps a section — and that section has to say
    "comment on #1077", or the model files a duplicate of the very issue the
    location key matched.
    """
    clusters = _cluster(
        prep,
        [
            make_finding(path="docs/a.mdx", dedup_key="docs-a-prereqs"),
            make_finding(path="docs/b.mdx", dedup_key="docs-b-prereqs"),
        ],
        tmp_path,
    )
    prep.classify(clusters, {"docs-a-prereqs": [1077]}, {})

    text = _render(prep, clusters)

    assert "#1077 already track part of this defect" in text
    assert "do not open another" in text
    # It is still a NEW defect with a section of its own, not a skipped line.
    assert "Skipped (do not re-file" not in text


def test_the_dossier_says_how_many_locations_were_dropped_as_wontfix(prep, tmp_path):
    """The count is the reader's only clue that the location list is partial."""
    clusters = _cluster(
        prep,
        [
            make_finding(path="docs/a.mdx", dedup_key="docs-a-prereqs"),
            make_finding(path="docs/b.mdx", dedup_key="docs-b-prereqs"),
            make_finding(path="docs/c.mdx", dedup_key="docs-c-prereqs"),
        ],
        tmp_path,
    )
    prep.classify(clusters, {}, {"docs-b-prereqs": [700]})

    text = _render(prep, clusters)

    assert "1 location(s) omitted as `audit-wontfix`" in text
    assert "docs/b.mdx" not in text
    assert "docs/a.mdx" in text and "docs/c.mdx" in text


def test_a_clean_run_says_nothing_about_omitted_locations(prep, tmp_path):
    """Zero suppressed locations must print no line at all — noise gets skimmed."""
    clusters = _cluster(prep, [make_finding()], tmp_path)
    prep.classify(clusters, {}, {})

    assert "omitted as `audit-wontfix`" not in _render(prep, clusters)


def test_one_defect_is_called_a_defect_not_defects(prep, tmp_path):
    """The header is the first line the model reads; "1 defects" reads as a bug."""
    clusters = _cluster(prep, [make_finding()], tmp_path)
    prep.classify(clusters, {}, {})

    text = _render(prep, clusters)

    assert "collapsed to 1 distinct defect:" in text
    assert "1 defects" not in text


def test_a_long_location_list_is_truncated_with_the_elided_count(prep, tmp_path):
    """The dossier is a worklist; the JSON keeps every location.

    The cap matches what the synthesis prompt allows in the issue's location
    table, so the model is never shown more rows than it may write — but it must
    be told how many it did not see, or the issue claims a smaller blast radius
    than the defect has.
    """
    assert prep.MAX_LOCATIONS_SHOWN == 20
    clusters = _cluster(
        prep,
        [
            make_finding(path=f"docs/guides/guide-{i:02d}.mdx", evidence=f"guide {i}")
            for i in range(25)
        ],
        tmp_path,
    )
    prep.classify(clusters, {}, {})

    text = _render(prep, clusters)

    assert "- **locations (25):**" in text
    assert text.count("docs/guides/guide-") == 20
    assert "…and 5 more locations" in text
    assert "say in the issue how many were elided" in text


# ----------------------------------------------------------------------
# Quarantined findings are reported, never silently dropped
# ----------------------------------------------------------------------


def test_the_dossier_names_the_findings_it_dropped(prep, tmp_path):
    """A quarantined finding that nobody hears about is a silently lost defect.

    Quarantine is only acceptable because it is loud: the lens prompt gets fixed
    from this block and the CI error annotation, not from a diff in the count.
    """
    clusters = _cluster(prep, [make_finding()], tmp_path)
    prep.classify(clusters, {}, {})

    text = _render(prep, clusters, ["findings-docs.json[3]: missing cluster_key"])

    assert "1 finding(s) were dropped as malformed" in text
    assert "findings-docs.json[3]: missing cluster_key" in text


def test_the_dropped_findings_block_is_capped_but_states_the_true_total(prep, tmp_path):
    """Listing 200 rejects buries the worklist; hiding the count hides the drift."""
    clusters = _cluster(prep, [make_finding()], tmp_path)
    prep.classify(clusters, {}, {})
    rejects = [f"findings-docs.json[{i}]: missing why" for i in range(14)]

    text = _render(prep, clusters, rejects)

    assert "14 finding(s) were dropped as malformed" in text
    assert "…and 4 more" in text
    assert "findings-docs.json[9]: missing why" in text
    assert "findings-docs.json[10]: missing why" not in text


def test_a_run_with_no_rejects_carries_no_dropped_findings_block(prep, tmp_path):
    clusters = _cluster(prep, [make_finding()], tmp_path)
    prep.classify(clusters, {}, {})

    assert "dropped as malformed" not in _render(prep, clusters)


# ----------------------------------------------------------------------
# End to end, offline
# ----------------------------------------------------------------------


def test_main_writes_a_worklist_and_a_dossier_without_touching_gh(
    prep, tmp_path, backlog_issues, capsys
):
    findings_dir = tmp_path / "findings"
    write_findings(
        findings_dir / "docs",
        "findings-docs.json",
        [
            make_finding(
                title="Guide prerequisites missing",
                why="Guides do not list prerequisites before the first command.",
                cluster_key="guide-prerequisites-missing",
                path="docs/guides/chat.mdx",
            ),
            make_finding(
                title="Guide prerequisites missing",
                why="Guides do not list prerequisites before the first command.",
                cluster_key="guide-prerequisites-missing",
                path="docs/guides/talk.mdx",
            ),
        ],
    )
    backlog_file = tmp_path / "backlog.json"
    backlog_file.write_text(
        json.dumps({"open_issues": backlog_issues, "filed": [], "suppressed": []}),
        encoding="utf-8",
    )
    out = tmp_path / "worklist.json"
    dossier = tmp_path / "dossier.md"

    exit_code = prep.main(
        [
            "--findings-dir",
            str(findings_dir),
            "--repo",
            "amd/gaia",
            "--out",
            str(out),
            "--dossier",
            str(dossier),
            "--backlog-file",
            str(backlog_file),
        ]
    )

    assert exit_code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["counts"] == {
        "raw_findings": 2,
        "clusters": 1,
        "new": 1,
        "already_filed": 0,
        "suppressed": 0,
        "open_issues_searched": len(backlog_issues),
        "rejected_findings": 0,
    }
    assert len(payload["clusters"][0]["locations"]) == 2

    text = dossier.read_text(encoding="utf-8")
    # The synthesis model must be told to file ONE issue, not one per location.
    assert "this ONE defect spans 2 locations" in text
    assert "#1077" in text
    assert "2 raw findings -> 1 defect (" in capsys.readouterr().out


def test_main_reports_no_new_defects_rather_than_an_empty_dossier(
    prep, tmp_path, backlog_issues
):
    """A clean run must say so explicitly — a blank dossier reads as a crash."""
    findings_dir = tmp_path / "findings"
    write_findings(findings_dir, "findings-docs.json", [make_finding()])
    backlog_file = tmp_path / "backlog.json"
    backlog_file.write_text(
        json.dumps(
            {
                "open_issues": backlog_issues,
                "filed": [
                    {
                        "number": 1077,
                        "body": "<!-- audit-key: docs-guide-prerequisites-missing -->",
                    }
                ],
                "suppressed": [],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "worklist.json"
    dossier = tmp_path / "dossier.md"

    assert (
        prep.main(
            [
                "--findings-dir",
                str(findings_dir),
                "--repo",
                "amd/gaia",
                "--out",
                str(out),
                "--dossier",
                str(dossier),
                "--backlog-file",
                str(backlog_file),
            ]
        )
        == 0
    )

    text = dossier.read_text(encoding="utf-8")
    assert "No new defects this run. File nothing." in text
    assert "already-filed: `docs-guide-prerequisites-missing`" in text


def test_main_creates_the_output_directory(prep, tmp_path):
    """The workflow writes into a subdirectory that does not exist yet.

    Failing here throws away a complete run at the last line, after every lens
    and the whole backlog search have already been paid for.
    """
    findings_dir = tmp_path / "findings"
    write_findings(findings_dir, "findings-docs.json", [make_finding()])
    backlog_file = tmp_path / "backlog.json"
    backlog_file.write_text(
        json.dumps({"open_issues": [], "filed": [], "suppressed": []}),
        encoding="utf-8",
    )
    out = tmp_path / "sub" / "dir" / "worklist.json"
    dossier = tmp_path / "other" / "dossier.md"
    assert not out.parent.exists()

    exit_code = prep.main(
        [
            "--findings-dir",
            str(findings_dir),
            "--repo",
            "amd/gaia",
            "--out",
            str(out),
            "--dossier",
            str(dossier),
            "--backlog-file",
            str(backlog_file),
        ]
    )

    assert exit_code == 0
    assert json.loads(out.read_text(encoding="utf-8"))["counts"]["clusters"] == 1
    assert dossier.read_text(encoding="utf-8").startswith("# Synthesis worklist")


def test_main_quarantines_a_bad_finding_and_reports_it_three_ways(
    prep, tmp_path, capsys
):
    """One bad record costs one record — but it is counted, annotated, and printed.

    The lenses emit one finding per location, so a 106-finding night is 106
    chances to drop a field. Failing the batch threw away every other lens's
    work; failing silently would let the lens prompt drift for months.
    """
    findings_dir = tmp_path / "findings"
    broken = make_finding(path="docs/b.mdx")
    del broken["evidence"]
    write_findings(
        findings_dir, "findings-docs.json", [make_finding(path="docs/a.mdx"), broken]
    )
    backlog_file = tmp_path / "backlog.json"
    backlog_file.write_text(
        json.dumps({"open_issues": [], "filed": [], "suppressed": []}),
        encoding="utf-8",
    )
    out = tmp_path / "worklist.json"
    dossier = tmp_path / "dossier.md"

    exit_code = prep.main(
        [
            "--findings-dir",
            str(findings_dir),
            "--repo",
            "amd/gaia",
            "--out",
            str(out),
            "--dossier",
            str(dossier),
            "--backlog-file",
            str(backlog_file),
        ]
    )

    assert exit_code == 0
    counts = json.loads(out.read_text(encoding="utf-8"))["counts"]
    assert counts == {
        "raw_findings": 1,
        "clusters": 1,
        "new": 1,
        "already_filed": 0,
        "suppressed": 0,
        "open_issues_searched": 0,
        "rejected_findings": 1,
    }
    assert "1 finding(s) were dropped as malformed" in dossier.read_text(
        encoding="utf-8"
    )
    # A GitHub error annotation, so it shows on the run without opening the log.
    assert "::error title=Malformed finding::" in capsys.readouterr().err


def test_main_aborts_when_the_backlog_query_hit_its_own_limit(prep, tmp_path):
    """A truncated backlog is a partial dedup search wearing a green checkmark.

    Searching "the whole open backlog" is the #1077 fix; if `--limit` capped the
    query then the issues past the cap were never compared, and the run files
    duplicates of exactly the issues it could not see.
    """
    findings_dir = tmp_path / "findings"
    write_findings(findings_dir, "findings-docs.json", [make_finding()])
    backlog_file = tmp_path / "backlog.json"
    backlog_file.write_text(
        json.dumps(
            {
                "open_issues": [
                    {"number": n, "title": f"Some open issue {n}", "labels": []}
                    for n in (10, 11, 12)
                ],
                "filed": [],
                "suppressed": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as excinfo:
        prep.main(
            [
                "--findings-dir",
                str(findings_dir),
                "--repo",
                "amd/gaia",
                "--limit",
                "3",
                "--out",
                str(tmp_path / "worklist.json"),
                "--dossier",
                str(tmp_path / "dossier.md"),
                "--backlog-file",
                str(backlog_file),
            ]
        )

    assert "hitting the --limit of 3" in str(excinfo.value)
    assert "Raise --limit" in str(excinfo.value)
