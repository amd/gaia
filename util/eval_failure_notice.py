#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Decide what a release eval run proved, and write the issue body if it regressed.

A release eval used to file an ``eval-failure`` issue whenever its job was not
green, with a template body that named nothing. Most of those runs measured
nothing at all (skipped lanes, no Claude quota, a final tag with no full
sweep), so the issues piled up and none said what broke.

This reads the run's scorecards, diffs each category against its committed
baseline with ``gaia.eval.runner.compare_scorecards`` (the same diff
``gaia eval agent --compare`` prints), and returns one of three verdicts:

* ``regressed`` -- a scenario in the stable smoke set (eval/ci_tiers.json)
  that passed in the baseline now fails, or the quality bar in the run's
  ``quality_gate_report.json`` was breached. Writes ``title.txt`` and
  ``body.md`` for the tracking issue. A PASS -> FAIL outside the stable set is
  listed as "watch" and never filed: the baseline is one run, and about 18% of
  scenarios flip between two runs of the same code (#5493).
* ``passed`` -- every required job succeeded, every expected category was
  compared, every scenario was measured, and nothing regressed. Open issues
  for the target may be closed.
* ``no-verdict`` -- the run did not measure enough to say either. Nothing is
  filed and nothing is closed; ``summary.md`` says what was not measured.

Usage (flagship, from eval_flagship.yml)::

    python util/eval_failure_notice.py --target flagship --tier smoke \\
        --matrix-json "$MATRIX" --artifacts-dir lane-artifacts \\
        --baseline-dir "$BASELINE_DIR" --job lanes=success --job scenarios=success \\
        --ref v0.26.0-rc1 --run-url "$RUN_URL" --out-dir notice

Exit 0 whatever the verdict; exit 1 only when the inputs are unusable.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: Which jobs a run must finish green before its clean comparison may close
#: an issue. Outcomes run only on a full sweep; a final tag measures nothing
#: itself and only checks that a full sweep exists.
REQUIRED_JOBS = {
    "smoke": ("lanes", "scenarios"),
    "full": ("lanes", "scenarios", "outcomes"),
    "verify": ("verify",),
    "release": ("scenarios",),
}

JOB_LABELS = {
    "lanes": "lane matrix",
    "scenarios": "scenario eval",
    "outcomes": "outcome tasks (`gaia eval tasks`)",
    "verify": "final-tag full-sweep check",
}

TARGET_LABELS = {
    "flagship": "Flagship eval",
    "agent-gaia": "Agent release eval",
}

#: Statuses that mean the harness produced no score: gaia.eval.runner's
#: _NO_MEASUREMENT_STATUSES plus the skipped ones, as the integrity gate counts.
NO_SCORE_STATUSES = {
    "INFRA_ERROR",
    "SETUP_ERROR",
    "TIMEOUT",
    "BUDGET_EXCEEDED",
    "ERRORED",
    "NOT_RUN",
    "SKIPPED_NO_DOCUMENT",
    "SKIPPED_NO_MODEL",
}

#: Rows in the visible table; the rest are counted, never dropped silently.
MAX_ROWS = 40
ROOT_CAUSE_CHARS = 220
#: Scenarios that passed at least 90% of 10+ runs (eval/ci_tiers.json, smoke).
#: A flip there is a signal; anywhere else a lone flip is often noise (#5493).
DEFAULT_STABLE_FILE = Path(__file__).resolve().parents[1] / "eval" / "ci_tiers.json"
SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")


@dataclass
class Regression:
    scenario_id: str
    category: str
    baseline_status: str | None
    baseline_score: float | None
    current_status: str
    current_score: float | None
    root_cause: str
    fix: str


@dataclass
class Notice:
    target: str
    ref: str
    run_url: str
    verdict: str = "no-verdict"
    regressions: list[Regression] = field(default_factory=list)
    bar_breaches: list[str] = field(default_factory=list)
    improved: int = 0
    score_drops: list[str] = field(default_factory=list)
    compared_categories: list[str] = field(default_factory=list)
    compared_scenarios: int = 0
    not_measured: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    baseline_dir: str = ""
    tier: str = ""
    #: PASS -> FAIL outside the stable set: reported, never filed.
    watch: list[Regression] = field(default_factory=list)
    stable: frozenset = frozenset()


# --- untrusted text -----------------------------------------------------------


def clean(text, limit: int = ROOT_CAUSE_CHARS) -> str:
    """One markdown-table-safe line from judge or scenario text.

    Judge output is model-written and lands in a public issue, so it must not
    break the table, open HTML (or an auto-issue marker), or @-mention anyone.
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False)
    text = " ".join(text.split())
    if text.lower() in ("null", "none"):
        return ""
    # First sentence when it is a reasonable length; else a hard cut.
    match = re.match(r"(.{40,}?[.!?])(\s|$)", text)
    if match and len(match.group(1)) <= limit:
        text = match.group(1)
    elif len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = text.replace("|", "\\|").replace("`", "'")
    # Scenarios plant hostile links: no clickable link, image, URL, issue
    # reference or mention may survive from judge text.
    text = text.replace("://", ":\u200b//").replace("www.", "www\u200b.")
    text = re.sub(r"(?i)\b(gh)-(\d)", "\\1-\u200b\\2", text)
    text = text.replace("#", "#\u200b").replace("@", "@\u200b")
    # Last: these entities carry a `#` the line above would break.
    return text.replace("[", "&#91;").replace("]", "&#93;")


def code_id(value: str) -> str:
    """A scenario or category id as inline code, or escaped text if it is odd."""
    if SAFE_ID.match(value or ""):
        return f"`{value}`"
    return clean(value, 120) or "(unnamed)"


def id_list(ids, shown: int = 10) -> str:
    more = f" and {len(ids) - shown} more" if len(ids) > shown else ""
    return ", ".join(code_id(i) for i in ids[:shown]) + more


def fmt_score(status, score) -> str:
    if status is None:
        return "—"
    if isinstance(score, (int, float)) and not isinstance(score, bool):
        return f"{clean(str(status), 30)} {score:.1f}"
    return clean(str(status), 30)


def fix_line(fix) -> str:
    if isinstance(fix, dict):
        where = " ".join(
            str(fix.get(k)) for k in ("target", "file") if fix.get(k) not in (None, "")
        )
        what = clean(fix.get("description"), 200)
        return f"{clean(where, 120)}: {what}" if where else what
    return clean(fix, 200)


# --- reading the run ----------------------------------------------------------


def read_json(path: Path):
    # utf-8-sig: lane files are written on Windows and may carry a BOM.
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        raise ValueError(f"{path} is not readable JSON: {e}") from e


def expected_from_matrix(matrix_json: str, artifacts_dir: Path, prefix: str):
    """``[(category, scorecard_path, artifact_name)]`` for a lane matrix."""
    try:
        include = json.loads(matrix_json)["include"]
    except (ValueError, KeyError, TypeError) as e:
        raise ValueError(f"--matrix-json is not the lanes job's matrix ({e})") from e
    rows = []
    for entry in include:
        lane = entry["lane"]
        artifact = f"{prefix}{lane}"
        root = artifacts_dir / artifact
        if not root.is_dir():
            # download-artifact unpacks a lone artifact with no per-name folder.
            flat = read_json(artifacts_dir / "lane-status.json") or {}
            if flat.get("lane") == lane:
                root = artifacts_dir
        for category in str(entry.get("categories", "")).split():
            rows.append((category, root / category / "scorecard.json", artifact))
    return rows


def expected_from_categories(categories: str, artifacts_dir: Path, artifact: str):
    return [
        (c.strip(), artifacts_dir / c.strip() / "scorecard.json", artifact)
        for c in categories.split(",")
        if c.strip()
    ]


def scenario_rows(card: dict) -> dict:
    return {
        s.get("scenario_id"): s
        for s in card.get("scenarios") or []
        if s.get("scenario_id") is not None
    }


def has_baseline(baseline_dir: Path | None) -> bool:
    return bool(
        baseline_dir
        and baseline_dir.is_dir()
        and any(baseline_dir.glob("scorecard_*.json"))
    )


def compare(baseline: Path, current: Path) -> dict:
    """``compare_scorecards`` with its printed diff sent to stderr (the job log)."""
    from gaia.eval.runner import compare_scorecards

    with contextlib.redirect_stdout(sys.stderr):
        return compare_scorecards(baseline, current)


def load_stable(path: Path) -> frozenset:
    """The smoke tier's scenario ids: the set a lone PASS -> FAIL is filed for."""
    doc = read_json(path)
    if doc is None:
        raise ValueError(
            f"stable-scenario file {path} not found; it decides which flips are "
            "filed. Pass --stable-scenarios eval/ci_tiers.json from the checkout"
        )
    machines = ((doc.get("tiers") or {}).get("smoke") or {}).get("machines") or []
    ids = frozenset(e["id"] for m in machines for e in m.get("scenarios") or [])
    if not ids:
        raise ValueError(f"{path} lists no smoke-tier scenarios")
    return ids


def build(args) -> Notice:
    from gaia.eval.runner import ScorecardMismatchError

    notice = Notice(
        target=args.target,
        ref=args.ref,
        run_url=args.run_url,
        baseline_dir=str(args.baseline_dir or ""),
        tier=args.tier,
        stable=load_stable(args.stable_scenarios),
    )
    jobs = dict(args.job or [])

    if args.tier not in REQUIRED_JOBS:
        raise ValueError(
            f"no rule for tier '{args.tier}'. Add it to REQUIRED_JOBS in "
            "util/eval_failure_notice.py when util/eval_tiers.py gains a tier."
        )
    if args.tier == "verify":
        if jobs.get("verify") == "failure":
            notice.not_measured.append(
                "No full sweep ran on this commit or any of its RCs, so this release "
                "was never measured. Dispatch the workflow on the tag with tier=full."
            )
        else:
            notice.not_measured.append(
                "A final tag re-measures nothing; it only checks that a full sweep "
                f"ran on this commit or one of its RCs (that check: "
                f"`{clean(jobs.get('verify', 'missing'), 30)}`)."
            )
        return notice

    for key in REQUIRED_JOBS[args.tier]:
        result = jobs.get(key, "missing")
        if result == "success" or (key == "outcomes" and result == "failure"):
            continue
        label = JOB_LABELS.get(key, key)
        if result == "skipped":
            notice.not_measured.append(f"The {label} job was skipped.")
        elif result == "cancelled":
            notice.not_measured.append(
                f"The {label} job was cancelled (a manual cancel, or a machine that "
                "waited out GitHub's 24 h queue limit)."
            )
        else:
            notice.not_measured.append(
                f"The {label} job ended `{clean(result, 30)}`. Its job log names the "
                "step; any category it did measure is still compared below."
            )

    if jobs.get("outcomes") == "failure":
        # `gaia eval tasks gate` exits 1 for a missed expectation and for an
        # unreachable backend alike, so this cannot tell a drop from an outage.
        suites = clean(args.outcomes_failed or "unknown suites", 200)
        notice.not_measured.append(
            f"The outcome-task gate failed for: {suites}. That gate does not separate "
            "a missed expectation from an unreachable backend, so it is not "
            "adjudicated here; read the outcomes job."
        )

    if args.matrix_json is not None:
        expected = expected_from_matrix(
            args.matrix_json, args.artifacts_dir, args.artifact_prefix
        )
    else:
        expected = expected_from_categories(
            args.categories or "", args.artifacts_dir, args.artifact_name
        )

    baseline_dir = args.baseline_dir
    baseline_ok = has_baseline(baseline_dir)
    if expected and not baseline_ok:
        notice.not_measured.append(
            f"No baseline is committed at `{baseline_dir}`, so nothing was compared. "
            "The scorecards ran and are uploaded; a clean run here is not a pass."
        )

    for category, scorecard, artifact in expected:
        card = read_json(scorecard)
        if card is None:
            notice.not_measured.append(
                f"{code_id(category)} produced no scorecard (artifact `{artifact}`)."
            )
            continue
        if artifact not in notice.artifacts:
            notice.artifacts.append(artifact)
        # Older scorecards record a mid-run stop only under `stopped`.
        stop = card.get("partial") or card.get("stopped") or {}
        if stop:
            why = stop.get("summary") or stop.get("headline") or stop.get("kind")
            notice.not_measured.append(
                f"{code_id(category)} stopped part-way "
                f"({clean(why or 'no reason recorded', 200)})."
            )
        no_score = [
            sid
            for sid, s in scenario_rows(card).items()
            if str(s.get("status", "")).upper() in NO_SCORE_STATUSES
        ]
        if no_score:
            notice.not_measured.append(
                f"{code_id(category)}: {len(no_score)} scenario(s) produced no score "
                f"({id_list(no_score)})."
            )
        if not baseline_ok:
            continue
        baseline = baseline_dir / f"scorecard_{category}.json"
        if not baseline.is_file():
            notice.not_measured.append(
                f"{code_id(category)} has no baseline at `{baseline}`, so it was not "
                "compared."
            )
            continue
        try:
            diff = compare(baseline, scorecard)
        except ScorecardMismatchError as e:
            notice.not_measured.append(
                f"{code_id(category)} was not compared: {clean(str(e), 400)}"
            )
            continue
        notice.compared_categories.append(category)
        add_diff(notice, category, diff, read_json(baseline) or {}, card)

    if args.quality_report:
        report = read_json(args.quality_report)
        if report is None:
            notice.not_measured.append(
                "The quality-gate report was not produced, so the quality bar was "
                "not checked."
            )
        else:
            gate = report.get("quality_gate") or {}
            notice.bar_breaches = [clean(b, 200) for b in gate.get("breaches") or []]
            if notice.bar_breaches and not notice.regressions:
                add_failing(notice, expected)

    notice.verdict = decide(notice, expected)
    return notice


def add_diff(notice: Notice, category: str, diff: dict, base: dict, card: dict):
    base_rows = scenario_rows(base)
    rows = scenario_rows(card)
    for e in diff.get("regressed") or []:
        current = rows.get(e["scenario_id"], {})
        stable = e["scenario_id"] in notice.stable
        (notice.regressions if stable else notice.watch).append(
            Regression(
                scenario_id=e["scenario_id"],
                category=category,
                baseline_status=e.get("baseline_status"),
                baseline_score=e.get("baseline_score"),
                current_status=e.get("current_status") or "?",
                current_score=e.get("current_score"),
                root_cause=clean(current.get("root_cause")),
                fix=fix_line(current.get("recommended_fix")),
            )
        )
    notice.improved += len(diff.get("improved") or [])
    for e in diff.get("score_regressed") or []:
        notice.score_drops.append(
            f"{code_id(e['scenario_id'])} {e['baseline_score']:.1f} → "
            f"{e['current_score']:.1f}"
        )
    notice.compared_scenarios += sum(
        len(diff.get(k) or [])
        for k in ("improved", "regressed", "score_regressed", "unchanged")
    )
    # A targeted run files unselected baseline scenarios under not_run, so
    # anything left in only_in_baseline was expected and never reported.
    missing = [sid for sid in diff.get("only_in_baseline") or [] if sid in base_rows]
    if missing:
        notice.not_measured.append(
            f"{code_id(category)}: {len(missing)} baseline scenario(s) are missing "
            f"from this run ({id_list(missing)})."
        )


def add_failing(notice: Notice, expected):
    """With a breached bar and no baseline diff, list what failed in this run."""
    for category, scorecard, _artifact in expected:
        card = read_json(scorecard) or {}
        for sid, s in scenario_rows(card).items():
            if s.get("status") in ("FAIL", "BLOCKED"):
                notice.regressions.append(
                    Regression(
                        scenario_id=sid,
                        category=category,
                        baseline_status=None,
                        baseline_score=None,
                        current_status=s.get("status"),
                        current_score=s.get("overall_score"),
                        root_cause=clean(s.get("root_cause")),
                        fix=fix_line(s.get("recommended_fix")),
                    )
                )


def decide(notice: Notice, expected) -> str:
    if notice.regressions or notice.bar_breaches:
        return "regressed"
    if notice.not_measured or not expected or not notice.compared_categories:
        return "no-verdict"
    return "passed"


# --- rendering ----------------------------------------------------------------


def title(notice: Notice) -> str:
    label = TARGET_LABELS.get(notice.target, notice.target)
    ref = clean(notice.ref, 80)
    pass_to_fail = [r for r in notice.regressions if r.baseline_status]
    if pass_to_fail:
        n = len(pass_to_fail)
        return f"{label}: {n} scenario{'s' if n != 1 else ''} regressed on {ref}"
    return f"{label}: quality bar breached on {ref}"


def body(notice: Notice) -> str:
    ref = clean(notice.ref, 80)
    pass_to_fail = [r for r in notice.regressions if r.baseline_status]
    out = []
    if pass_to_fail:
        n = len(pass_to_fail)
        out.append(
            f"**{n} scenario{'s' if n != 1 else ''} that passed in the baseline "
            f"now fail{'s' if n == 1 else ''} on `{ref}`.** Publishing was not "
            "blocked. Each row says what the judge saw; start with the first."
        )
    else:
        why = (
            "No scenario went PASS → FAIL against the baseline"
            if notice.compared_categories
            else "There is no committed baseline to diff against"
        )
        out.append(
            f"**The eval on `{ref}` fell below its quality bar** "
            f"({'; '.join(notice.bar_breaches)}). Publishing was not blocked. "
            f"{why}, so the rows are every scenario that failed in this run."
        )
    out += [
        "",
        "| scenario | category | baseline | this run | judge's root cause |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in notice.regressions[:MAX_ROWS]:
        out.append(
            f"| {code_id(r.scenario_id)} | {code_id(r.category)} | "
            f"{fmt_score(r.baseline_status, r.baseline_score)} | "
            f"{fmt_score(r.current_status, r.current_score)} | "
            f"{r.root_cause or '—'} |"
        )
    if len(notice.regressions) > MAX_ROWS:
        out.append(
            f"\n…and {len(notice.regressions) - MAX_ROWS} more; the artifacts have "
            "every scorecard."
        )
    out.append("")
    if notice.improved:
        out.append(
            f"{notice.improved} scenario{'s' if notice.improved != 1 else ''} went "
            "the other way, FAIL → PASS."
        )
        out.append("")
    artifacts = ", ".join(f"`{a}`" for a in notice.artifacts) or "none uploaded"
    out += [
        f"- Run: {notice.run_url}",
        f"- Transcripts and judge rationales: artifact {artifacts} (kept 14 days; "
        "the eval machines are gone, so this is the only copy)",
        "",
        (
            "Every row is in the stable smoke set (each passed at least 90% of 10+ "
            "runs), where a flip is rarely noise. "
            if notice.regressions and notice.regressions[0].baseline_status
            else ""
        )
        + "This issue is updated in place by the next tag that regresses, and "
        "closed by the next one that passes.",
        "",
        "<details>",
        "<summary>🔍 Technical details</summary>",
        "",
    ]
    if notice.baseline_dir:
        out.append(f"- Baseline: `{clean(notice.baseline_dir, 200)}`")
    if notice.tier:
        out.append(f"- Tier: `{clean(notice.tier, 30)}`")
    if notice.bar_breaches and pass_to_fail:
        out.append(f"- Quality bar also breached: {'; '.join(notice.bar_breaches)}")
    out.append(
        f"- Compared: {notice.compared_scenarios} scenario(s) in "
        f"{len(notice.compared_categories)} categor"
        f"{'y' if len(notice.compared_categories) == 1 else 'ies'}"
    )
    fixes = [r for r in notice.regressions[:MAX_ROWS] if r.fix]
    if fixes:
        out += ["", "Judge's recommended fix, per scenario:", ""]
        out += [f"- {code_id(r.scenario_id)} → {r.fix}" for r in fixes]
    if notice.watch:
        out += ["", "Also PASS → FAIL, outside the stable set (watch, not filed):", ""]
        out += [watch_line(r) for r in notice.watch[:MAX_ROWS]]
    if notice.score_drops:
        out += [
            "",
            "Still passing, but the score dropped 2+ points: "
            + ", ".join(notice.score_drops[:20]),
        ]
    if notice.not_measured:
        out += ["", "Not measured in this run:", ""]
        out += [f"- {line}" for line in notice.not_measured]
    out += [
        "",
        "Reproduce a row: download the artifact, then "
        "`gaia eval agent --compare <baseline>/scorecard_<category>.json "
        "<artifact>/<category>/scorecard.json`.",
        "</details>",
    ]
    return "\n".join(out) + "\n"


def watch_line(r: Regression) -> str:
    return (
        f"- {code_id(r.scenario_id)} ({code_id(r.category)}): "
        f"{fmt_score(r.baseline_status, r.baseline_score)} → "
        f"{fmt_score(r.current_status, r.current_score)}"
    )


def summary(notice: Notice) -> str:
    label = TARGET_LABELS.get(notice.target, notice.target)
    ref = clean(notice.ref, 80)
    out = [f"### {label} verdict on `{ref}`: {notice.verdict}", ""]
    if notice.verdict == "regressed":
        out.append(
            f"**Measured regression.** {len(notice.regressions)} scenario row(s) and "
            f"{len(notice.bar_breaches)} quality-bar breach(es); the `eval-failure` "
            "issue for this target is filed or updated."
        )
    elif notice.verdict == "passed":
        out.append(
            f"**No regression.** {notice.compared_scenarios} scenario(s) in "
            f"{len(notice.compared_categories)} categories held against the baseline "
            f"({notice.improved} improved). Open `eval-failure` issues for this "
            "target are closed."
        )
    else:
        out.append(
            "**No verdict: nothing filed, nothing closed.** This run did not measure "
            "enough to say whether quality dropped:"
        )
    if notice.not_measured:
        out.append("")
        out += [f"- {line}" for line in notice.not_measured]
    if notice.watch:
        out += [
            "",
            f"**Watch:** {len(notice.watch)} scenario(s) went PASS → FAIL outside "
            "the stable smoke set, where one run against a one-run baseline flips "
            "about 18% of the time (#5493). Not filed; a stable-set flip would be.",
            "",
        ]
        out += [watch_line(r) for r in notice.watch[:MAX_ROWS]]
    return "\n".join(out) + "\n"


# --- CLI ----------------------------------------------------------------------


def _job(value: str):
    key, sep, result = value.partition("=")
    if not sep or not key.strip():
        raise argparse.ArgumentTypeError(f"--job wants KEY=RESULT, got {value!r}")
    return key.strip(), result.strip()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--target", required=True, choices=sorted(TARGET_LABELS))
    p.add_argument("--tier", required=True, help="smoke, full, verify or release")
    p.add_argument("--ref", required=True, help="The tag (or ref) the run measured")
    p.add_argument("--run-url", required=True)
    p.add_argument("--artifacts-dir", type=Path, default=Path("eval-artifacts"))
    p.add_argument("--baseline-dir", type=Path)
    p.add_argument("--matrix-json", help="Lane matrix; scorecards per lane artifact")
    p.add_argument("--artifact-prefix", default="eval-")
    p.add_argument("--categories", help="Comma-separated, for a one-artifact run")
    p.add_argument("--artifact-name", default="gaia-agent-eval")
    p.add_argument("--quality-report", type=Path)
    p.add_argument(
        "--stable-scenarios",
        type=Path,
        default=DEFAULT_STABLE_FILE,
        help="Tier file whose smoke scenarios are filed on a lone flip",
    )
    p.add_argument("--job", type=_job, action="append", help="KEY=RESULT")
    p.add_argument("--outcomes-failed", default="")
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--github-output", type=Path)
    p.add_argument("--step-summary", type=Path)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        notice = build(args)
    except ValueError as e:
        print(
            f"::error title=Eval notice::{e}. No verdict was reached, so no issue "
            "was filed or closed. Fix the input and re-run this job."
        )
        return 1
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "summary.md").write_text(summary(notice), encoding="utf-8")
    if notice.verdict == "regressed":
        (args.out_dir / "title.txt").write_text(title(notice), encoding="utf-8")
        (args.out_dir / "body.md").write_text(body(notice), encoding="utf-8")
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as fh:
            fh.write(f"verdict={notice.verdict}\n")
            if notice.verdict == "regressed":
                fh.write(f"title={title(notice)}\n")
    if args.step_summary:
        with args.step_summary.open("a", encoding="utf-8") as fh:
            fh.write(summary(notice) + "\n")
    level = {"regressed": "warning", "passed": "notice"}.get(notice.verdict, "warning")
    print(
        f"::{level} title=Eval verdict::{notice.verdict} - "
        + (
            "; ".join(notice.not_measured[:3])
            if notice.verdict == "no-verdict"
            else f"{len(notice.regressions)} regressed, {notice.improved} improved"
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
