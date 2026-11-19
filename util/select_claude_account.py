#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Pick the Claude subscription account a CI job runs on, by asking each one.

CI holds two subscription tokens: ``CLAUDE_CODE_OAUTH_TOKEN`` (primary) and
``CLAUDE_CODE_OAUTH_TOKEN_SECONDARY`` (backup). This probes the primary with the
smallest call the CLI can make. It uses the primary if it answers, and the
secondary if the primary is out of quota and the secondary answers. It fails if
both are out of quota, naming both reset times.

Only quota moves a job to the backup. A rejected credential, or a reply this
cannot classify, fails at once naming the secret to fix: running on the other
account would hide a token that needs rotating.

The tokens come from the environment (``CLAUDE_TOKEN_PRIMARY`` and
``CLAUDE_TOKEN_SECONDARY``), never argv, and are masked before anything runs.
The account is reported as a label (``primary``, ``secondary``, or ``none`` when
neither is set); the workflow picks the secret by that label, so no token is
ever written to an output. Used by ``.github/actions/claude-credential``.

stdlib only; "out of quota" means what ``util/classify_claude_probe.py`` says.
"""

from __future__ import annotations

import argparse
import functools
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

HERE = Path(__file__).resolve().parent

PRIMARY = "primary"
SECONDARY = "secondary"
NONE = "none"

SECRET_NAMES = {
    PRIMARY: "CLAUDE_CODE_OAUTH_TOKEN",
    SECONDARY: "CLAUDE_CODE_OAUTH_TOKEN_SECONDARY",
}
TOKEN_ENV = {PRIMARY: "CLAUDE_TOKEN_PRIMARY", SECONDARY: "CLAUDE_TOKEN_SECONDARY"}

PROBE_PROMPT = "Reply with exactly the word: ok"

EXIT_OK = 0
EXIT_QUOTA = 2
EXIT_CREDENTIAL = 3
EXIT_UNKNOWN = 4

# ASCII only (see classify_claude_probe.py); \u00b7 is the CLI's separator.
_RESET_TEXT = re.compile(
    r"\bresets?\s+(?:at\s+)?([^|\"\n\u00b7]{1,60})", re.IGNORECASE
)
_RESET_EPOCH = re.compile(r"limit reached\|(\d{9,11})\b", re.IGNORECASE)

# Probe children never see these: an API key makes the CLI ignore the token
# under test, and a second token has no business in the child.
_STRIPPED_ENV = (
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN_SECONDARY",
    "CLAUDE_CODE_OAUTH_TOKEN_BACKUP",
    *TOKEN_ENV.values(),
)


@functools.lru_cache(maxsize=1)
def _classifier():
    spec = importlib.util.spec_from_file_location(
        "classify_claude_probe", HERE / "classify_claude_probe.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class Probe:
    """What one account said: ``ok``, ``quota``, ``credential`` or ``unknown``."""

    kind: str
    detail: str = ""
    resets: Optional[str] = None


@dataclass
class Selection:
    """The account to use, or why there is none, ready to print."""

    account: str
    verdict: str
    exit_code: int
    commands: list[str] = field(default_factory=list)
    summary: str = ""
    probes: dict[str, Probe] = field(default_factory=dict)


def reset_time(text: str) -> Optional[str]:
    """The reset the CLI quoted, e.g. ``3pm (UTC)``; ``None`` when it named none."""
    epoch = _RESET_EPOCH.search(text)
    if epoch:
        when = datetime.fromtimestamp(int(epoch.group(1)), tz=timezone.utc)
        return when.strftime("%Y-%m-%d %H:%M UTC")
    match = _RESET_TEXT.search(text)
    if match:
        return match.group(1).strip().rstrip(".,;")
    return None


def classify_reply(stdout: str, stderr: str, returncode: int) -> Probe:
    """Turn one ``claude -p --output-format json`` run into a :class:`Probe`."""
    classifier = _classifier()
    try:
        reply = classifier.extract_reply(stdout, "stream-json")
    except ValueError:
        reply = stdout
    if returncode != 0 and stderr:
        reply = f"{reply}\n{stderr}"
    verdict = classifier.classify(reply, returncode, always_scan=True)
    excerpt = " ".join(reply.split())[:300] or f"exit {returncode}, no output"
    if verdict.kind == "ok":
        if "ok" in reply.lower():
            return Probe("ok", excerpt)
        return Probe(
            "unknown",
            f"the CLI exited {returncode} but never replied 'ok': {excerpt}",
        )
    if verdict.kind == "quota":
        return Probe("quota", excerpt, reset_time(reply))
    if verdict.kind == "credential":
        return Probe("credential", excerpt)
    return Probe("unknown", excerpt)


def run_probe(token: str, model: str, claude_bin: str, timeout: float) -> Probe:
    """Ask one account for a one-word reply through the CLI the jobs use."""
    env = {k: v for k, v in os.environ.items() if k not in _STRIPPED_ENV}
    env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    cmd = [
        claude_bin,
        "-p",
        PROBE_PROMPT,
        "--model",
        model,
        "--max-turns",
        "1",
        "--output-format",
        "json",
        "--no-session-persistence",
    ]
    try:
        proc = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return Probe("unknown", f"no reply within {timeout:.0f}s")
    except OSError as exc:
        return Probe("unknown", f"could not run {claude_bin}: {exc}")
    return classify_reply(proc.stdout or "", proc.stderr or "", proc.returncode)


def _resets(probe: Probe) -> str:
    return probe.resets or "reset time not stated"


def select(
    configured: dict[str, bool],
    probe: Callable[[str], Probe],
    what: str = "This job",
    quota_note: str = "",
) -> Selection:
    """Decide the account. *probe* is called with ``primary`` or ``secondary``."""
    if not any(configured.values()):
        notice = (
            "::notice title=Claude account::Neither CLAUDE_CODE_OAUTH_TOKEN nor "
            f"CLAUDE_CODE_OAUTH_TOKEN_SECONDARY is set, so {what} uses "
            "ANTHROPIC_API_KEY if that is set."
        )
        return Selection(NONE, "none", EXIT_OK, [notice])

    commands: list[str] = []
    probes: dict[str, Probe] = {}
    if not configured.get(PRIMARY):
        commands.append(
            "::warning title=Claude account::CLAUDE_CODE_OAUTH_TOKEN is not set, so "
            f"{what} can only use the secondary account."
        )

    for account in (PRIMARY, SECONDARY):
        if not configured.get(account):
            continue
        result = probe(account)
        probes[account] = result
        secret = SECRET_NAMES[account]
        if result.kind == "ok":
            primary = probes.get(PRIMARY)
            summary = ""
            if account == SECONDARY and primary is not None:
                commands.append(
                    "::warning title=Claude account: using the backup::The primary "
                    f"Claude account is out of quota ({_resets(primary)}), so {what} "
                    "uses the secondary account (CLAUDE_CODE_OAUTH_TOKEN_SECONDARY)."
                )
                summary = (
                    f"### Claude account: secondary\n\n{what} runs on the secondary "
                    f"account because the primary is out of quota "
                    f"({_resets(primary)}).\n"
                )
            commands.append(
                f"::notice title=Claude account::{what} uses the {account} Claude "
                f"account ({secret})."
            )
            return Selection(account, "ok", EXIT_OK, commands, summary, probes)

        if result.kind == "quota":
            continue

        if result.kind == "credential":
            headline = f"the {account} account's token was rejected"
            cause = (
                f"Claude rejected {secret}: {result.detail}. A rejected token is never "
                "worked around with the other account."
            )
            fix = (
                f"Run `claude setup-token` signed in to that account, then "
                f"`gh secret set {secret} --repo amd/gaia`."
            )
            return _failure(
                what, "credential", EXIT_CREDENTIAL, headline, cause, fix, probes
            )

        headline = f"Claude did not answer the {account} account's probe"
        cause = (
            f"The probe of {secret} was neither answered nor refused for quota: "
            f"{result.detail}. Only a quota refusal moves a job to the other account."
        )
        fix = (
            "Read the probe output above. If this is a new refusal wording, add it to "
            "util/classify_claude_probe.py."
        )
        return _failure(what, "unknown", EXIT_UNKNOWN, headline, cause, fix, probes)

    primary = probes.get(PRIMARY)
    secondary = probes.get(SECONDARY)
    if primary is not None and secondary is not None:
        headline = "both Claude accounts are out of quota"
        cause = (
            f"The primary account resets {_resets(primary)}; the secondary resets "
            f"{_resets(secondary)}."
        )
        fix = "Re-run after the earlier reset."
    elif primary is not None:
        headline = "the primary Claude account is out of quota and there is no backup"
        cause = (
            f"The primary account resets {_resets(primary)}, and "
            "CLAUDE_CODE_OAUTH_TOKEN_SECONDARY is not set."
        )
        fix = (
            "Re-run after the reset, or add a second account: `claude setup-token` "
            "signed in to it, then `gh secret set CLAUDE_CODE_OAUTH_TOKEN_SECONDARY "
            "--repo amd/gaia`."
        )
    else:
        headline = "the secondary Claude account is out of quota and there is no primary"
        cause = (
            f"The secondary account resets {_resets(secondary)}, and "
            "CLAUDE_CODE_OAUTH_TOKEN is not set."
        )
        fix = "Re-run after the reset, or set CLAUDE_CODE_OAUTH_TOKEN."
    if quota_note:
        fix = f"{fix} {quota_note}"
    return _failure(what, "quota", EXIT_QUOTA, headline, cause, fix, probes)


def _failure(what, verdict, code, headline, cause, fix, probes) -> Selection:
    error = (
        f"::error title=Claude unavailable::{what} did not start: {headline}. "
        f"{cause} WHAT TO DO: {fix} WHERE TO LOOK: docs/reference/claude-ci-workflows.md"
    )
    summary = f"## {what} did not start: {headline}\n\n{cause}\n\n{fix}\n"
    return Selection("", verdict, code, [error], summary, probes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--model", default=os.environ.get("CLAUDE_PROBE_MODEL") or "claude-opus-5-5"
    )
    parser.add_argument(
        "--what",
        default=os.environ.get("CLAUDE_PROBE_WHAT") or "This job",
        help="Names the job in every message, e.g. \"Tonight's code audit\".",
    )
    parser.add_argument(
        "--quota-note",
        default=os.environ.get("CLAUDE_PROBE_QUOTA_NOTE") or "",
        help="Added to the message when every account is out of quota.",
    )
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--summary", type=Path, help="$GITHUB_STEP_SUMMARY")
    parser.add_argument("--github-output", type=Path, help="$GITHUB_OUTPUT")
    args = parser.parse_args(argv)

    tokens = {acct: os.environ.get(var, "").strip() for acct, var in TOKEN_ENV.items()}
    for token in tokens.values():
        if token:
            print(f"::add-mask::{token}", flush=True)

    claude_bin = shutil.which("claude")
    if any(tokens.values()) and claude_bin is None:
        print(
            "::error title=Claude CLI missing::The `claude` CLI is not on PATH, so no "
            "account can be probed. WHAT TO DO: install it before this step (npm "
            "install -g @anthropic-ai/claude-code). WHERE TO LOOK: "
            ".github/actions/claude-credential/action.yml"
        )
        return EXIT_UNKNOWN

    def probe(account: str) -> Probe:
        print(f"Probing the {account} Claude account on {args.model} ...", flush=True)
        result = run_probe(tokens[account], args.model, claude_bin, args.timeout)
        print(f"  {account}: {result.kind} ({result.detail[:200]})", flush=True)
        return result

    selection = select(
        {acct: bool(tok) for acct, tok in tokens.items()},
        probe,
        what=args.what,
        quota_note=args.quota_note,
    )
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as fh:
            fh.write(f"account={selection.account}\n")
            fh.write(f"verdict={selection.verdict}\n")
    if args.summary and selection.summary:
        with args.summary.open("a", encoding="utf-8") as fh:
            fh.write(selection.summary + "\n")
    for line in selection.commands:
        print(line, flush=True)
    return selection.exit_code


if __name__ == "__main__":
    sys.exit(main())
