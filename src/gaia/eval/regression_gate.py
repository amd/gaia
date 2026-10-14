# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Noise-aware regression gate for the flagship scenario eval (``eval_flagship.yml``).

A single run is a noisy measurement: across 11 runs on main with the same model
and judge, a third of the scenarios passed somewhere between 20% and 80% of the
time, and every run flipped 8-16 baseline passes to FAIL, most of which passed
again on later runs. A gate that blocks on any one PASS->FAIL is therefore
always red, and an always-red gate gates nothing. This one blocks only on
regressions the run has *confirmed*:

* **Confirm before counting.** A scenario that went PASS->FAIL against the
  baseline is re-run on the same machine (``confirm``), stopping at the first
  pass. It is a regression only if a re-run fails and none passes; re-runs that
  only time out or error leave it unresolved (warned, not gated). Re-run results
  are written next to the scorecard and printed in the verdict, never folded
  away.
* **Category thresholds.** A category fails when it loses at least
  ``category_max_confirmed_losses`` confirmed scenarios, or its pass rate over
  the gated scenarios drops by more than ``category_max_pass_rate_drop_pp``
  points (``gate``).
* **Aggregate floor.** The whole run fails when its judged passes, over the
  scenarios both sides judged, fall below the baseline's (``aggregate``).
* **Visible quarantine.** Scenarios measured as unstable are still run and
  reported, as a ``::warning::`` carrying their measured pass rate, but never
  gate. Every entry needs a measured rate and a tracking issue;
  :func:`load_config` refuses an entry without them.

Thresholds and the quarantine live in one checked-in file,
``tests/fixtures/gaia/flagship_regression_gate.json``.

What this does NOT judge: completeness (``gaia.eval.integrity_gate`` owns
"did we measure at all"), score-only drops and wall-clock drops (``gaia eval
agent --compare`` still prints them, as a prompt to look rather than a verdict).

Stdlib only, like ``lane_summary``: the ``aggregate`` subcommand runs as a file
on a hosted runner with no ``gaia`` install.

Usage::

    # In the lane, right after `gaia eval agent --category X`, backend still up:
    python -m gaia.eval.regression_gate confirm --category X \\
        --eval-log eval-out/X.log --results-dir eval-out \\
        --baseline-dir tests/fixtures/eval_baselines/gaia-flagship \\
        --backend http://127.0.0.1:4200 --model claude-sonnet-4-6

    # Per-lane verdict:
    python -m gaia.eval.regression_gate gate --results-dir eval-out \\
        --baseline-dir tests/fixtures/eval_baselines/gaia-flagship \\
        --category X --category Y --enforce

    # Whole-run floor, over every lane's artifact:
    python src/gaia/eval/regression_gate.py aggregate \\
        --artifacts-dir lane-artifacts --lanes-file eval/ci_lanes.json \\
        --baseline-dir tests/fixtures/eval_baselines/gaia-flagship --enforce

Exit codes:
    0 - no confirmed regression, or one found without ``--enforce``.
    1 - a confirmed regression under ``--enforce``; a re-run that crashed; or
        an unreadable config.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "fixtures"
    / "gaia"
    / "flagship_regression_gate.json"
)

JUDGED = ("PASS", "FAIL")
RERUNS_FILE = "reruns.json"
_OUTPUT_LINE = re.compile(r"^Output:\s*(.+?)\s*$")


@dataclass(frozen=True)
class GateConfig:
    confirm_reruns: int
    max_reruns_per_category: int
    category_max_confirmed_losses: int
    category_max_pass_rate_drop_pp: float
    aggregate_max_lost_passes: int
    quarantine: dict = field(default_factory=dict)


def _require_int(doc: dict, key: str, path: Path, minimum: int) -> int:
    value = doc.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(
            f"{path}: '{key}' must be an integer >= {minimum}, got {value!r}. "
            "Fix the gate config; it is the single source of the thresholds."
        )
    return value


def load_config(path: Path = DEFAULT_CONFIG) -> GateConfig:
    """Read and validate the gate config. Raises ``ValueError`` on any defect."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise ValueError(f"cannot read the regression-gate config {path}: {e}") from e

    drop = doc.get("category_max_pass_rate_drop_pp")
    if isinstance(drop, bool) or not isinstance(drop, (int, float)) or drop < 0:
        raise ValueError(
            f"{path}: 'category_max_pass_rate_drop_pp' must be a number >= 0, "
            f"got {drop!r}."
        )

    quarantine = {}
    for i, entry in enumerate(doc.get("quarantine") or []):
        where = f"{path}: quarantine[{i}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{where} must be an object, got {entry!r}.")
        sid = entry.get("scenario")
        if not isinstance(sid, str) or not sid:
            raise ValueError(f"{where} needs a 'scenario' id.")
        rate = entry.get("pass_rate")
        runs = entry.get("runs")
        issue = entry.get("issue")
        if isinstance(rate, bool) or not isinstance(rate, (int, float)):
            raise ValueError(
                f"{where} ({sid}) has no measured 'pass_rate'. A scenario is "
                "quarantined on evidence: record the pass rate measured over "
                "repeated runs on the Strix Halo pool."
            )
        if not 0 <= rate <= 1:
            raise ValueError(f"{where} ({sid}) 'pass_rate' {rate} is not in [0, 1].")
        if isinstance(runs, bool) or not isinstance(runs, int) or runs < 2:
            raise ValueError(
                f"{where} ({sid}) needs 'runs' >= 2: the number of judged runs "
                "the pass rate was measured over."
            )
        if isinstance(issue, bool) or not isinstance(issue, int) or issue <= 0:
            raise ValueError(
                f"{where} ({sid}) needs a tracking 'issue' number. A quarantine "
                "with no owner is a scenario that silently stopped gating."
            )
        if sid in quarantine:
            raise ValueError(f"{where}: '{sid}' is quarantined twice.")
        quarantine[sid] = entry

    return GateConfig(
        confirm_reruns=_require_int(doc, "confirm_reruns", path, 0),
        max_reruns_per_category=_require_int(doc, "max_reruns_per_category", path, 0),
        category_max_confirmed_losses=_require_int(
            doc, "category_max_confirmed_losses", path, 1
        ),
        category_max_pass_rate_drop_pp=float(drop),
        aggregate_max_lost_passes=_require_int(
            doc, "aggregate_max_lost_passes", path, 0
        ),
        quarantine=quarantine,
    )


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _statuses(scorecard: dict) -> dict:
    return {
        s["scenario_id"]: s.get("status")
        for s in scorecard.get("scenarios") or []
        if s.get("scenario_id")
    }


def flips_to_confirm(baseline: dict, current: dict, config: GateConfig) -> list:
    """Baseline-PASS scenarios that FAILED now and are not quarantined."""
    base = _statuses(baseline)
    cur = _statuses(current)
    return sorted(
        sid
        for sid, status in base.items()
        if status == "PASS" and cur.get(sid) == "FAIL" and sid not in config.quarantine
    )


@dataclass
class CategoryVerdict:
    category: str
    gated: int = 0
    baseline_passed: int = 0
    current_passed: int = 0
    confirmed: list = field(default_factory=list)
    cleared: list = field(default_factory=list)
    improved: list = field(default_factory=list)
    quarantined_flips: list = field(default_factory=list)
    unresolved: list = field(default_factory=list)
    reasons: list = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return bool(self.reasons)

    @property
    def drop_pp(self) -> float:
        if not self.gated:
            return 0.0
        return 100.0 * (self.baseline_passed - self.current_passed) / self.gated


def evaluate_category(
    category: str,
    baseline: dict,
    current: dict,
    reruns: dict,
    config: GateConfig,
) -> CategoryVerdict:
    """Judge one category.

    ``reruns`` is the ``reruns.json`` the ``confirm`` step wrote:
    ``{"attempts": {sid: [status, ...]}, "not_rerun": [sid, ...]}``. A flip is
    cleared by any re-run PASS and confirmed by a re-run FAIL, or by having no
    re-run at all. Re-runs that all produced no judged result leave it
    *unresolved*: reported, and kept out of the verdict like any other
    scenario without a measurement.
    """
    attempts = (reruns or {}).get("attempts") or {}
    base = _statuses(baseline)
    cur = _statuses(current)
    verdict = CategoryVerdict(category)

    for sid in sorted(set(base) & set(cur)):
        b, c = base[sid], cur[sid]
        tries = attempts.get(sid, [])
        if sid in config.quarantine:
            if b == "PASS" and c == "FAIL":
                verdict.quarantined_flips.append((sid, tries))
            continue
        # A side without a judged result is the integrity gate's to report.
        if b not in JUDGED or c not in JUDGED:
            continue
        flip = b == "PASS" and c == "FAIL"
        if flip and tries and not any(t in JUDGED for t in tries):
            verdict.unresolved.append((sid, tries))
            continue
        verdict.gated += 1
        verdict.baseline_passed += b == "PASS"
        final = c
        if flip:
            if "PASS" in tries:
                verdict.cleared.append((sid, tries))
                final = "PASS"
            else:
                verdict.confirmed.append((sid, tries))
        elif b == "FAIL" and c == "PASS":
            verdict.improved.append(sid)
        verdict.current_passed += final == "PASS"

    k = config.category_max_confirmed_losses
    if len(verdict.confirmed) >= k:
        verdict.reasons.append(
            f"{len(verdict.confirmed)} confirmed regression(s), limit is {k - 1}"
        )
    if verdict.drop_pp > config.category_max_pass_rate_drop_pp:
        verdict.reasons.append(
            f"pass rate fell {verdict.drop_pp:.1f} points "
            f"({verdict.baseline_passed}/{verdict.gated} -> "
            f"{verdict.current_passed}/{verdict.gated}), limit is "
            f"{config.category_max_pass_rate_drop_pp:g}"
        )
    return verdict


def _tries(tries: list) -> str:
    return ", ".join(tries) if tries else "not re-run"


def _quarantine_note(sid: str, config: GateConfig) -> str:
    q = config.quarantine[sid]
    return (
        f"measured pass rate {q['pass_rate']:.0%} over {q['runs']} runs, "
        f"tracked in #{q['issue']}"
    )


def render_category(verdict: CategoryVerdict, config: GateConfig) -> list:
    """Console lines for one category. Every bucket is shown, cleared flips too."""
    v = verdict
    state = "FAIL" if v.failed else "ok"
    lines = [
        f"{v.category}: {state} - {v.current_passed}/{v.gated} gated scenarios pass "
        f"(baseline {v.baseline_passed}/{v.gated}), "
        f"{len(v.confirmed)} confirmed, {len(v.cleared)} cleared on re-run, "
        f"{len(v.improved)} improved, {len(v.quarantined_flips)} quarantined flip(s), "
        f"{len(v.unresolved)} unresolved"
    ]
    for sid, tries in v.confirmed:
        lines.append(f"    CONFIRMED  {sid}  (re-runs: {_tries(tries)})")
    for sid, tries in v.cleared:
        lines.append(f"    cleared    {sid}  (re-runs: {_tries(tries)})")
    for sid in v.improved:
        lines.append(f"    improved   {sid}")
    for sid, _ in v.quarantined_flips:
        lines.append(f"    quarantine {sid}  ({_quarantine_note(sid, config)})")
    for sid, tries in v.unresolved:
        lines.append(f"    unresolved {sid}  (re-runs: {_tries(tries)})")
    for reason in v.reasons:
        lines.append(f"    -> {reason}")
    return lines


def _write_summary(lines: list) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def _summary_rows(verdicts: list) -> list:
    rows = [
        "| category | gated pass | baseline | confirmed | cleared on re-run | quarantined flips | verdict |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for v in verdicts:
        rows.append(
            f"| `{v.category}` | {v.current_passed}/{v.gated} | "
            f"{v.baseline_passed}/{v.gated} | {len(v.confirmed)} | {len(v.cleared)} | "
            f"{len(v.quarantined_flips)} | "
            f"{'**FAIL**: ' + '; '.join(v.reasons) if v.failed else 'ok'} |"
        )
    return rows


# ---------------------------------------------------------------------------
# confirm
# ---------------------------------------------------------------------------


def run_dir_from_log(log: Path) -> Path | None:
    """The run dir from the eval's last ``Output: <dir>`` line, as CI parses it."""
    try:
        text = Path(log).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None
    hits = [m.group(1) for m in map(_OUTPUT_LINE.match, text.splitlines()) if m]
    return Path(hits[-1]) if hits else None


def _rerun_once(sid: str, backend: str, model: str, log: Path) -> tuple:
    """Run ``gaia eval agent --scenario sid`` once. Returns (exit, run_dir)."""
    gaia = shutil.which("gaia")
    if not gaia:
        raise RuntimeError(
            "the `gaia` CLI is not on PATH, so no flip can be re-run. Activate the "
            "venv the eval ran from before calling `regression_gate confirm`."
        )
    cmd = [
        gaia,
        "eval",
        "agent",
        "--scenario",
        sid,
        "--backend",
        backend,
        "--model",
        model,
    ]
    with open(log, "w", encoding="utf-8") as handle:
        proc = subprocess.run(
            cmd,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    return proc.returncode, run_dir_from_log(log)


def confirm_category(
    category: str,
    baseline: dict,
    current: dict,
    config: GateConfig,
    reruns: int,
    out_dir: Path,
    rerun_once=None,
) -> dict:
    """Re-run each unquarantined PASS->FAIL flip up to ``reruns`` times.

    Stops a scenario at its first PASS. Every attempt's scorecard and log are
    kept under ``out_dir``. Raises ``RuntimeError`` when a re-run crashes, the
    same way a crashed category fails the lane: a crashed re-run is not a FAIL.
    """
    rerun_once = rerun_once or _rerun_once
    out_dir.mkdir(parents=True, exist_ok=True)
    flips = flips_to_confirm(baseline, current, config)
    budget = config.max_reruns_per_category
    record = {
        "category": category,
        "reruns_per_flip": reruns,
        "flips": flips,
        "attempts": {},
        "not_rerun": [],
    }
    for n, sid in enumerate(flips):
        # Past the budget a category is already far beyond noise; the rest count
        # as confirmed and are named, so the cap never hides a scenario.
        if reruns == 0 or n >= budget:
            record["not_rerun"].append(sid)
            continue
        tries = []
        for attempt in range(1, reruns + 1):
            log = out_dir / f"{sid}.{attempt}.log"
            code, run_dir = rerun_once(sid, log)
            scorecard = (run_dir / "scorecard.json") if run_dir else None
            if code != 0 or not scorecard or not scorecard.exists():
                raise RuntimeError(
                    f"re-run {attempt} of {category}/{sid} crashed (exit {code}, "
                    f"run dir {run_dir}); see {log}. That is a harness failure, "
                    "not a FAIL, so the flip cannot be confirmed or cleared."
                )
            shutil.copyfile(scorecard, out_dir / f"{sid}.{attempt}.json")
            status = _statuses(_read_json(scorecard) or {}).get(sid, "MISSING")
            tries.append(status)
            if status == "PASS":
                break
        record["attempts"][sid] = tries
    (out_dir.parent / RERUNS_FILE).write_text(
        json.dumps(record, indent=2), encoding="utf-8"
    )
    return record


def _cmd_confirm(args, config: GateConfig) -> int:
    baseline_path = Path(args.baseline_dir) / f"scorecard_{args.category}.json"
    baseline = _read_json(baseline_path)
    if baseline is None:
        print(f"{args.category}: no baseline at {baseline_path}; nothing to confirm.")
        return 0
    run_dir = run_dir_from_log(Path(args.eval_log))
    current = _read_json(run_dir / "scorecard.json") if run_dir else None
    if current is None:
        # Reported by the collect step and the integrity gate; not a re-run job.
        print(
            f"{args.category}: no scorecard from {args.eval_log}; nothing to confirm."
        )
        return 0
    reruns = config.confirm_reruns if args.reruns is None else args.reruns
    out_dir = Path(args.results_dir) / args.category / "reruns"

    def rerun_once(sid, log):
        return _rerun_once(sid, args.backend, args.model, log)

    try:
        record = confirm_category(
            args.category, baseline, current, config, reruns, out_dir, rerun_once
        )
    except RuntimeError as e:
        print(f"::error::{e}")
        return 1
    print(
        f"{args.category}: {len(record['flips'])} unquarantined PASS->FAIL flip(s), "
        f"{reruns} re-run(s) each"
    )
    for sid, tries in record["attempts"].items():
        print(f"    {sid}: {_tries(tries)}")
    for sid in record["not_rerun"]:
        print(f"    {sid}: not re-run (budget or reruns=0) - counts as confirmed")
    return 0


# ---------------------------------------------------------------------------
# gate / aggregate
# ---------------------------------------------------------------------------


def judge_results_dir(
    results_dir: Path, baseline_dir: Path, categories: list, config: GateConfig
) -> tuple:
    """Verdicts for each category with both a baseline and a scorecard.

    Returns ``(verdicts, notes)``; ``notes`` names what could not be judged.
    """
    verdicts, notes = [], []
    for category in categories:
        baseline = _read_json(baseline_dir / f"scorecard_{category}.json")
        if baseline is None:
            notes.append(f"{category}: not compared - no baseline yet")
            continue
        current = _read_json(results_dir / category / "scorecard.json")
        if current is None:
            notes.append(f"{category}: NOT JUDGED - this run produced no scorecard")
            continue
        reruns = _read_json(results_dir / category / RERUNS_FILE) or {}
        verdicts.append(evaluate_category(category, baseline, current, reruns, config))
    return verdicts, notes


def _emit_warnings(verdicts: list, config: GateConfig) -> None:
    for v in verdicts:
        for sid, _ in v.quarantined_flips:
            print(
                f"::warning::{v.category}/{sid} failed but is quarantined "
                f"({_quarantine_note(sid, config)}), so it does not gate."
            )
        for sid, tries in v.unresolved:
            print(
                f"::warning::{v.category}/{sid} failed, and its re-runs produced no "
                f"judged result ({_tries(tries)}), so it is unresolved and does not "
                "gate. Read its re-run logs under reruns/ in the lane artifact."
            )


def _cmd_gate(args, config: GateConfig) -> int:
    verdicts, notes = judge_results_dir(
        Path(args.results_dir), Path(args.baseline_dir), args.categories, config
    )
    for v in verdicts:
        print("\n".join(render_category(v, config)))
    for note in notes:
        print(note)
    _emit_warnings(verdicts, config)

    failed = [v for v in verdicts if v.failed]
    unjudged = [n for n in notes if "NOT JUDGED" in n]
    level = "error" if args.enforce else "warning"
    for v in failed:
        print(f"::{level}::Regression gate: {v.category}: {'; '.join(v.reasons)}.")
    for note in unjudged:
        print(f"::{level}::Regression gate: {note}.")

    summary = ["", f"### Regression gate - lane `{args.lane or '?'}`", ""]
    summary += _summary_rows(verdicts) + [""]
    summary += [f"- {n}" for n in notes]
    if failed or unjudged:
        summary.append(
            "**enforce=true - this failed the build.**"
            if args.enforce
            else "**enforce=false - reported only.**"
        )
    _write_summary(summary)
    return 1 if (failed or unjudged) and args.enforce else 0


def _lane_categories(lanes_file: Path) -> list:
    doc = _read_json(lanes_file)
    if not doc or "lanes" not in doc:
        raise ValueError(f"cannot read the lane map at {lanes_file}")
    return [(lane["lane"], c) for lane in doc["lanes"] for c in lane["categories"]]


def _cmd_aggregate(args, config: GateConfig) -> int:
    try:
        pairs = _lane_categories(Path(args.lanes_file))
    except ValueError as e:
        print(f"::error::{e}")
        return 1
    verdicts = []
    for lane, category in pairs:
        root = Path(args.artifacts_dir) / f"{args.artifact_prefix}{lane}"
        found, _ = judge_results_dir(root, Path(args.baseline_dir), [category], config)
        verdicts += found
    base = sum(v.baseline_passed for v in verdicts)
    cur = sum(v.current_passed for v in verdicts)
    gated = sum(v.gated for v in verdicts)
    allowed = config.aggregate_max_lost_passes
    lines = [
        "",
        "### Regression gate - whole run",
        "",
        f"Judged passes over the {gated} scenarios both sides judged, "
        f"quarantine excluded and re-runs applied: **{cur}** now, "
        f"**{base}** in the baseline (floor: {base - allowed}).",
    ]
    below = cur < base - allowed
    print("\n".join(lines))
    if below:
        level = "error" if args.enforce else "warning"
        print(
            f"::{level}::Regression gate: the run passes {cur} of {gated} gated "
            f"scenarios, below the baseline's {base} (allowed loss {allowed})."
        )
        lines.append(
            "**Below the baseline - "
            + ("this failed the build.**" if args.enforce else "reported only.**")
        )
    _write_summary(lines)
    return 1 if below and args.enforce else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gaia.eval.regression_gate",
        description="Noise-aware regression gate for the flagship scenario eval.",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help="Gate thresholds and quarantine (default: %(default)s).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    c = sub.add_parser("confirm", help="Re-run this category's PASS->FAIL flips.")
    c.add_argument("--category", required=True)
    c.add_argument("--eval-log", required=True, help="The category's eval log.")
    c.add_argument("--baseline-dir", required=True)
    c.add_argument("--results-dir", required=True)
    c.add_argument("--backend", required=True)
    c.add_argument("--model", required=True)
    c.add_argument(
        "--reruns",
        type=int,
        default=None,
        help="Re-runs per flip; overrides the config's confirm_reruns.",
    )

    g = sub.add_parser("gate", help="Per-lane verdict.")
    g.add_argument("--baseline-dir", required=True)
    g.add_argument("--results-dir", required=True)
    g.add_argument("--category", action="append", default=[], dest="categories")
    g.add_argument("--lane", default=None)
    g.add_argument("--enforce", action="store_true")

    a = sub.add_parser("aggregate", help="Whole-run floor over every lane.")
    a.add_argument("--artifacts-dir", required=True)
    a.add_argument("--lanes-file", required=True)
    a.add_argument("--baseline-dir", required=True)
    a.add_argument("--artifact-prefix", default="eval-")
    a.add_argument("--enforce", action="store_true")

    args = parser.parse_args(argv)
    try:
        config = load_config(Path(args.config))
    except ValueError as e:
        print(f"::error::{e}")
        return 1
    if args.command == "confirm":
        if args.reruns is not None and args.reruns < 0:
            print("::error::--reruns must be >= 0.")
            return 1
        return _cmd_confirm(args, config)
    if args.command == "gate":
        if not args.categories:
            print("::error::regression_gate gate: no --category given; nothing judged.")
            return 1
        return _cmd_gate(args, config)
    return _cmd_aggregate(args, config)


if __name__ == "__main__":
    sys.exit(main())
