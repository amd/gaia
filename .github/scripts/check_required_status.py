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
    Shared between load_requirements() and _collect_reserved_static_names()
    so both see exactly the same parse.
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


def _collect_reserved_static_names(workflows_dir: str) -> dict[str, str]:
    """Map every fully-static (non-templated) job name, across every
    workflow file with no exclusions, to its source file.

    This is what makes prefix matching for matrix-templated jobs safe: a job
    named "Test ${{ matrix.package }}" derives the candidate prefix "Test",
    which would otherwise startswith()-match unrelated static check-runs
    like "Test Chat Agent" — or even a same-named job in an intentionally
    excluded workflow (build_tui.yml has its own static "Test" job). Every
    workflow is scanned here regardless of `exclude` or trigger type,
    because a real check-run can carry any of these names whether or not
    its source workflow is itself a requirement.
    """
    reserved: dict[str, str] = {}
    for fname, _doc, _job_id, _job, name_tmpl, is_templated in _iter_workflow_jobs(workflows_dir):
        if not is_templated and name_tmpl:
            reserved[name_tmpl] = fname
    return reserved


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
    reserved_names: dict[str, str] | None = None,
) -> EvalResult:
    reserved_names = reserved_names or {}
    result = EvalResult()
    for req in requirements:
        if not is_path_relevant(req, changed_files):
            continue
        if req.exact:
            matches = [cr for cr in check_runs if cr.name == req.name_prefix]
        else:
            # Exclude candidates that are some OTHER job's exact static
            # name — see _collect_reserved_static_names for why this is
            # necessary, not just defensive.
            matches = [
                cr
                for cr in check_runs
                if cr.name.startswith(req.name_prefix) and reserved_names.get(cr.name, req.source_file) == req.source_file
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
    args = parser.parse_args()

    requirements = load_requirements(args.workflows_dir, args.exclude)
    reserved_names = _collect_reserved_static_names(args.workflows_dir)
    changed_files = fetch_changed_files(args.changed_files)

    print(f"Derived {len(requirements)} job requirements from {args.workflows_dir} "
          f"(excluding {args.exclude}).")
    print(f"PR touches {len(changed_files)} file(s).")

    start = time.monotonic()
    while True:
        elapsed = int(time.monotonic() - start)
        check_runs = fetch_check_runs(args.repo, args.sha)
        result = evaluate(requirements, changed_files, check_runs, reserved_names)
        print(format_report(result, elapsed))

        if result.is_terminal_failure:
            for req, reason in result.failed:
                print(
                    f"::error::Required check '{req.name_prefix}' (from "
                    f"{req.source_file}) did not pass: {reason}. This is the "
                    f"failure mode issue #2767 exists to catch — a check that "
                    f"should have run for this diff did not report success."
                )
            return 1
        if result.is_fully_ok:
            print("All path-relevant required checks reported and passed.")
            return 0
        if elapsed >= args.timeout_seconds:
            for req in result.unresolved:
                print(
                    f"::error::Required check '{req.name_prefix}' (from "
                    f"{req.source_file}) never reported after {args.timeout_seconds}s."
                )
            return 1
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    sys.exit(main())
