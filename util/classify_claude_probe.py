# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Classify a Claude CLI probe response so CI names the right owner.

A rejected probe has two very different causes that need two different people:

* the org behind the credential is out of budget (402/429-class) - a billing
  admin has to raise the limit, and no secrets change helps;
* the credential is absent, expired, or malformed (401-class) - a secrets
  change fixes it, and no billing change helps.

Both used to print the same line, so #4080 sent people to check secrets while
the real cause was spend. This module is the shared discriminator: the eval
preflight and the auth canary both call it so the two surfaces cannot drift.

There is deliberately no "probably fine" outcome. A response that matches
neither class, or matches both, fails with the raw text and says it could not
be classified - a guard that swallows the distinction it exists to draw is the
defect, not the fix.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field

# Matched case-insensitively against the probe response. Every pattern must stay
# ASCII: the real rejection text carries U+00B7 separators, and this file is
# referenced from a Windows PowerShell step where PS 5.1 reads a BOM-less file as
# ANSI. Match around such characters, never on them.
QUOTA_PATTERNS: tuple[str, ...] = (
    "spend limit",
    "usage limit",
    "rate limit",
    "rate_limit_error",
    "limit resets",
    "quota",
    "credit balance",
    "out of credit",
    "insufficient credit",
    "insufficient_quota",
    "billing",
    "upgrade your plan",
    "admin-settings/usage",
)

CREDENTIAL_PATTERNS: tuple[str, ...] = (
    "authentication_error",
    "authentication failed",
    "invalid api key",
    "invalid x-api-key",
    "invalid bearer token",
    "invalid_token",
    "unauthorized",
    "oauth token",
    "token has expired",
    "please run /login",
    "not logged in",
    "no credential",
)

# Bare status codes need a word boundary: a token count or a session id can
# contain the digits, and a substring match on "402" would misread it.
QUOTA_CODES: tuple[str, ...] = ("402", "429")
CREDENTIAL_CODES: tuple[str, ...] = ("401", "403")

EXIT_OK = 0
EXIT_QUOTA = 2
EXIT_CREDENTIAL = 3
EXIT_UNCLASSIFIED = 4
EXIT_MISSING_REPLY = 5


@dataclass
class Verdict:
    """The classification plus the three things an actionable error names."""

    kind: str  # ok | quota | credential | unclassified
    exit_code: int
    summary: str
    action: str
    where: str
    matched: list[str] = field(default_factory=list)

    def message(self) -> str:
        parts = [
            self.summary,
            f"WHAT TO DO: {self.action}",
            f"WHERE TO LOOK: {self.where}",
        ]
        if self.matched:
            parts.append("MATCHED: " + ", ".join(self.matched))
        return " | ".join(parts)


def _hits(text: str, patterns: tuple[str, ...], codes: tuple[str, ...]) -> list[str]:
    lowered = text.lower()
    found = [p for p in patterns if p in lowered]
    found += [c for c in codes if re.search(rf"(?<!\d){c}(?!\d)", lowered)]
    return found


def classify(text: str, exit_code: int, always_scan: bool = False) -> Verdict:
    """Decide what a probe response means.

    On ``exit_code == 0`` with ``always_scan`` off the text is never inspected,
    so a working credential can never be failed by a pattern that happens to
    appear in a successful reply. ``always_scan`` is the auth canary's mode,
    where the whole job is to tell "the token parses" from "CI can call Claude"
    and the body is checked even when the runner reports success.
    """
    if exit_code == 0 and not always_scan:
        return Verdict(
            kind="ok",
            exit_code=EXIT_OK,
            summary="Claude credential accepted by a live probe.",
            action="Nothing - continue.",
            where="n/a",
        )

    quota = _hits(text, QUOTA_PATTERNS, QUOTA_CODES)
    credential = _hits(text, CREDENTIAL_PATTERNS, CREDENTIAL_CODES)

    if exit_code == 0 and not quota and not credential:
        return Verdict(
            kind="ok",
            exit_code=EXIT_OK,
            summary=(
                "Claude credential accepted by a live probe, and the response body "
                "carries no rejection signature."
            ),
            action="Nothing - continue.",
            where="n/a",
        )

    excerpt = " ".join(text.split())[:400] or "(empty response)"

    if quota and credential:
        return Verdict(
            kind="unclassified",
            exit_code=EXIT_UNCLASSIFIED,
            summary=(
                "The Claude probe was rejected and the response matches BOTH the quota "
                "and the credential signature, so the cause is ambiguous. "
                f"Response: {excerpt}"
            ),
            action=(
                "Read the full response before acting: raising the org spend limit and "
                "rotating the credential are different fixes by different people. Then "
                "add the distinguishing phrase to the right list in "
                "util/classify_claude_probe.py."
            ),
            where="util/classify_claude_probe.py and the failing step's log",
            matched=sorted(set(quota + credential)),
        )

    if quota:
        return Verdict(
            kind="quota",
            exit_code=EXIT_QUOTA,
            summary=(
                "QUOTA EXHAUSTED (402/429-class): the Claude credential is VALID but the "
                "org behind it is out of budget, so every scenario would error. "
                f"Response: {excerpt}"
            ),
            action=(
                "Ask an Anthropic org admin to raise the monthly spend limit at "
                "claude.ai/admin-settings/usage, or wait for the stated reset. Do NOT "
                "rotate the repository secrets - that will not help and the current ones "
                "are fine."
            ),
            where="https://claude.ai/admin-settings/usage and amd/gaia issue #4080",
            matched=sorted(set(quota)),
        )

    if credential:
        return Verdict(
            kind="credential",
            exit_code=EXIT_CREDENTIAL,
            summary=(
                "CREDENTIAL INVALID (401-class): the Claude credential is absent, expired, "
                f"or malformed, so every scenario would error. Response: {excerpt}"
            ),
            action=(
                "Run `claude setup-token` locally and update the repository secret with "
                "`gh secret set CLAUDE_CODE_OAUTH_TOKEN --repo amd/gaia`. Do NOT ask for a "
                "spend-limit increase - the budget is not the problem."
            ),
            where="repository secrets (CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY)",
            matched=sorted(set(credential)),
        )

    return Verdict(
        kind="unclassified",
        exit_code=EXIT_UNCLASSIFIED,
        summary=(
            f"The Claude probe was rejected (exit code {exit_code}) and the response "
            f"matches neither the quota nor the credential signature. Response: {excerpt}"
        ),
        action=(
            "Read the full response in the step log and act on it directly, then teach "
            "this guard the new phrase by adding it to QUOTA_PATTERNS or "
            "CREDENTIAL_PATTERNS in util/classify_claude_probe.py so the next occurrence "
            "is named."
        ),
        where="util/classify_claude_probe.py and the failing step's log",
    )


def extract_reply(raw: str, log_format: str) -> str:
    """Return only the model's own words from a probe log.

    ``text`` is the CLI's combined stdout/stderr and is returned unchanged.
    ``stream-json`` is what claude-code-action writes to ``execution_file``;
    only assistant and result text is returned, because the log also echoes the
    prompt and matching against that would let the canary pass on its own
    question.
    """
    if log_format == "text":
        return raw

    stripped = raw.strip()
    if not stripped:
        raise ValueError("the execution log is empty")

    try:
        parsed = json.loads(stripped)
        entries = parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        entries = []
        for line in stripped.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"could not parse the execution log as JSON or JSONL: {exc}"
                ) from exc

    chunks: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") == "assistant":
            content = entry.get("message", {}).get("content", [])
            if isinstance(content, str):
                chunks.append(content)
            elif isinstance(content, list):
                chunks += [
                    b.get("text", "")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
        elif entry.get("type") == "result":
            result = entry.get("result")
            if isinstance(result, str):
                chunks.append(result)
            if entry.get("is_error"):
                chunks.append("is_error: true")
    return "\n".join(c for c in chunks if c)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--exit-code", type=int, required=True, help="exit status of the probe command"
    )
    parser.add_argument(
        "--text-file", required=True, help="file holding the probe response"
    )
    parser.add_argument(
        "--format",
        dest="log_format",
        choices=("text", "stream-json"),
        default="text",
        help="text = raw CLI output; stream-json = claude-code-action's execution_file",
    )
    parser.add_argument(
        "--always-scan",
        action="store_true",
        help="inspect the response body even when the probe exited 0 (the canary's mode)",
    )
    parser.add_argument(
        "--expect",
        default=None,
        help="literal the reply must contain; missing it fails even on a zero exit",
    )
    args = parser.parse_args(argv)

    try:
        with open(args.text_file, "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except OSError as exc:
        print(
            f"::error::Could not read the Claude probe response at {args.text_file}: "
            f"{exc} | WHAT TO DO: the step that runs the probe must write its output to "
            "this path before classifying it; without the response this check cannot "
            "prove the credential works. | WHERE TO LOOK: the workflow step that invokes "
            "util/classify_claude_probe.py"
        )
        return EXIT_UNCLASSIFIED

    try:
        reply = extract_reply(raw, args.log_format)
    except ValueError as exc:
        print(
            f"::error::Could not read the model's reply out of the probe log: {exc} | "
            "WHAT TO DO: check whether the action still writes stream-json to "
            "execution_file; an unreadable log means this check is not proving anything "
            f"and must not pass. | WHERE TO LOOK: {args.text_file}"
        )
        return EXIT_UNCLASSIFIED

    verdict = classify(reply, args.exit_code, always_scan=args.always_scan)
    if verdict.exit_code != EXIT_OK:
        print(f"::error::{verdict.message()}")
        return verdict.exit_code

    if args.expect is not None and args.expect.lower() not in reply.lower():
        excerpt = " ".join(reply.split())[:400] or "(empty reply)"
        print(
            "::error::The probe reported success but the model never replied with "
            f"'{args.expect}', so this proves the token parses, not that CI can call "
            f"Claude. Reply: {excerpt} | WHAT TO DO: read the reply above - a budget or "
            "policy rejection delivered as a normal completion looks exactly like this. "
            f"| WHERE TO LOOK: {args.text_file}"
        )
        return EXIT_MISSING_REPLY

    print(verdict.summary)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
