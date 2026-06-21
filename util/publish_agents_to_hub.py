# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Batch pack-and-publish every GAIA hub agent to an Agent Hub instance.

Discovers the publishable set from ``util/list_agent_packages.py`` (the single
source of truth: ``setup.py[agents]``), then for each agent runs the real CLI
— ``gaia agent pack`` followed by ``gaia agent publish --skip-pypi --hub-url
<url>`` — exactly as a human publisher would (per the repo's testing
philosophy: exercise the CLI, never bypass it).

Usage::

    # Publish all 14 agents to a local worker
    GAIA_HUB_TOKEN=dev-token python util/publish_agents_to_hub.py \\
        --hub-url http://localhost:8788

    # Subset, tolerating already-published versions
    python util/publish_agents_to_hub.py --hub-url http://localhost:8788 \\
        --agents hello-world word-count --skip-existing

    # Pack only, show what would be published
    python util/publish_agents_to_hub.py --hub-url http://localhost:8788 --dry-run

The Hub token resolves exactly as ``gaia agent publish`` resolves it
(``GAIA_HUB_TOKEN`` env var, then the OS keyring via ``gaia agent login``).
Per ``CLAUDE.md`` (No Silent Fallbacks): a missing token, a failed pack, or a
rejected publish is a loud per-agent failure and a non-zero exit. The only
tolerated rejection is the Worker's 409 version-already-exists — and only when
``--skip-existing`` is passed explicitly.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

UTIL_DIR = Path(__file__).resolve().parent
if str(UTIL_DIR) not in sys.path:
    sys.path.insert(0, str(UTIL_DIR))

from list_agent_packages import (  # noqa: E402  (path set above)
    AgentListError,
    AgentPackage,
    list_agent_packages,
)

# The publisher's 409 message (gaia.hub.publisher._publish_r2). --skip-existing
# matches on this to distinguish "version already published" from real failures.
VERSION_EXISTS_MARKER = "already exists on the Hub (409)"

HUB_URL_ENV = "GAIA_HUB_URL"

# Statuses for the summary table.
STATUS_PUBLISHED = "published"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"
STATUS_PACKED = "packed (dry-run)"

# runner(cmd) -> (returncode, combined stdout+stderr)
Runner = Callable[[List[str]], Tuple[int, str]]


class PipelineError(Exception):
    """Raised when the pipeline cannot start (bad args, missing token/CLI)."""


@dataclass
class AgentOutcome:
    """Per-agent result for the summary table."""

    agent_id: str
    status: str
    detail: str = ""


def _default_runner(cmd: List[str]) -> Tuple[int, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _gaia_cli() -> str:
    """Locate the ``gaia`` console script; fail loudly if it is not installed."""
    path = shutil.which("gaia")
    if not path:
        raise PipelineError(
            "the 'gaia' CLI is not on PATH. Install the repo first "
            "('uv pip install -e \".[dev]\"') so 'gaia agent pack/publish' "
            "can run."
        )
    return path


def _require_hub_token() -> None:
    """Fail upfront (before any pack) if no Hub publish token resolves.

    Reuses the publisher's exact resolution (env var, then keyring) so this
    script and ``gaia agent publish`` always agree on where tokens come from.
    """
    from gaia.hub import publisher

    if publisher.get_hub_token() is None:
        raise PipelineError(
            "no Hub publish token found. Run 'gaia agent login --hub-token "
            f"<token>' to store one, or set {publisher.HUB_TOKEN_ENV}. The "
            "Worker rejects anonymous publishes with 401."
        )


def select_agents(
    packages: Sequence[AgentPackage], requested: Optional[Sequence[str]]
) -> List[AgentPackage]:
    """Return the packages to publish, validating any ``--agents`` subset."""
    if not requested:
        return list(packages)
    by_id = {p.agent_id: p for p in packages}
    unknown = [a for a in requested if a not in by_id]
    if unknown:
        raise PipelineError(
            f"unknown agent id(s): {', '.join(unknown)}. Known publishable "
            f"agents (from setup.py[agents]): {', '.join(sorted(by_id))}."
        )
    return [by_id[a] for a in requested]


def _tail(output: str, lines: int = 6) -> str:
    """Last few non-empty lines of a CLI's output, for failure details."""
    kept = [ln.strip() for ln in output.strip().splitlines() if ln.strip()]
    return " | ".join(kept[-lines:])


def process_agent(
    pkg: AgentPackage,
    *,
    gaia: str,
    hub_url: str,
    skip_existing: bool,
    dry_run: bool,
    runner: Runner,
) -> AgentOutcome:
    """Pack (always) then publish (unless ``--dry-run``) a single agent."""
    print(f"==> {pkg.agent_id}: packing ({pkg.rel_path})")
    rc, out = runner([gaia, "agent", "pack", str(pkg.path)])
    if rc != 0:
        print(f"    pack FAILED (exit {rc})")
        return AgentOutcome(pkg.agent_id, STATUS_FAILED, f"pack failed: {_tail(out)}")

    if dry_run:
        print("    packed; would publish (dry-run)")
        return AgentOutcome(pkg.agent_id, STATUS_PACKED, f"would publish to {hub_url}")

    print(f"    publishing to {hub_url}")
    rc, out = runner(
        [
            gaia,
            "agent",
            "publish",
            str(pkg.path),
            "--hub-url",
            hub_url,
            "--skip-pypi",
        ]
    )
    if rc == 0:
        print("    published")
        return AgentOutcome(pkg.agent_id, STATUS_PUBLISHED, f"published to {hub_url}")

    if VERSION_EXISTS_MARKER in out:
        if skip_existing:
            print("    skipped: this version is already on the Hub (409)")
            return AgentOutcome(
                pkg.agent_id,
                STATUS_SKIPPED,
                "version already published (409); skipped via --skip-existing",
            )
        print("    FAILED: version already on the Hub (409)")
        return AgentOutcome(
            pkg.agent_id,
            STATUS_FAILED,
            "version already published (409). Bump with 'gaia agent version "
            "<patch|minor|major>' or re-run with --skip-existing.",
        )

    print(f"    publish FAILED (exit {rc})")
    return AgentOutcome(pkg.agent_id, STATUS_FAILED, f"publish failed: {_tail(out)}")


def run_pipeline(args: argparse.Namespace, runner: Runner) -> List[AgentOutcome]:
    """Run pack+publish across the selected agents; never raises per-agent."""
    packages = select_agents(list_agent_packages(), args.agents)
    gaia = _gaia_cli()
    if not args.dry_run:
        _require_hub_token()

    print(f"Publishing {len(packages)} agent(s) to {args.hub_url}")
    return [
        process_agent(
            pkg,
            gaia=gaia,
            hub_url=args.hub_url,
            skip_existing=args.skip_existing,
            dry_run=args.dry_run,
            runner=runner,
        )
        for pkg in packages
    ]


def print_summary(outcomes: Sequence[AgentOutcome]) -> None:
    width = max(len(o.agent_id) for o in outcomes)
    print("\n" + "=" * 72)
    print(f"{'AGENT'.ljust(width)}  {'STATUS'.ljust(16)}  DETAIL")
    for o in outcomes:
        print(f"{o.agent_id.ljust(width)}  {o.status.ljust(16)}  {o.detail}")
    counts = {
        s: sum(1 for o in outcomes if o.status == s)
        for s in (STATUS_PUBLISHED, STATUS_SKIPPED, STATUS_FAILED, STATUS_PACKED)
    }
    parts = [f"{n} {s}" for s, n in counts.items() if n]
    print("-" * 72)
    print("Total: " + ", ".join(parts))


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pack and publish all GAIA hub agents to an Agent Hub "
        "instance (the worker's POST /publish).",
    )
    parser.add_argument(
        "--hub-url",
        default=os.environ.get(HUB_URL_ENV),
        help=f"Agent Hub origin, e.g. http://localhost:8788 (default: ${HUB_URL_ENV})",
    )
    parser.add_argument(
        "--agents",
        nargs="+",
        default=None,
        metavar="ID",
        help="Publish only these agent ids (default: every agent in "
        "setup.py[agents])",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Treat the Hub's 409 version-already-exists as a skip instead of "
        "a failure",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Pack only; list what would be published without uploading",
    )
    args = parser.parse_args(argv)
    if not args.hub_url:
        parser.error(
            f"--hub-url is required (or set {HUB_URL_ENV}). Example: "
            "--hub-url http://localhost:8788"
        )
    return args


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        outcomes = run_pipeline(args, runner=_default_runner)
    except (PipelineError, AgentListError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print_summary(outcomes)
    failed = [o for o in outcomes if o.status == STATUS_FAILED]
    if failed:
        print(
            f"\nerror: {len(failed)} agent(s) failed: "
            + ", ".join(o.agent_id for o in failed),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
