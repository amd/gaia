#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Time and record each phase of an installer journey, and gate it on the
checked-in install-time thresholds.

Used by .github/workflows/test_installers_headless.yml. Every installer x OS row
runs its phases through ``phase``, which times the command with the system-wide
monotonic clock and records pass/fail in one results file per row. ``finish``
turns that file into the row's line in the job summary and fails the job when a
phase failed or ran past its threshold. ``summarize`` builds the table for the
whole run from every row's file.

Stdlib only: it runs on stock GitHub-hosted Windows, macOS and Ubuntu runners
before anything is installed.

    python journey.py init     --results r.json --row win-setup-ui --title "..." ...
    python journey.py phase    --results r.json --phase install -- <command...>
    python journey.py skip     --results r.json --phase upgrade --reason "..."
    python journey.py finish   --results r.json --thresholds installer/install_time_thresholds.json
    python journey.py summarize --dir results/ --thresholds ... --out install-timings.json
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# The columns of the summary table, in journey order. A row records only the
# phases it has; the rest render as "n/a".
PHASES = [
    "download",
    "install",
    "upgrade",
    "launch",
    "health",
    "preflight",
    "uninstall",
    "leftovers",
]

# Timed metrics the thresholds file can gate. ``launch_to_healthy`` is derived:
# the time from starting the app to its health check answering is launch +
# health, which is what a user waits through on first launch.
METRICS = ["download", "install", "launch_to_healthy", "upgrade", "uninstall"]

THRESHOLDS_URL = (
    "https://github.com/amd/gaia/blob/main/installer/install_time_thresholds.json"
)

PASS, FAIL, NA, SKIPPED = "pass", "fail", "n/a", "skipped"


def _load(path: Path) -> Dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _save(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def runner_image() -> str:
    """The hosted image this ran on, e.g. ``ubuntu24/20261013.1``."""
    image = os.environ.get("ImageOS", "")
    version = os.environ.get("ImageVersion", "")
    if image or version:
        return f"{image}/{version}".strip("/")
    return f"local-{platform.system().lower()}-{platform.machine().lower()}"


def metrics_of(result: Dict[str, Any]) -> Dict[str, float]:
    """Seconds per gateable metric, for the phases that passed."""
    phases = result.get("phases", {})

    def ok(name: str) -> Optional[float]:
        p = phases.get(name)
        if p and p.get("status") == PASS and p.get("seconds") is not None:
            return float(p["seconds"])
        return None

    out: Dict[str, float] = {}
    for name in ("download", "install", "upgrade", "uninstall"):
        value = ok(name)
        if value is not None:
            out[name] = value
    launch, health = ok("launch"), ok("health")
    if launch is not None and health is not None:
        out["launch_to_healthy"] = launch + health
    return out


# ── threshold gate ───────────────────────────────────────────────────────────


def check_thresholds(
    result: Dict[str, Any], thresholds: Dict[str, Any]
) -> tuple[List[str], List[str]]:
    """Return (errors, warnings) for one row against the thresholds file."""
    errors: List[str] = []
    warnings: List[str] = []
    gated = set(thresholds.get("gated_metrics", []))
    row = result["row"]
    entry = thresholds.get("rows", {}).get(row)
    measured = metrics_of(result)
    for metric in METRICS:
        if metric not in measured or metric not in gated:
            continue
        limit = (entry or {}).get("metrics", {}).get(metric, {}).get("threshold")
        if limit is None:
            warnings.append(
                f"{row} / {metric}: no threshold yet (took {measured[metric]:.0f}s). "
                f"Not a pass: add one from measured runs, see {THRESHOLDS_URL}"
            )
            continue
        if measured[metric] > float(limit):
            errors.append(
                f"{row} / {metric}: took {measured[metric]:.0f}s, over its "
                f"{float(limit):.0f}s threshold. If the slowdown is real, find what "
                f"grew; if the runners changed, re-measure. See {THRESHOLDS_URL}"
            )
    return errors, warnings


# ── commands ─────────────────────────────────────────────────────────────────


def cmd_init(args: argparse.Namespace) -> int:
    path = Path(args.results)
    path.parent.mkdir(parents=True, exist_ok=True)
    _save(
        path,
        {
            "row": args.row,
            "title": args.title,
            "os": args.os_label,
            "installer": args.installer,
            "release": args.release,
            "source": args.source,
            "runner_image": runner_image(),
            "run_id": os.environ.get("GITHUB_RUN_ID", ""),
            "phases": {},
            "notes": [],
        },
    )
    return 0


def _record(path: Path, phase: str, **fields: Any) -> None:
    data = _load(path)
    data["phases"][phase] = fields
    _save(path, data)


def cmd_phase(args: argparse.Namespace) -> int:
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("journey.py phase: no command given after --", file=sys.stderr)
        return 2
    path = Path(args.results)
    print(f"::group::{args.phase}: {' '.join(command)}", flush=True)
    # time.monotonic is the system-wide monotonic clock on all three runner
    # OSes, so a wall-clock jump mid-install cannot bend the measurement.
    start = time.monotonic()
    try:
        code = subprocess.call(command)
    except OSError as exc:
        print(f"::error::{args.phase}: could not start {command[0]}: {exc}", flush=True)
        code = 127
    seconds = round(time.monotonic() - start, 1)
    print("::endgroup::", flush=True)
    status = PASS if code == 0 else FAIL
    _record(path, args.phase, status=status, seconds=seconds, exit_code=code)
    print(f"{args.phase}: {status} in {seconds}s (exit {code})", flush=True)
    if code != 0:
        print(f"::error::{_load(path)['row']}: {args.phase} failed (exit {code})")
    return code


def cmd_bounded(args: argparse.Namespace) -> int:
    """Run a command with a hard time bound; exit 124 when the bound is hit.

    macOS ships no `timeout`, and a check whose point is "does not hang" needs
    one on every OS. The whole process tree goes on timeout, not just the
    direct child, so a sidecar the command spawned cannot outlive the check.
    """
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    out = open(args.stdout, "wb") if args.stdout else None
    err = open(args.stderr, "wb") if args.stderr else None
    kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        kwargs["start_new_session"] = True
    start = time.monotonic()
    proc = subprocess.Popen(command, stdout=out, stderr=err, **kwargs)
    try:
        code = proc.wait(timeout=args.seconds)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.call(["taskkill", "/T", "/F", "/PID", str(proc.pid)])
        else:
            import signal

            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        proc.wait()
        code = 124
    finally:
        for fh in (out, err):
            if fh:
                fh.close()
    elapsed = time.monotonic() - start
    print(
        f"bounded: exit {code} after {elapsed:.1f}s (bound {args.seconds:g}s)",
        file=sys.stderr,
    )
    if args.elapsed_file:
        Path(args.elapsed_file).write_text(f"{elapsed:.1f}\n", encoding="utf-8")
    return code


def cmd_skip(args: argparse.Namespace) -> int:
    _record(Path(args.results), args.phase, status=NA, reason=args.reason)
    return 0


def cmd_finding(args: argparse.Namespace) -> int:
    """Record a defect the row found without stopping its later phases.

    For a contract the installer breaks that does not block the rest of the
    journey (a documented command missing, a package name that collides), so
    one bug does not hide whether everything after it works. Any finding fails
    the row.
    """
    path = Path(args.results)
    data = _load(path)
    data.setdefault("findings", []).append(args.text)
    _save(path, data)
    print(f"::error::{data['row']}: {args.text}")
    return 0


def cmd_note(args: argparse.Namespace) -> int:
    path = Path(args.results)
    data = _load(path)
    data["notes"].append(args.text)
    _save(path, data)
    return 0


def _cell(result: Dict[str, Any], phase: str) -> str:
    p = result.get("phases", {}).get(phase)
    if p is None:
        return NA
    status = p.get("status", "?")
    if status in (PASS, FAIL) and p.get("seconds") is not None:
        return f"{'pass' if status == PASS else '**FAIL**'} {p['seconds']:.0f}s"
    if status == NA:
        return "n/a"
    return status


def _finalize(result: Dict[str, Any], expected: List[str]) -> None:
    """Mark the phases this row should have run but never reached."""
    for phase in expected:
        if phase not in result["phases"]:
            result["phases"][phase] = {
                "status": SKIPPED,
                "reason": "an earlier phase failed",
            }


def table(results: List[Dict[str, Any]]) -> str:
    head = ["Row", "OS", "Source"] + [p.capitalize() for p in PHASES] + ["Findings"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in sorted(results, key=lambda r: r["row"]):
        cells = [f"`{r['row']}`", r.get("os", ""), r.get("source", "")]
        cells += [_cell(r, p) for p in PHASES]
        cells.append(f"**{len(r['findings'])}**" if r.get("findings") else "none")
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def cmd_finish(args: argparse.Namespace) -> int:
    path = Path(args.results)
    result = _load(path)
    _finalize(result, args.expect or [])
    _save(path, result)
    thresholds = _load(Path(args.thresholds)) if args.thresholds else {}
    errors, warnings = check_thresholds(result, thresholds)
    failed = [p for p, v in result["phases"].items() if v.get("status") == FAIL]
    for w in warnings:
        print(f"::warning::{w}")
    for e in errors:
        print(f"::error::{e}")
    md = [f"### `{result['row']}`: {result.get('title', '')}", "", table([result]), ""]
    if result.get("notes"):
        md += [f"- {n}" for n in result["notes"]] + [""]
    if result.get("findings"):
        md += ["**Findings:**", ""] + [f"- {f}" for f in result["findings"]] + [""]
    if errors:
        md += ["**Over threshold:**", ""] + [f"- {e}" for e in errors] + [""]
    if warnings:
        md += [f"- {w}" for w in warnings] + [""]
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("\n".join(md) + "\n")
    print("\n".join(md))
    if failed:
        print(f"::error::{result['row']}: failed phase(s): {', '.join(failed)}")
    return 1 if failed or errors or result.get("findings") else 0


def cmd_summarize(args: argparse.Namespace) -> int:
    results = []
    for f in sorted(Path(args.dir).rglob("*.json")):
        try:
            data = _load(f)
        except (OSError, ValueError) as exc:
            print(f"::error::unreadable result file {f}: {exc}")
            return 1
        if isinstance(data, dict) and "row" in data and "phases" in data:
            results.append(data)
    if not results:
        print(
            f"::error::no row results under {args.dir}: every installer job failed before recording anything"
        )
        return 1
    thresholds = _load(Path(args.thresholds)) if args.thresholds else {}
    md = [
        f"## Installer journeys: {results[0].get('release', '?')}",
        "",
        "Each cell is the phase's result and how long it took. `n/a`: the row "
        "has no such phase. `skipped`: an earlier phase failed.",
        "",
        table(results),
        "",
    ]
    over: List[str] = []
    missing: List[str] = []
    for r in results:
        e, w = check_thresholds(r, thresholds)
        over += e
        missing += w
    findings = [f"`{r['row']}`: {f}" for r in results for f in r.get("findings", [])]
    if findings:
        md += ["### Findings", ""] + [f"- {f}" for f in findings] + [""]
    if over:
        md += ["### Over threshold", ""] + [f"- {e}" for e in over] + [""]
    if missing:
        md += ["### No threshold yet", ""] + [f"- {w}" for w in missing] + [""]
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("\n".join(md) + "\n")
    print("\n".join(md))
    if args.out:
        _save(Path(args.out), {"results": results})
    failed_rows = [
        r["row"]
        for r in results
        if any(v.get("status") == FAIL for v in r["phases"].values())
        or r.get("findings")
    ]
    if failed_rows or over:
        print(
            f"::error::rows failed: {', '.join(sorted(set(failed_rows)))}; over threshold: {len(over)}"
        )
        return 1
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init")
    p.add_argument("--results", required=True)
    p.add_argument("--row", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--os-label", required=True)
    p.add_argument("--installer", required=True)
    p.add_argument("--release", required=True)
    p.add_argument("--source", required=True)
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("phase")
    p.add_argument("--results", required=True)
    p.add_argument("--phase", required=True, choices=PHASES)
    p.add_argument("command", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_phase)

    p = sub.add_parser("bounded")
    p.add_argument("--seconds", type=float, required=True)
    p.add_argument("--stdout")
    p.add_argument("--stderr")
    p.add_argument("--elapsed-file")
    p.add_argument("command", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_bounded)

    p = sub.add_parser("skip")
    p.add_argument("--results", required=True)
    p.add_argument("--phase", required=True, choices=PHASES)
    p.add_argument("--reason", required=True)
    p.set_defaults(func=cmd_skip)

    p = sub.add_parser("finding")
    p.add_argument("--results", required=True)
    p.add_argument("--text", required=True)
    p.set_defaults(func=cmd_finding)

    p = sub.add_parser("note")
    p.add_argument("--results", required=True)
    p.add_argument("--text", required=True)
    p.set_defaults(func=cmd_note)

    p = sub.add_parser("finish")
    p.add_argument("--results", required=True)
    p.add_argument("--thresholds")
    p.add_argument("--expect", nargs="*", choices=PHASES, help="phases this row runs")
    p.set_defaults(func=cmd_finish)

    p = sub.add_parser("summarize")
    p.add_argument("--dir", required=True)
    p.add_argument("--thresholds")
    p.add_argument("--out")
    p.set_defaults(func=cmd_summarize)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
