#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Run, time and record the phases of an installer journey, and gate them.

Used by .github/workflows/test_installers_headless.yml. Each installer x OS row
calls ``run`` once: it runs the row's phases in order, times each with the
system-wide monotonic clock, records pass/fail in one results file per row, and
stops at the first failure. ``summarize`` builds the table for the whole run
from every row's file.

A row fails when a phase fails, when it records a finding (a defect that does
not stop the journey, e.g. a documented command that is missing), or when a
phase runs past its threshold in installer/install_time_thresholds.json.

A failure that is a filed, open bug is listed in installer/headless/
known_failures.json with the issue and an output signature. It shows as "known
#N" instead of failing the row, and the row fails again the moment the bug stops
reproducing, so the entry cannot outlive the fix.

Stdlib only: it runs on stock GitHub-hosted runners before anything is installed.

    python journey.py init      --results r.json --row ROW --title ... --os-label ... --installer ... --release ... --source ...
    python journey.py run       --results r.json --phases "download install ..." --known K --thresholds T -- <command with {phase}>
    python journey.py finding   --results r.json --phase install --text "..."
    python journey.py summarize --dir results/ --known K --thresholds T --out install-timings.json
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# The columns of the summary table, in journey order.
PHASES = [
    "download",
    "install",
    "upgrade",
    "launch",
    "health",
    "preflight",
    "quit",
    "uninstall",
    "leftovers",
]

# Timed metrics the thresholds file can gate. ``launch_to_healthy`` is derived:
# from starting the app to its health check answering is launch + health, which
# is what a user waits through on first launch.
METRICS = ["download", "install", "launch_to_healthy", "upgrade", "uninstall"]

REPO_BLOB = "https://github.com/amd/gaia/blob/main/installer"
THRESHOLDS_URL = f"{REPO_BLOB}/install_time_thresholds.json"
KNOWN_URL = f"{REPO_BLOB}/headless/known_failures.json"

PASS, FAIL, KNOWN, NA, SKIPPED = "pass", "fail", "known", "n/a", "skipped"

# How much of a phase's output is kept for matching known-failure signatures.
OUTPUT_KEEP = 2_000_000


def _load(path: Path) -> Dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _save(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _optional(path: Optional[str]) -> Dict[str, Any]:
    return _load(Path(path)) if path else {}


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


# ── known failures ───────────────────────────────────────────────────────────


def known_entries(known: Dict[str, Any], row: str, phase: str) -> List[Dict[str, Any]]:
    return [
        e
        for e in known.get("entries", [])
        if row in e.get("rows", []) and e.get("phase") == phase
    ]


def match_known(
    known: Dict[str, Any], row: str, phase: str, text: str, kind: str
) -> Optional[Dict[str, Any]]:
    """The entry that explains this failure (kind 'failure') or finding."""
    for e in known_entries(known, row, phase):
        if e.get("kind", "failure") == kind and e["signature"] in text:
            return e
    return None


def stale_known(result: Dict[str, Any], known: Dict[str, Any]) -> List[str]:
    """Known entries whose phase ran here without the bug showing up."""
    errors = []
    observed = {(k["issue"], k["phase"]) for k in result.get("known", [])}
    for phase, p in result.get("phases", {}).items():
        if p.get("status") not in (PASS, KNOWN):
            continue
        for e in known_entries(known, result["row"], phase):
            if (e["issue"], phase) not in observed:
                errors.append(
                    f"{result['row']} / {phase}: known bug #{e['issue']} no longer "
                    f"reproduces ('{e['signature']}' not seen). If it is fixed, remove "
                    f"its entry from {KNOWN_URL}"
                )
    return errors


# ── threshold gate ───────────────────────────────────────────────────────────


def check_thresholds(
    result: Dict[str, Any], thresholds: Dict[str, Any]
) -> Tuple[List[str], List[str]]:
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


# ── running a phase ──────────────────────────────────────────────────────────


def _stream(command: List[str], log: Optional[Path]) -> Tuple[int, str]:
    """Run ``command``, echoing its output live and keeping a copy.

    A background process the phase started (the desktop app, a fake server) may
    hold the output pipe open after the phase itself exits. Reading is therefore
    given a short grace after exit and then abandoned, rather than waiting for
    that process to end.
    """
    kept: List[str] = []
    size = 0
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    log_fh = log.open("a", encoding="utf-8") if log else None

    def pump() -> None:
        nonlocal size
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            if log_fh:
                log_fh.write(line)
            if size < OUTPUT_KEEP:
                kept.append(line)
                size += len(line)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    code = proc.wait()
    reader.join(timeout=10)
    if log_fh:
        log_fh.close()
    return code, "".join(kept)


def run_phase(
    path: Path,
    phase: str,
    command: List[str],
    known: Dict[str, Any],
    logs: Optional[Path],
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Run one phase and record it. Returns (status, known entry or None)."""
    data = _load(path)
    print(f"::group::{phase}: {' '.join(command)}", flush=True)
    # time.monotonic is the system-wide monotonic clock on all three runner
    # OSes, so a wall-clock jump mid-install cannot bend the measurement.
    start = time.monotonic()
    try:
        code, output = _stream(command, logs / f"{phase}.log" if logs else None)
    except OSError as exc:
        print(f"could not start {command[0]}: {exc}", flush=True)
        code, output = 127, str(exc)
    seconds = round(time.monotonic() - start, 1)
    print("::endgroup::", flush=True)

    entry = None
    if code == 0:
        status = PASS
    else:
        entry = match_known(known, data["row"], phase, output, "failure")
        status = KNOWN if entry else FAIL
    data = _load(path)  # the phase may have recorded findings meanwhile
    data["phases"][phase] = {"status": status, "seconds": seconds, "exit_code": code}
    if entry:
        data["phases"][phase]["issue"] = entry["issue"]
        data.setdefault("known", []).append({"issue": entry["issue"], "phase": phase})
    _save(path, data)

    print(f"{phase}: {status} in {seconds}s (exit {code})", flush=True)
    if status == FAIL:
        print(f"::error::{data['row']}: {phase} failed (exit {code})")
    elif status == KNOWN:
        print(
            f"::warning::{data['row']}: {phase} failed with known bug #{entry['issue']}"
        )
    return status, entry


def _cell(result: Dict[str, Any], phase: str) -> str:
    p = result.get("phases", {}).get(phase)
    if p is None:
        return NA
    status = p.get("status", "?")
    secs = f" {p['seconds']:.0f}s" if p.get("seconds") is not None else ""
    if status == PASS:
        return f"pass{secs}"
    if status == FAIL:
        return f"**FAIL**{secs}"
    if status == KNOWN:
        return f"known #{p.get('issue')}{secs}"
    if status == SKIPPED and p.get("issue"):
        return f"skipped (#{p['issue']})"
    return status


def table(results: List[Dict[str, Any]]) -> str:
    head = ["Row", "OS", "Source"] + [p.capitalize() for p in PHASES] + ["Findings"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in sorted(results, key=lambda r: r["row"]):
        cells = [f"`{r['row']}`", r.get("os", ""), r.get("source", "")]
        cells += [_cell(r, p) for p in PHASES]
        found = [f"**{len(r['findings'])} new**"] if r.get("findings") else []
        found += [f"known #{k['issue']}" for k in r.get("known_findings", [])]
        cells.append(", ".join(found) or "none")
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def verdict(
    result: Dict[str, Any], thresholds: Dict[str, Any], known: Dict[str, Any]
) -> Tuple[List[str], List[str]]:
    """(errors, warnings) for one finished row."""
    errors, warnings = check_thresholds(result, thresholds)
    failed = [p for p, v in result["phases"].items() if v.get("status") == FAIL]
    if failed:
        errors.append(f"{result['row']}: failed phase(s): {', '.join(failed)}")
    errors += [f"{result['row']}: {f}" for f in result.get("findings", [])]
    errors += stale_known(result, known)
    return errors, warnings


def _summary(md: List[str]) -> None:
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("\n".join(md) + "\n")
    print("\n".join(md))


def finish(
    path: Path, expected: List[str], thresholds: Dict[str, Any], known: Dict[str, Any]
) -> int:
    result = _load(path)
    for phase in expected:
        result["phases"].setdefault(
            phase, {"status": SKIPPED, "reason": "an earlier phase failed"}
        )
    _save(path, result)
    errors, warnings = verdict(result, thresholds, known)
    for w in warnings:
        print(f"::warning::{w}")
    for e in errors:
        print(f"::error::{e}")
    md = [f"### `{result['row']}`: {result.get('title', '')}", "", table([result]), ""]
    md += [f"- {n}" for n in result.get("notes", [])]
    md += [
        f"- Known bug #{k['issue']}: {k['text']}"
        for k in result.get("known_findings", [])
    ]
    md += [f"- **{e}**" for e in errors] + [f"- {w}" for w in warnings] + [""]
    _summary(md)
    return 1 if errors else 0


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


def cmd_run(args: argparse.Namespace) -> int:
    """Run a row's phases in order; stop at the first failure; then finish."""
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command or not any("{phase}" in c for c in command):
        print("journey.py run: the command must contain {phase}", file=sys.stderr)
        return 2
    path = Path(args.results)
    phases = args.phases.split()
    unknown = [p for p in phases if p not in PHASES]
    if unknown:
        print(f"journey.py run: unknown phase(s) {unknown}", file=sys.stderr)
        return 2
    skips = dict(s.split("=", 1) for s in args.skip)
    known = _optional(args.known)
    logs = Path(args.logs) if args.logs else None
    if logs:
        logs.mkdir(parents=True, exist_ok=True)

    for i, phase in enumerate(phases):
        if phase in skips:
            data = _load(path)
            data["phases"][phase] = {"status": NA, "reason": skips[phase]}
            _save(path, data)
            continue
        status, entry = run_phase(
            path, phase, [c.replace("{phase}", phase) for c in command], known, logs
        )
        if status == FAIL:
            break
        if status == KNOWN and entry.get("blocks", False):
            data = _load(path)
            for rest in phases[i + 1 :]:
                data["phases"][rest] = {
                    "status": SKIPPED,
                    "issue": entry["issue"],
                    "reason": f"blocked by known bug #{entry['issue']}",
                }
            _save(path, data)
            break
    return finish(path, phases, _optional(args.thresholds), known)


def cmd_finding(args: argparse.Namespace) -> int:
    """Record a defect that does not stop the journey; it fails the row.

    For a contract the installer breaks without blocking the rest (a documented
    command missing, a package name that collides), so one bug does not hide
    whether everything after it works.
    """
    path = Path(args.results)
    data = _load(path)
    entry = match_known(
        _optional(args.known), data["row"], args.phase, args.text, "finding"
    )
    if entry:
        data.setdefault("known_findings", []).append(
            {"issue": entry["issue"], "text": args.text}
        )
        data.setdefault("known", []).append(
            {"issue": entry["issue"], "phase": args.phase}
        )
        print(f"::warning::{data['row']}: known bug #{entry['issue']}: {args.text}")
    else:
        data.setdefault("findings", []).append(args.text)
        print(f"::error::{data['row']}: {args.text}")
    _save(path, data)
    return 0


def cmd_note(args: argparse.Namespace) -> int:
    path = Path(args.results)
    data = _load(path)
    data["notes"].append(args.text)
    _save(path, data)
    return 0


def cmd_finish(args: argparse.Namespace) -> int:
    return finish(
        Path(args.results),
        args.expect or [],
        _optional(args.thresholds),
        _optional(args.known),
    )


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
    proc = subprocess.Popen(
        command, stdout=out, stderr=err, stdin=subprocess.DEVNULL, **kwargs
    )
    try:
        code = proc.wait(timeout=args.seconds)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.call(["taskkill", "/T", "/F", "/PID", str(proc.pid)])
        else:
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
    thresholds, known = _optional(args.thresholds), _optional(args.known)
    errors: List[str] = []
    warnings: List[str] = []
    for r in results:
        e, w = verdict(r, thresholds, known)
        errors += e
        warnings += w
    issues = sorted({k["issue"] for r in results for k in r.get("known", [])})
    md = [
        f"## Installer journeys: {results[0].get('release', '?')}",
        "",
        "Each cell is the phase's result and how long it took. `known #N`: a filed "
        "bug, listed in installer/headless/known_failures.json. `n/a`: the row has "
        "no such phase. `skipped`: an earlier phase failed.",
        "",
        table(results),
        "",
    ]
    if issues:
        md += ["Known bugs hit: " + ", ".join(f"#{i}" for i in issues), ""]
    if errors:
        md += ["### Failures", ""] + [f"- {e}" for e in errors] + [""]
    if warnings:
        md += ["### No threshold yet", ""] + [f"- {w}" for w in warnings] + [""]
    _summary(md)
    if args.out:
        _save(Path(args.out), {"results": results})
    return 1 if errors else 0


def main(argv: Optional[List[str]] = None) -> int:
    # A Windows runner's console is cp1252; an echoed ❌ must not kill the relay.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="replace")
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

    p = sub.add_parser("run")
    p.add_argument("--results", required=True)
    p.add_argument("--phases", required=True, help="space-separated, in journey order")
    p.add_argument("--skip", action="append", default=[], metavar="PHASE=REASON")
    p.add_argument("--known")
    p.add_argument("--thresholds")
    p.add_argument("--logs", help="directory for one <phase>.log per phase")
    p.add_argument("command", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("finding")
    p.add_argument("--results", required=True)
    p.add_argument("--phase", required=True, choices=PHASES)
    p.add_argument("--text", required=True)
    p.add_argument("--known")
    p.set_defaults(func=cmd_finding)

    p = sub.add_parser("note")
    p.add_argument("--results", required=True)
    p.add_argument("--text", required=True)
    p.set_defaults(func=cmd_note)

    p = sub.add_parser("finish")
    p.add_argument("--results", required=True)
    p.add_argument("--thresholds")
    p.add_argument("--known")
    p.add_argument("--expect", nargs="*", choices=PHASES, help="phases this row runs")
    p.set_defaults(func=cmd_finish)

    p = sub.add_parser("bounded")
    p.add_argument("--seconds", type=float, required=True)
    p.add_argument("--stdout")
    p.add_argument("--stderr")
    p.add_argument("--elapsed-file")
    p.add_argument("command", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_bounded)

    p = sub.add_parser("summarize")
    p.add_argument("--dir", required=True)
    p.add_argument("--thresholds")
    p.add_argument("--known")
    p.add_argument("--out")
    p.set_defaults(func=cmd_summarize)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
