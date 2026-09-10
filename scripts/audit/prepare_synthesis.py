#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Turn raw audit findings into a deduplicated worklist for the synthesis step.

The proactive Claude audits used to hand their raw findings straight to a
synthesis prompt and ask it to dedupe by eye. It could not:

* One defect spanning N files arrived as N findings with N different
  ``dedup_key``s, so it filed N issues for one fix (``lemonade-server serve``
  became five issues in a single night).
* Dedup only looked at issues already carrying the audit's own label, so a
  four-month-old milestoned issue was invisible and got re-discovered 14 times
  in one night (#1077).

This script does the mechanical half deterministically -- cluster by root cause,
then search the WHOLE open backlog for each cluster -- and leaves the synthesis
model only the semantic call it is actually good at: "is candidate #1077 really
the same defect as this cluster?"

stdlib only; runs on a bare GitHub-hosted runner before any venv exists.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

#: Severity ranking, most severe first. Used to pick a cluster's severity and
#: to order the dossier.
SEVERITY_ORDER = ["🔴", "🟠", "🟡"]

#: Fields every finding must carry. A finding missing one is REJECTED, never
#: defaulted: filing a finding under a guessed cluster key is exactly the failure
#: this script exists to end. Rejects are reported loudly (a CI error annotation
#: plus a dossier section) rather than sinking the whole run -- see load_findings.
REQUIRED_FINDING_FIELDS = ("severity", "title", "why", "evidence", "cluster_key")

#: Titles the audits file about themselves (run receipts) are never a match for
#: a code finding. Kept as a guard for the receipts already in the backlog --
#: the workflows no longer create them.
RECEIPT_TITLE_RE = re.compile(
    r"^(nightly audit|weekly audit|doc walkthrough|security audit)\s*[-—–]",
    re.IGNORECASE,
)

#: A backlog issue must clear this cosine-ish similarity to be shown to the
#: synthesis model as a possible duplicate. Low on purpose: the model makes the
#: final call and a missed candidate is what caused #1077's 14 re-files, while a
#: wrong candidate costs one line of dossier.
MATCH_THRESHOLD = 0.18

#: How many backlog candidates to show per cluster.
MAX_CANDIDATES = 5

#: How many of a defect's locations the dossier lists. Matches the cap the
#: synthesis prompts give for the issue's location table, so the model is never
#: asked to list more rows than it was shown.
MAX_LOCATIONS_SHOWN = 20


#: Words that carry no signal for matching an issue title to a finding.
STOPWORDS = frozenset("""
    a an and are as at be because been but by can cannot could did do does doesn
    doing done for from had has have how in into is it its just like made make
    more never no not of on only or our out over own same should so some still
    such than that the their them then there these they this those to too under
    up use used uses using was way we were what when where which while who why
    will with without would you your
    add added adds also always any bug currently every fix fixed issue must need
    needs new now real really run runs same set still support supports thing
    things via
    """.split())


# ----------------------------------------------------------------------
# Loading findings
# ----------------------------------------------------------------------


def _dimension_from_filename(name: str) -> str:
    """`findings-docs.json` -> `docs`; `findings-walkthrough-cli.json` -> `walkthrough-cli`."""
    stem = re.sub(r"\.json$", "", name)
    return re.sub(r"^findings-", "", stem) or "unknown"


def load_findings(findings_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Read every ``findings-*.json`` under *findings_dir* (recursively).

    Returns ``(findings, rejects)``. Each artifact lands in its own subdirectory,
    hence the recursive glob.

    A malformed finding is QUARANTINED, not swallowed and not fatal. The lenses
    now emit one finding per location, so a big run is a hundred independent
    chances to drop a field, and failing the batch would throw away every lens's
    work over one bad record. Rejects are returned so the caller can raise them
    as CI errors and print them in the dossier — loud, but not all-or-nothing.
    Zero valid findings alongside rejects IS fatal; the caller enforces that.
    """
    files = sorted(findings_dir.rglob("findings-*.json"))
    if not files:
        raise SystemExit(
            f"No findings-*.json under {findings_dir}. Every lens writes one even when "
            f"clean, so zero files means no lens ran. Refusing to synthesize an empty "
            f"audit -- check the lens jobs' artifacts."
        )

    findings: list[dict[str, Any]] = []
    problems: list[str] = []
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SystemExit(f"Cannot read findings file {path}: {exc}") from exc
        raw = payload.get("findings")
        if raw is None or not isinstance(raw, list):
            problems.append(f"{path}: no top-level 'findings' list")
            continue
        dimension = _dimension_from_filename(path.name)
        for index, finding in enumerate(raw):
            if not isinstance(finding, dict):
                problems.append(f"{path}[{index}]: not an object")
                continue
            missing = [f for f in REQUIRED_FINDING_FIELDS if not finding.get(f)]
            if missing:
                problems.append(f"{path}[{index}]: missing {', '.join(missing)}")
                continue
            enriched = dict(finding)
            enriched.setdefault("dimension", dimension)
            enriched.setdefault("path", "")
            enriched.setdefault("symbol", "")
            enriched.setdefault("dedup_key", enriched["cluster_key"])
            enriched.setdefault("auto_fixable", False)
            findings.append(enriched)

    if problems and not findings:
        raise SystemExit(
            "Every finding was malformed — the lens prompt and this schema have "
            "drifted:\n  "
            + "\n  ".join(problems)
            + "\nEvery finding needs "
            + ", ".join(REQUIRED_FINDING_FIELDS)
            + ". Fix the lens prompt in the workflow rather than relaxing this check."
        )
    return findings, problems


# ----------------------------------------------------------------------
# Clustering by root cause
# ----------------------------------------------------------------------


def normalize_key(key: str) -> str:
    return re.sub(r"\s+", " ", str(key)).strip().lower()


def _severity_rank(severity: str) -> int:
    try:
        return SEVERITY_ORDER.index(severity)
    except ValueError:
        # An unknown severity sorts last but is never dropped -- see it in the
        # dossier and fix the lens, don't lose the finding.
        return len(SEVERITY_ORDER)


def cluster_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse findings that share a ``cluster_key`` into one defect.

    This is the fix for "one defect, N files, N issues": the lenses key a
    finding by ROOT CAUSE, so every location of the same defect merges here --
    across files, across dimensions, and across the walkthrough's per-guide jobs
    that cannot see each other.
    """
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for finding in findings:
        buckets[normalize_key(finding["cluster_key"])].append(finding)

    clusters = []
    for key, members in buckets.items():
        members.sort(key=lambda f: _severity_rank(f["severity"]))
        lead = members[0]
        locations = []
        seen_locations = set()
        for member in members:
            signature = (member["path"], member["symbol"])
            if signature in seen_locations:
                continue
            seen_locations.add(signature)
            locations.append(
                {
                    "path": member["path"],
                    "symbol": member["symbol"],
                    "evidence": member["evidence"],
                    "dedup_key": member["dedup_key"],
                    "dimension": member["dimension"],
                }
            )
        clusters.append(
            {
                "cluster_key": key,
                "severity": lead["severity"],
                "title": lead["title"],
                "why": lead["why"],
                "dimensions": sorted({m["dimension"] for m in members}),
                "auto_fixable": any(bool(m["auto_fixable"]) for m in members),
                "locations": locations,
                "member_count": len(members),
            }
        )

    clusters.sort(key=lambda c: (_severity_rank(c["severity"]), c["cluster_key"]))
    return clusters


# ----------------------------------------------------------------------
# The open backlog
# ----------------------------------------------------------------------


def _gh_json(args: list[str]) -> Any:
    """Run a `gh` command that emits JSON, failing loudly on any error."""
    try:
        completed = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", check=False
        )
    except FileNotFoundError as exc:
        raise SystemExit(
            "`gh` is not on PATH. This script needs the GitHub CLI to read the open "
            "backlog; on a GitHub-hosted runner it is preinstalled and only needs "
            "GH_TOKEN in the environment."
        ) from exc
    if completed.returncode != 0:
        raise SystemExit(
            f"`{' '.join(args[:4])} ...` failed with exit {completed.returncode}:\n"
            f"{completed.stderr.strip()}\n"
            "Refusing to dedupe against a backlog we could not read — that is how the "
            "same finding gets filed twice."
        )
    return json.loads(completed.stdout)


def fetch_backlog(repo: str, label: str, limit: int) -> dict[str, Any]:
    """Read the whole open backlog plus the audit's own filed/suppressed keys."""
    open_issues = _gh_json(
        [
            "gh",
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--limit",
            str(limit),
            "--json",
            "number,title,labels",
        ]
    )
    labelled = _gh_json(
        [
            "gh",
            "issue",
            "list",
            "--repo",
            repo,
            "--label",
            label,
            "--state",
            "open",
            "--limit",
            str(limit),
            "--json",
            "number,body",
        ]
    )
    suppressed = _gh_json(
        [
            "gh",
            "issue",
            "list",
            "--repo",
            repo,
            "--label",
            label,
            "--label",
            "audit-wontfix",
            "--state",
            "all",
            "--limit",
            str(limit),
            "--json",
            "number,body",
        ]
    )
    return {
        "open_issues": open_issues,
        "filed": labelled,
        "suppressed": suppressed,
    }


#: Both markers are read. `audit-key` is what every already-filed issue carries;
#: `audit-cluster` is the root-cause key this script introduced. Reading both is
#: what stops the schema change from re-filing the ~130 findings already open.
KEY_MARKER_RE = re.compile(r"<!--\s*audit-(?:key|cluster):\s*(.+?)\s*-->")


def extract_keys(issues: list[dict[str, Any]]) -> dict[str, list[int]]:
    """Map every audit key embedded in an issue body to the issues carrying it.

    A list, not a single number: two open issues can carry the same key (the
    backlog already holds several open issues per defect from before clustering
    existed), and keeping only the first makes the rest unreachable — they stay
    open forever with nothing ever pointing at them again.
    """
    keys: dict[str, list[int]] = defaultdict(list)
    for issue in issues:
        for match in KEY_MARKER_RE.finditer(issue.get("body") or ""):
            number = issue["number"]
            bucket = keys[normalize_key(match.group(1))]
            if number not in bucket:
                bucket.append(number)
    return dict(keys)


# ----------------------------------------------------------------------
# Matching a cluster to the open backlog
# ----------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def tokenize(text: str) -> set[str]:
    """Split text into lowercase content tokens, also splitting snake/camel case.

    `lemonade-server serve` and `LemonadeServer.serve()` must share tokens, or
    the backlog search misses the very duplicates it exists to catch.
    """
    tokens: set[str] = set()
    for raw in _TOKEN_RE.findall(text or ""):
        parts = [raw] + re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", raw)
        for part in parts:
            lowered = part.lower()
            if len(lowered) >= 3 and lowered not in STOPWORDS:
                tokens.add(lowered)
    return tokens


def _idf(corpus: list[set[str]]) -> tuple[dict[str, float], float]:
    """Inverse document frequency, plus the weight to use for an unseen token.

    An unseen token is maximally rare, so it gets the weight a once-seen token
    would. Defaulting it to 1.0 instead made the score depend on how big the
    backlog happened to be: rare corpus tokens weigh ~7 against 1000 open
    issues, so under-weighting novel query tokens shrank the query norm and
    inflated every score.
    """
    document_count = max(len(corpus), 1)
    frequency: dict[str, int] = defaultdict(int)
    for document in corpus:
        for token in document:
            frequency[token] += 1
    idf = {
        token: math.log(document_count / (1 + count)) + 1.0
        for token, count in frequency.items()
    }
    return idf, math.log(document_count) + 1.0


def cluster_query_text(cluster: dict[str, Any]) -> str:
    """Everything about a cluster that is worth matching a backlog title against."""
    parts = [cluster["title"], cluster["why"], cluster["cluster_key"]]
    for location in cluster["locations"][:8]:
        parts.append(Path(location["path"]).name if location["path"] else "")
        parts.append(location["symbol"])
    return " ".join(p for p in parts if p)


def find_backlog_candidates(
    clusters: list[dict[str, Any]],
    open_issues: list[dict[str, Any]],
    *,
    threshold: float = MATCH_THRESHOLD,
    max_candidates: int = MAX_CANDIDATES,
) -> tuple[dict[str, float], float]:
    """Attach the most similar open issues to each cluster, in place.

    This is the #1077 fix: the search covers EVERY open issue, not just the ones
    carrying the audit's label, so a human-filed issue tracking the same defect
    is visible to the synthesis step and gets a comment instead of a 15th
    duplicate.

    Returns the term weights built from the backlog so sibling detection can
    score against the same large, stable corpus.
    """
    searchable = [
        issue
        for issue in open_issues
        if not RECEIPT_TITLE_RE.match(issue.get("title") or "")
    ]
    issue_tokens = [tokenize(issue.get("title") or "") for issue in searchable]
    idf, unseen = _idf(issue_tokens)

    for cluster in clusters:
        query = tokenize(cluster_query_text(cluster))
        query_norm = math.sqrt(sum(idf.get(t, unseen) ** 2 for t in query)) or 1.0
        scored = []
        for issue, tokens in zip(searchable, issue_tokens):
            overlap = query & tokens
            if not overlap:
                continue
            issue_norm = math.sqrt(sum(idf.get(t, unseen) ** 2 for t in tokens)) or 1.0
            score = sum(idf.get(t, unseen) ** 2 for t in overlap) / (
                query_norm * issue_norm
            )
            if score >= threshold:
                scored.append((score, issue))
        scored.sort(key=lambda pair: (-pair[0], pair[1]["number"]))
        cluster["backlog_candidates"] = [
            {
                "number": issue["number"],
                "title": issue["title"],
                "labels": [lab["name"] for lab in issue.get("labels") or []],
                "score": round(score, 3),
            }
            for score, issue in scored[:max_candidates]
        ]
    return idf, unseen


#: Two clusters in the SAME run must look this alike before we ask the synthesis
#: model whether they are one defect. Higher than MATCH_THRESHOLD: these are
#: findings that already failed to agree on a cluster key, so only a strong
#: signal is worth a second look.
#:
#: Calibrated against a real 106-finding walkthrough run: the median score for
#: an overlapping pair there is 0.04, genuine duplicates score 0.30-0.45, and
#: 0.30 selects 26 pairs out of 5460 — every one of the top pairs a real
#: duplicate. Do NOT raise this without re-measuring; at 0.45 that same run
#: surfaced ZERO siblings, including nine findings that were all one crash.
SIBLING_THRESHOLD = 0.30


def find_sibling_clusters(
    clusters: list[dict[str, Any]],
    idf: dict[str, float],
    unseen: float,
    *,
    threshold: float = SIBLING_THRESHOLD,
) -> None:
    """Flag new clusters in THIS run that look like the same defect, in place.

    The doc walkthrough fans out one judge per guide and those judges cannot see
    each other, so five guides quoting the same dead command produced five
    findings with five different keys — and five issues in one night. Cluster
    keys catch that only when the independent judges happen to agree on a slug;
    this catches it when they don't.

    Scored with the BACKLOG's term weights, not weights derived from this run's
    own findings. Deriving them per-run made the threshold mean different things
    on different nights — a duplicate pair scored 0.65 in a 5-finding run and
    0.35 in a 106-finding one, because a term's rarity was measured against
    however many findings happened to come in.

    The relation is symmetric and never truncated. Two earlier shapes were both
    wrong, and both in ways that would re-file duplicates:

    * A per-cluster list capped at the top N is asymmetric — in a group of nine,
      A shows five, so a reader merges those six and moves on while the other
      three keep live sections and get filed separately.
    * Transitively closing the pairs into components over-merges badly. Single
      linkage chains A-B-C until loosely-related things join up: on a real
      106-finding run it produced ONE component of 46 defects, which tells a
      reader nothing they can act on.

    So: direct pairs only, both directions recorded, nothing dropped.
    """
    # c["status"], not .get(): if classify() has not run, that is a bug that
    # must raise, not a run that quietly finds zero siblings.
    new_clusters = [c for c in clusters if c["status"] == "new"]
    token_sets = [tokenize(cluster_query_text(c)) for c in new_clusters]
    norms = [
        math.sqrt(sum(idf.get(t, unseen) ** 2 for t in tokens)) or 1.0
        for tokens in token_sets
    ]

    for cluster in new_clusters:
        cluster["sibling_clusters"] = []

    for i in range(len(new_clusters)):
        for j in range(i + 1, len(new_clusters)):
            overlap = token_sets[i] & token_sets[j]
            if not overlap:
                continue
            score = sum(idf.get(t, unseen) ** 2 for t in overlap) / (
                norms[i] * norms[j]
            )
            if score < threshold:
                continue
            rounded = round(score, 3)
            new_clusters[i]["sibling_clusters"].append(
                {
                    "cluster_key": new_clusters[j]["cluster_key"],
                    "title": new_clusters[j]["title"],
                    "score": rounded,
                }
            )
            new_clusters[j]["sibling_clusters"].append(
                {
                    "cluster_key": new_clusters[i]["cluster_key"],
                    "title": new_clusters[i]["title"],
                    "score": rounded,
                }
            )

    for cluster in new_clusters:
        cluster["sibling_clusters"].sort(key=lambda s: (-s["score"], s["cluster_key"]))


# ----------------------------------------------------------------------
# Assembling the worklist
# ----------------------------------------------------------------------


def classify(
    clusters: list[dict[str, Any]],
    filed_keys: dict[str, list[int]],
    suppressed_keys: dict[str, list[int]],
) -> None:
    """Mark each cluster `suppressed`, `already-filed`, or `new`, in place.

    A key match is scoped to what it actually covers. Both key kinds coexist and
    they mean different things, so treating any hit as a whole-defect verdict
    loses real findings:

    * A **cluster-key** hit covers the DEFECT. Suppressed → never mention it
      again; filed → it is already tracked, don't re-file.
    * A **location-key** hit covers ONE LOCATION. Those are what the ~130
      pre-clustering issues carry, one per file. Suppressing that one location
      must not silence the 38 others, and an old single-location issue must not
      swallow a defect the run just found in 38 more places. So a
      location-key hit drops that location (suppressed) or promotes the issue to
      a comment candidate (filed) — it never discards the cluster.

    Records EVERY matching issue, not just the first: one defect was routinely
    filed under N keys, and naming only one leaves the rest open as permanent
    duplicates that nothing ever points at again.
    """
    for cluster in clusters:
        cluster_key = cluster["cluster_key"]

        # Location-scoped suppression: drop the silenced locations only.
        kept, silenced = [], []
        for location in cluster["locations"]:
            hits = suppressed_keys.get(normalize_key(location["dedup_key"]), ())
            (silenced if hits else kept).append(location)
        cluster["suppressed_locations"] = len(silenced)

        defect_suppressed = sorted(set(suppressed_keys.get(cluster_key, ())))
        if defect_suppressed or (silenced and not kept):
            cluster["status"] = "suppressed"
            cluster["existing_issues"] = defect_suppressed or sorted(
                {
                    n
                    for loc in silenced
                    for n in suppressed_keys[normalize_key(loc["dedup_key"])]
                }
            )
        else:
            cluster["locations"] = kept
            defect_filed = sorted(set(filed_keys.get(cluster_key, ())))
            location_filed = sorted(
                {
                    n
                    for loc in kept
                    for n in filed_keys.get(normalize_key(loc["dedup_key"]), ())
                }
            )
            if defect_filed:
                cluster["status"] = "already-filed"
                cluster["existing_issues"] = defect_filed
            else:
                # Location-key hits mean part of this defect is already on the
                # tracker under the old one-issue-per-file scheme. That is a
                # comment, not a skip — and it is how those issues get
                # consolidated instead of orphaned.
                cluster["status"] = "new"
                cluster["existing_issues"] = location_filed
        cluster["existing_issue"] = (
            cluster["existing_issues"][0] if cluster["existing_issues"] else None
        )


#: Evidence is a raw quote or transcript excerpt and can run to hundreds of
#: characters. The dossier is a worklist, not the archive — the JSON written
#: alongside it keeps the full text for anything the model wants to read in full.
MAX_EVIDENCE_CHARS = 240


def _abbreviate(text: str) -> str:
    flat = re.sub(r"\s+", " ", str(text)).strip()
    if len(flat) <= MAX_EVIDENCE_CHARS:
        return flat
    return flat[:MAX_EVIDENCE_CHARS] + " …[truncated; full text in the synthesis JSON]"


def _plural(count: int, noun: str) -> str:
    return noun if count == 1 else noun + "s"


def _shorten(text: str, limit: int = 110) -> str:
    """Trim a title used only to identify an issue the reader will go and open."""
    flat = re.sub(r"\s+", " ", str(text)).strip()
    return flat if len(flat) <= limit else flat[:limit] + "…"


def render_dossier(
    clusters: list[dict[str, Any]],
    counts: dict[str, int],
    rejects: list[str] | None = None,
) -> str:
    """The markdown the synthesis model reads instead of raw findings JSON."""
    lines = [
        "# Synthesis worklist",
        "",
        f"{counts['raw_findings']} raw findings collapsed to {counts['clusters']} "
        f"distinct {_plural(counts['clusters'], 'defect')}: **{counts['new']} new**, "
        f"{counts['already_filed']} already filed, {counts['suppressed']} suppressed "
        f"as wontfix.",
        "",
    ]

    # Barely any collapse means the lenses keyed per location instead of per root
    # cause — the exact failure that turned one dead command into five issues. Say
    # so loudly: the sibling lists are then the only dedup signal left.
    if (
        counts["raw_findings"] >= 10
        and counts["clusters"] > 0.9 * counts["raw_findings"]
    ):
        lines += [
            f"> ⚠ **The lenses barely clustered anything** — {counts['raw_findings']} "
            f"findings produced {counts['clusters']} keys, so they keyed by LOCATION, "
            f"not by root cause. Do not file one issue per section below. Lean hard on "
            f"the ⚠ sibling lists and the backlog candidates, and mention the "
            f"mis-keying in the run report so the lens prompt can be fixed.",
            "",
        ]

    if rejects:
        lines += [
            f"> ⚠ **{len(rejects)} finding(s) were dropped as malformed** and are NOT "
            "below — a lens omitted a required field. Say so in the run report so the "
            "lens prompt gets fixed:",
            "",
        ]
        lines += [f"> - {reject}" for reject in rejects[:10]]
        if len(rejects) > 10:
            lines.append(f"> - …and {len(rejects) - 10} more")
        lines.append("")

    new_clusters = [c for c in clusters if c["status"] == "new"]
    if not new_clusters:
        lines += ["No new defects this run. File nothing.", ""]
    else:
        # Stated once here rather than repeated under all N defects.
        lines += [
            "One section per NEW defect below. For each, in order:",
            "",
            "1. **already-open issues** — issues from the WHOLE open backlog whose "
            "titles resemble this defect, best match first. Open the top ones. If a "
            "fix for this defect would close one of them, COMMENT on it instead of "
            "filing a duplicate.",
            "2. **⚠ may be the same defect as these, from this run** — a text match, "
            "not a verdict, and NOT transitive. Read each; where ONE fix closes a "
            "set of them, file ONE issue covering it and skip their sections.",
            "3. **locations** — every place this one defect appears. They belong in "
            "ONE issue, never one issue each.",
            "",
            "Scores are cosine similarity over issue-title terms; they rank, they do "
            "not decide. Evidence is truncated here; the synthesis JSON written "
            "alongside this file has it in full.",
            "",
        ]

    for cluster in new_clusters:
        multi = len(cluster["locations"]) > 1
        lines.append(f"## {cluster['severity']} {cluster['title']}")
        lines.append("")
        if multi:
            # The heading is one member's wording and usually names that member's
            # file, which pulls directly against "title the defect, not a file".
            # Say so rather than letting the model copy it.
            lines.append(
                "- ⚠ **the heading above is ONE member's wording and probably names "
                "just its own file — write a title for the whole defect instead.**"
            )
        lines.append(
            f"- **cluster key:** `{cluster['cluster_key']}` (use as `audit-cluster`)"
        )
        lines.append(f"- **why:** {_abbreviate(cluster['why'])}")
        if cluster.get("existing_issues"):
            refs = ", ".join(f"#{n}" for n in cluster["existing_issues"])
            lines.append(
                f"- ‼ **{refs} already track part of this defect** (matched by location "
                "key, from the old one-issue-per-file scheme). COMMENT on the "
                "lowest-numbered one with the locations below and note the others as "
                "its duplicates — do not open another."
            )
        if cluster.get("suppressed_locations"):
            lines.append(
                f"- {cluster['suppressed_locations']} location(s) omitted as "
                "`audit-wontfix`; the rest still stand."
            )
        lines.append(f"- **lens/doc:** {', '.join(cluster['dimensions'])}")
        lines.append(
            f"- **auto-fixable:** {'yes' if cluster['auto_fixable'] else 'no'}"
        )
        if multi:
            lines.append(
                f"- ⚠ **this ONE defect spans {len(cluster['locations'])} locations** — "
                "file a single issue listing every location in its body."
            )
        if cluster["locations"]:
            # The prompts require an `audit-key` marker built from the first
            # location's dedup_key, so the dossier has to actually carry it.
            lines.append(
                f"- **first location key:** `{cluster['locations'][0]['dedup_key']}` "
                "(use as `audit-key`)"
            )
        lines.append(f"- **locations ({len(cluster['locations'])}):**")
        for location in cluster["locations"][:MAX_LOCATIONS_SHOWN]:
            where = " · ".join(p for p in (location["path"], location["symbol"]) if p)
            lines.append(f"  - `{where}` — {_abbreviate(location['evidence'])}")
        if len(cluster["locations"]) > MAX_LOCATIONS_SHOWN:
            elided = len(cluster["locations"]) - MAX_LOCATIONS_SHOWN
            lines.append(
                f"  - …and {elided} more locations — the full list is in the synthesis "
                "JSON; say in the issue how many were elided."
            )
        if cluster.get("backlog_candidates"):
            lines.append("- **already-open issues that may be this defect:**")
            for candidate in cluster["backlog_candidates"]:
                labels = (
                    f" [{', '.join(candidate['labels'])}]"
                    if candidate["labels"]
                    else ""
                )
                lines.append(
                    f"  - #{candidate['number']} ({candidate['score']}){labels} "
                    f"— {_shorten(candidate['title'])}"
                )
        else:
            lines.append("- **already-open issues:** none above threshold")
        siblings = cluster.get("sibling_clusters") or []
        if siblings:
            lines.append(
                f"- ⚠ **may be the same defect as these {len(siblings)} from this run:**"
            )
            for sibling in siblings:
                lines.append(
                    f"  - `{sibling['cluster_key']}` ({sibling['score']}) "
                    f"— {_shorten(sibling['title'])}"
                )
        lines.append("")

    skipped = [c for c in clusters if c["status"] != "new"]
    if skipped:
        lines.append("## Skipped (do not re-file, do not comment)")
        lines.append("")
        for cluster in skipped:
            issues = cluster.get("existing_issues") or []
            refs = ", ".join(f"#{n}" for n in issues) or "unknown"
            extra = (
                "  ← several open issues track this one defect; note it in the report"
                if len(issues) > 1
                else ""
            )
            lines.append(
                f"- {cluster['status']}: `{cluster['cluster_key']}` → {refs}{extra}"
            )
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--findings-dir", type=Path, required=True)
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--label", default="weekly-audit")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dossier", type=Path, required=True)
    parser.add_argument(
        "--backlog-file",
        type=Path,
        help=(
            "Read the backlog from this JSON file instead of calling `gh`. Used by the "
            "unit tests and by a local dry run against a saved snapshot."
        ),
    )
    args = parser.parse_args(argv)

    findings, rejects = load_findings(args.findings_dir)
    for reject in rejects:
        # A GitHub annotation, so a dropped field is visible in the run without
        # reading the log — and one bad record does not cost the whole night.
        print(f"::error title=Malformed finding::{reject}", file=sys.stderr)
    clusters = cluster_findings(findings)

    if args.backlog_file:
        backlog = json.loads(args.backlog_file.read_text(encoding="utf-8"))
    else:
        backlog = fetch_backlog(args.repo, args.label, args.limit)

    if len(backlog["open_issues"]) >= args.limit:
        raise SystemExit(
            f"The backlog query returned {len(backlog['open_issues'])} issues, hitting "
            f"the --limit of {args.limit}. Dedup would silently be searching only part "
            f"of the backlog — which is the exact failure this pass exists to end. "
            f"Raise --limit above the open-issue count."
        )

    classify(
        clusters,
        extract_keys(backlog["filed"]),
        extract_keys(backlog["suppressed"]),
    )
    idf, unseen = find_backlog_candidates(clusters, backlog["open_issues"])
    find_sibling_clusters(clusters, idf, unseen)

    counts = {
        "raw_findings": len(findings),
        "clusters": len(clusters),
        "new": sum(1 for c in clusters if c["status"] == "new"),
        "already_filed": sum(1 for c in clusters if c["status"] == "already-filed"),
        "suppressed": sum(1 for c in clusters if c["status"] == "suppressed"),
        "open_issues_searched": len(backlog["open_issues"]),
        "rejected_findings": len(rejects),
    }

    for destination in (args.out, args.dossier):
        destination.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps({"counts": counts, "clusters": clusters}, indent=2),
        encoding="utf-8",
    )
    args.dossier.write_text(render_dossier(clusters, counts, rejects), encoding="utf-8")

    print(
        f"{counts['raw_findings']} raw findings -> {counts['clusters']} "
        f"{_plural(counts['clusters'], 'defect')} ({counts['new']} new, "
        f"{counts['already_filed']} already filed, {counts['suppressed']} suppressed); "
        f"searched {counts['open_issues_searched']} open issues"
        + (f"; {counts['rejected_findings']} malformed" if rejects else "")
        + "."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
