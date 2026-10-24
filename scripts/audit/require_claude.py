#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Stop a proactive audit before it spends anything when Claude cannot answer.

Five of ten October nights, both daily audits launched every lens, each lens was
refused in under a second with "You've hit your weekly limit", and the run failed
at an artifact upload with "the audit did not run". The cause was in the log of
four separate jobs; the summary said nothing. On the self-hosted walkthrough the
same refusal tied up a Strix Point runner for ~3 minutes per guide.

One probe now runs first. When it is refused the run fails here, once, with the
account, the reason, and the reset time in the job summary, and nothing else is
started. The verdict goes to ``--github-output`` so the workflow's
``request-account-switch`` job can dispatch claude-account-switch.yml on a quota
refusal, and the next run picks up the skipped work (see audit_history.py).

The dispatch is explicit because the audits are started by the rotation with
GITHUB_TOKEN. GitHub documents that GITHUB_TOKEN events create no workflow runs
except ``workflow_dispatch`` and ``repository_dispatch``, and does not say that
the account switch's ``workflow_run`` hook fires when such a run completes.

stdlib only; reuses util/classify_claude_probe.py so "out of quota" means the
same thing here as in the canary and the account switch.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CLASSIFIER = REPO_ROOT / "util" / "classify_claude_probe.py"

#: The reset clause in the CLI's refusal, e.g. "resets Oct 21, 3pm (UTC)".
RESET_RE = re.compile(r"resets [^|·\"\n)]{1,60}\)?", re.IGNORECASE)


def _classifier():
    spec = importlib.util.spec_from_file_location("classify_claude_probe", CLASSIFIER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


#: What the workflow reads from ``--github-output``. Only ``quota`` asks for an
#: account switch: a rejected token is fixed by rotating it, never by switching.
VERDICTS = ("ok", "quota", "credential", "no-log", "unknown")


def gate(
    conclusion: str, log: str, account: str, what: str
) -> tuple[int, str, str, str]:
    """Return ``(exit_code, verdict, error_line, summary_markdown)``; 0 means go."""
    classifier = _classifier()
    if not log or not Path(log).is_file():
        error = (
            f"The Claude probe wrote no execution log (conclusion '{conclusion}'), so it "
            f"is unknown whether the {account} account can answer. {what} was not "
            "started. Read the 'Probe the Claude account' step for the install or "
            "startup error."
        )
        return 1, "no-log", error, f"## ⛔ {what} did not start\n\n{error}\n"

    argv = [
        "--exit-code",
        "0" if conclusion == "success" else "1",
        "--text-file",
        log,
        "--format",
        "stream-json",
        "--always-scan",
        "--expect",
        "ok",
    ]
    captured = io.StringIO()
    with redirect_stdout(captured):
        code = classifier.main(argv)
    detail = captured.getvalue().strip()
    if code == classifier.EXIT_OK:
        return 0, "ok", "", ""

    reset = RESET_RE.search(detail)
    reset_text = f" The limit {reset.group(0).strip()}." if reset else ""
    if code == classifier.EXIT_QUOTA:
        verdict = "quota"
        headline = f"the {account} Claude account is out of quota"
        cause = (
            f"Claude refused the probe: {headline}.{reset_text} Nothing was started, "
            "so nothing was spent."
        )
        fix = (
            "Tonight's work is not lost: the next scheduled run covers everything since "
            "the last run that succeeded. This run's 'request-account-switch' job asks "
            "claude-account-switch.yml to move Claude CI to the other account if that "
            "one still has quota; its run shows the result."
        )
    elif code == classifier.EXIT_CREDENTIAL:
        verdict = "credential"
        headline = f"the {account} Claude account's token was rejected"
        cause = f"Claude refused the probe: {headline}. Nothing was started."
        fix = (
            "Rotate the token: `claude setup-token`, then `gh secret set "
            "CLAUDE_CODE_OAUTH_TOKEN` (or `_SECONDARY`) on amd/gaia."
        )
    else:
        verdict = "unknown"
        headline = "Claude did not answer the probe"
        cause = f"{headline} on the {account} account. Nothing was started."
        fix = "Read the classifier line below and the 'Probe the Claude account' step."

    error = f"{what} skipped: {cause} {fix}"
    summary = "\n".join(
        [
            f"## ⛔ {what} skipped: {headline}",
            "",
            cause,
            "",
            fix,
            "",
            "<details>",
            "<summary>Probe verdict</summary>",
            "",
            "```",
            detail,
            "```",
            "</details>",
            "",
        ]
    )
    return 1, verdict, error, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--conclusion", required=True)
    parser.add_argument("--log", default="")
    parser.add_argument("--account", required=True)
    parser.add_argument("--what", required=True, help="e.g. 'Tonight's nightly audit'")
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument(
        "--github-output",
        type=Path,
        help="Append verdict=<ok|quota|credential|no-log|unknown> here ($GITHUB_OUTPUT).",
    )
    args = parser.parse_args(argv)

    code, verdict, error, summary = gate(
        args.conclusion, args.log, args.account, args.what
    )
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as fh:
            fh.write(f"verdict={verdict}\n")
    if code == 0:
        print(f"Claude answered the probe on the {args.account} account.")
        return 0
    with args.summary.open("a", encoding="utf-8") as fh:
        fh.write(summary)
    print(f"::error title=Claude unavailable::{error}")
    return code


if __name__ == "__main__":
    sys.exit(main())
