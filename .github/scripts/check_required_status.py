#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Required Checks Gate for #2767 — aggregates PR-triggered workflows' own
check-run state instead of hand-listing which checks "should" exist.

Why derive instead of curate: GitHub Actions has no cross-workflow `needs:`,
so an aggregating gate can only *observe* the Checks API for the PR's head
SHA — it can't depend on other workflows' jobs directly. The set of what to
observe is parsed straight from every workflow file's own `pull_request:`
trigger (paths/paths-ignore) and each job's own `continue-on-error`, so a
new workflow is covered the moment it's added, with nothing to remember to
update here.

Split into `evaluate()` (pure, unit-tested — see
tests/unit/test_check_required_status.py) and a thin polling `main()` that
isn't worth unit testing beyond that split.
"""
from __future__ import annotations

import argparse
import fnmatch
import glob
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Iterable

import yaml

# Conclusions the Checks API can report. `success` and `neutral` both count
# as passing — that mirrors GitHub's own branch-protection semantics (a
# `neutral` required check does not block a merge).
PASSING_CONCLUSIONS = {"success", "neutral"}
# Terminal-bad conclusions: waiting longer will never fix these, so a match
# here ends the poll loop immediately instead of burning the full timeout.
# `skipped` is deliberately in this set, not in PASSING_CONCLUSIONS — a
# required, path-relevant job that reports "skipped" is exactly the #2755
# symptom (a job whose own `if:` gate never got a fresh evaluation).
TERMINAL_BAD_CONCLUSIONS = {"skipped", "failure", "cancelled", "timed_out", "action_required"}


@dataclass
class Requirement:
    name_prefix: str
    # Static job names ("Test", "Dependency Review") must match exactly —
    # startswith() on a short static name would spuriously match unrelated
    # check-runs (e.g. a bare "Test" job matching "Test Chat Agent"'s check
    # run). Only a matrix-templated name ("Unit Tests (py${{ matrix... }})",
    # collapsed to the prefix before the expression) needs prefix matching,
    # since the rendered suffix isn't known statically.
    exact: bool
    paths: list[str] | None
    paths_ignore: list[str] | None
    source_file: str
    job_id: str


@dataclass
class CheckRun:
    name: str
    status: str
    conclusion: str | None


@dataclass
class EvalResult:
    ok: list[Requirement] = field(default_factory=list)
    # Present, still running/queued — worth another poll.
    pending: list[Requirement] = field(default_factory=list)
    # Absent, no matching check-run row at all yet — worth another poll
    # (the sibling workflow may not have registered with the Checks API yet).
    missing: list[Requirement] = field(default_factory=list)
    # Present with a conclusion that will never change without a fresh
    # triggering event — no point polling further.
    failed: list[tuple[Requirement, str]] = field(default_factory=list)

    @property
    def unresolved(self) -> list[Requirement]:
        return self.pending + self.missing

    @property
    def is_terminal_failure(self) -> bool:
        return bool(self.failed)

    @property
    def is_fully_ok(self) -> bool:
        return not self.pending and not self.missing and not self.failed


def _on_block(doc: dict) -> dict | None:
    # `on:` is a YAML 1.1 boolean-ish key; PyYAML's safe_load parses the
    # unquoted form as the Python bool True.
    on = doc.get("on", doc.get(True, doc.get("true")))
    if isinstance(on, dict):
        return on
    return None


def _iter_workflow_jobs(workflows_dir: str):
    """Yield (fname, doc, job_id, job, name_tmpl, is_templated) for every job
    in every *.yml/*.yaml file in workflows_dir, regardless of trigger type.
    Shared between load_requirements() and _collect_all_name_matchers() so
    both see exactly the same parse.
    """
    for path in sorted(glob.glob(os.path.join(workflows_dir, "*.yml"))) + sorted(
        glob.glob(os.path.join(workflows_dir, "*.yaml"))
    ):
        fname = os.path.basename(path)
        with open(path) as f:
            try:
                doc = yaml.safe_load(f)
            except yaml.YAMLError as e:
                raise ValueError(f"invalid workflow YAML at {path}: {e}") from e
        if not isinstance(doc, dict):
            continue
        jobs = doc.get("jobs")
        if not isinstance(jobs, dict):
            continue
        for job_id, job in jobs.items():
            if not isinstance(job, dict):
                continue
            name_tmpl = job.get("name") or job_id
            yield fname, doc, job_id, job, name_tmpl, "${{" in name_tmpl


@dataclass(frozen=True)
class _NameMatcher:
    pattern: str
    exact: bool
    source_file: str
    job_id: str


def _collect_all_name_matchers(workflows_dir: str) -> list[_NameMatcher]:
    """Every job in every workflow file, as a name matcher — static names
    exact, matrix-templated names as their prefix — regardless of
    `exclude` or trigger type.

    Live evidence on #2767 (PR #2775, throwaway) is what proved a bare
    prefix guard against static names wasn't enough: test_hub_agents.yml's
    "Test ${{ matrix.package }}" collapses to the prefix "Test", which
    startswith()-matched not just an unrelated static "Test Chat Agent" but
    ALSO other matrix-templated jobs' rendered names, e.g. "Test Apps Build
    (jira)" (from test_electron.yml's own, more specific, "Test Apps Build
    (" prefix) and "Test MCPAgent (ubuntu-latest)". The verdict came out
    right that time (both were legitimately failing), but the *reason*
    attributed those failures to the wrong job. `_most_specific_owner`
    below is the general fix: every check-run name belongs to whichever
    matcher matches it most specifically — an exact match beats any prefix,
    and among prefixes the longest (most specific) one wins.
    """
    matchers = []
    for fname, _doc, job_id, _job, name_tmpl, is_templated in _iter_workflow_jobs(workflows_dir):
        if not name_tmpl:
            continue
        prefix = name_tmpl.split("${{", 1)[0].rstrip() if is_templated else name_tmpl
        if not prefix:
            continue
        matchers.append(_NameMatcher(pattern=prefix, exact=not is_templated, source_file=fname, job_id=job_id))
    return matchers


def _most_specific_owner(name: str, matchers: list[_NameMatcher]) -> tuple[str, str] | None:
    best: _NameMatcher | None = None
    for m in matchers:
        matched = name == m.pattern if m.exact else name.startswith(m.pattern)
        if not matched:
            continue
        if best is None:
            best = m
            continue
        # An exact match always outranks a prefix match; among prefixes,
        # the longer (more specific) one wins.
        best_specificity = len(best.pattern) + (1000 if best.exact else 0)
        cand_specificity = len(m.pattern) + (1000 if m.exact else 0)
        if cand_specificity > best_specificity:
            best = m
    return (best.source_file, best.job_id) if best else None


def load_requirements(workflows_dir: str, exclude: Iterable[str]) -> list[Requirement]:
    """Parse every workflow with a `pull_request:` trigger into a flat list
    of per-job requirements. A job is excluded (not required) when the
    workflow itself has none to give: `continue-on-error: true` on that job
    means its own author has already declared it non-blocking.
    """
    exclude_set = set(exclude)
    reqs: list[Requirement] = []
    for fname, doc, job_id, job, name_tmpl, is_templated in _iter_workflow_jobs(workflows_dir):
        if fname in exclude_set:
            continue
        on = _on_block(doc)
        if on is None:
            continue
        pr = on.get("pull_request")
        if not isinstance(pr, dict):
            # No `pull_request:` trigger at all (or a bare `pull_request:` with
            # no filters, e.g. `on: [push, pull_request]`) -> if the key is
            # present at all with no dict body, it means "no path filter";
            # if pull_request isn't a trigger on this workflow, skip it.
            if "pull_request" not in on:
                continue
            paths, paths_ignore = None, None
        else:
            paths = pr.get("paths")
            paths_ignore = pr.get("paths-ignore")

        if job.get("continue-on-error") is True:
            continue
        # Matrix-templated names ("Foo (${{ matrix.x }})") can't be
        # statically expanded without evaluating GH Actions expressions;
        # match by the static prefix before the first expression instead.
        # A fully static name must match exactly (see the `exact` field
        # docstring on Requirement for why).
        prefix = name_tmpl.split("${{", 1)[0].rstrip() if is_templated else name_tmpl
        if not prefix:
            continue
        reqs.append(
            Requirement(
                name_prefix=prefix,
                exact=not is_templated,
                paths=paths,
                paths_ignore=paths_ignore,
                source_file=fname,
                job_id=job_id,
            )
        )
    return reqs


def is_gated_off(is_draft: bool, labels: set[str]) -> bool:
    """True when the audited suites legitimately have not run yet, by the
    same convention every one of them already uses:
    `github.event.pull_request.draft == false || contains(...'ready_for_ci')`.
    This gate mirrors that condition in Python (not a job-level `if:`) so it
    can decide it fresh on every invocation instead of skipping itself.
    """
    return is_draft and "ready_for_ci" not in labels


def _matches_any(changed_file: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(changed_file, pat) for pat in patterns)


def is_path_relevant(req: Requirement, changed_files: list[str]) -> bool:
    if req.paths is None and req.paths_ignore is None:
        return True
    if req.paths is not None:
        return any(_matches_any(cf, req.paths) for cf in changed_files)
    # paths-ignore only: relevant iff at least one changed file is NOT ignored.
    return any(not _matches_any(cf, req.paths_ignore) for cf in changed_files)


def evaluate(
    requirements: list[Requirement],
    changed_files: list[str],
    check_runs: list[CheckRun],
    all_matchers: list[_NameMatcher] | None = None,
) -> EvalResult:
    all_matchers = all_matchers or []
    result = EvalResult()
    for req in requirements:
        if not is_path_relevant(req, changed_files):
            continue
        if req.exact:
            matches = [cr for cr in check_runs if cr.name == req.name_prefix]
        else:
            # Only accept a candidate if THIS job is its most specific owner
            # — see _most_specific_owner for why a plain startswith() isn't
            # enough once other prefix-mode jobs exist too.
            matches = [
                cr
                for cr in check_runs
                if cr.name.startswith(req.name_prefix)
                and _most_specific_owner(cr.name, all_matchers) in (None, (req.source_file, req.job_id))
            ]
        if not matches:
            result.missing.append(req)
            continue
        not_completed = [cr for cr in matches if cr.status != "completed"]
        bad = [cr for cr in matches if cr.status == "completed" and cr.conclusion in TERMINAL_BAD_CONCLUSIONS]
        good = [cr for cr in matches if cr.status == "completed" and cr.conclusion in PASSING_CONCLUSIONS]
        if bad:
            reasons = ", ".join(f"{cr.name}={cr.conclusion}" for cr in bad)
            result.failed.append((req, reasons))
        elif not_completed:
            result.pending.append(req)
        elif good:
            result.ok.append(req)
        else:
            # Completed with some conclusion outside both sets (e.g. a
            # brand-new conclusion value GitHub adds later) — fail loud
            # rather than silently treating an unrecognized state as passing.
            reasons = ", ".join(f"{cr.name}={cr.conclusion}" for cr in matches)
            result.failed.append((req, f"unrecognized conclusion: {reasons}"))
    return result


def fetch_changed_files(path: str) -> list[str]:
    with open(path) as f:
        return [line.strip() for line in f if line.strip()]


CUSTOM_CHECK_NAME = "Required Checks Gate"


def post_check_run(repo: str, sha: str, conclusion: str, title: str, summary: str) -> None:
    """Create a check-run via the Checks API directly, independent of this
    job's own native pass/fail check-run.

    Why a second, API-created check-run instead of just letting the job's
    exit code decide: GitHub Actions can only ever conclude a job as
    success/failure/cancelled/skipped — there's no exit code for "neutral".
    But "this PR's required suites legitimately have not run yet because
    it's a draft" is neither a pass (nothing has been verified) nor a
    failure (nothing is wrong) — see the redirect on #2767 that led to this:
    a gate that skips itself in sympathy with the jobs it's auditing
    reproduces the exact silent-green bug it exists to catch. `neutral` is
    the one Checks API conclusion built for exactly this: visible, distinct
    from both green and red, and does not block a merge on its own (mirrors
    GitHub's own branch-protection semantics for a neutral required check).
    """
    payload = {
        "name": CUSTOM_CHECK_NAME,
        "head_sha": sha,
        "status": "completed",
        "conclusion": conclusion,
        "output": {"title": title, "summary": summary},
    }
    proc = subprocess.run(
        ["gh", "api", f"repos/{repo}/check-runs", "--input", "-"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gh api check-runs create failed (exit {proc.returncode}): {proc.stderr.strip()}")


def fetch_check_runs(repo: str, sha: str) -> list[CheckRun]:
    proc = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{repo}/commits/{sha}/check-runs",
            "--paginate",
            "-q",
            ".check_runs[] | {name, status, conclusion}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"gh api check-runs lookup failed (exit {proc.returncode}): {proc.stderr.strip()}"
        )
    runs = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        runs.append(CheckRun(name=obj["name"], status=obj["status"], conclusion=obj.get("conclusion")))
    return runs


def format_report(result: EvalResult, elapsed: int) -> str:
    lines = []
    if result.ok:
        lines.append(f"OK ({len(result.ok)}): " + ", ".join(r.name_prefix for r in result.ok))
    if result.pending or result.missing:
        lines.append(
            f"WAITING ({len(result.unresolved)}, {elapsed}s elapsed): "
            + ", ".join(r.name_prefix for r in result.unresolved)
        )
    if result.failed:
        lines.append("FAILED:")
        for req, reason in result.failed:
            lines.append(f"  - {req.name_prefix} (from {req.source_file}): {reason}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflows-dir", required=True)
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--changed-files", required=True, help="path to a file, one changed path per line")
    parser.add_argument("--repo", required=True, help="owner/repo")
    parser.add_argument("--sha", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--draft", choices=["true", "false"], required=True)
    parser.add_argument("--labels", default="", help="comma-separated PR label names")
    args = parser.parse_args()

    is_draft = args.draft == "true"
    labels = {label.strip() for label in args.labels.split(",") if label.strip()}

    if is_gated_off(is_draft, labels):
        # Deliberately NOT skipped via a job-level `if:` — see the module
        # docstring on post_check_run for why a job that skips itself here
        # would reproduce #2767's own bug. This branch always actually
        # executes and always actually decides, every time it's triggered
        # (including on a bare `labeled` event with no new commit) — so it
        # can never get stuck showing a stale state the way the audited
        # jobs can.
        title = "Required suites have not run yet"
        summary = (
            "This PR is a draft without the `ready_for_ci` label, so the "
            "suites this gate audits have not run (by the same convention "
            "every audited workflow uses). Nothing has been verified — this "
            "is not a pass. Mark the PR ready for review, or add the "
            "`ready_for_ci` label, to require and verify them."
        )
        print(f"GATED OFF: {summary}")
        post_check_run(args.repo, args.sha, "neutral", title, summary)
        return 0

    requirements = load_requirements(args.workflows_dir, args.exclude)
    all_matchers = _collect_all_name_matchers(args.workflows_dir)
    changed_files = fetch_changed_files(args.changed_files)

    print(f"Derived {len(requirements)} job requirements from {args.workflows_dir} "
          f"(excluding {args.exclude}).")
    print(f"PR touches {len(changed_files)} file(s).")

    start = time.monotonic()
    while True:
        elapsed = int(time.monotonic() - start)
        check_runs = fetch_check_runs(args.repo, args.sha)
        result = evaluate(requirements, changed_files, check_runs, all_matchers)
        print(format_report(result, elapsed))

        if result.is_terminal_failure:
            details = "; ".join(f"{req.name_prefix} ({reason})" for req, reason in result.failed)
            for req, reason in result.failed:
                print(
                    f"::error::Required check '{req.name_prefix}' (from "
                    f"{req.source_file}) did not pass: {reason}. This is the "
                    f"failure mode issue #2767 exists to catch — a check that "
                    f"should have run for this diff did not report success."
                )
            post_check_run(
                args.repo,
                args.sha,
                "failure",
                "Required suites did not pass",
                f"One or more required, path-relevant checks did not pass: {details}",
            )
            return 1
        if result.is_fully_ok:
            print("All path-relevant required checks reported and passed.")
            names = ", ".join(r.name_prefix for r in result.ok) or "(none required for this diff)"
            post_check_run(
                args.repo,
                args.sha,
                "success",
                "All required suites passed",
                f"Required and path-relevant for this diff: {names}",
            )
            return 0
        if elapsed >= args.timeout_seconds:
            details = ", ".join(r.name_prefix for r in result.unresolved)
            for req in result.unresolved:
                print(
                    f"::error::Required check '{req.name_prefix}' (from "
                    f"{req.source_file}) never reported after {args.timeout_seconds}s."
                )
            post_check_run(
                args.repo,
                args.sha,
                "failure",
                "Required suites never reported",
                f"Timed out after {args.timeout_seconds}s waiting on: {details}",
            )
            return 1
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    sys.exit(main())
