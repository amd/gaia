# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tell a judge-side infrastructure outage from a real scoring failure.

An LLM-judged eval has two failure modes that look identical in a traceback and
mean opposite things:

* the judge **scored** the output and it was bad, or the reply could not be
  parsed — that is a result, and it belongs in the scorecard;
* the judge could not be **reached** because the account is out of budget or the
  credential is rejected — that is an outage, and a scorecard produced under it
  measures nothing.

Conflating them is how a billing lapse reads as a model regression. The email
evals hit exactly that: a ``credit balance is too low`` 400 escaped
``ClaudeClient`` as a raw ``anthropic.BadRequestError`` after a 21-minute run,
so the log ended in an SDK traceback instead of naming the outage.

Sibling, deliberately not shared: ``util/classify_claude_probe.py`` draws the
same quota/credential line for the *CI preflight*, which classifies the text of
a ``claude -p`` probe. This module classifies a raised **SDK exception** inside
the library. They cannot be one file today — ``util/`` is repo tooling, is not a
package, and is not shipped in the wheel, so ``gaia.eval`` cannot import it. The
right end-state is the reverse dependency: these lists move here and the probe
script imports them. See the PR that introduced this module.
"""

from __future__ import annotations

import sys
import textwrap
from typing import Callable

# Wrap width for the printed outage report — a CI log pane, not a terminal.
_WRAP = 88

# Matched case-insensitively against the exception text. Kept narrow on purpose:
# this list only has to cover what the Anthropic SDK actually raises, not every
# phrase the CLI probe can print.
QUOTA_PHRASES: tuple[str, ...] = (
    "credit balance",
    "purchase credits",
    "out of credit",
    "insufficient credit",
    "insufficient_quota",
    "quota",
    "billing",
    "spend limit",
    "usage limit",
    "weekly limit",
    "rate limit",
    "rate_limit_error",
    "limit resets",
    "upgrade your plan",
)

CREDENTIAL_PHRASES: tuple[str, ...] = (
    "authentication_error",
    "authentication failed",
    "invalid api key",
    "invalid x-api-key",
    "invalid bearer token",
    "invalid_token",
    "unauthorized",
    "token has expired",
    "not logged in",
)

# A status code is decisive on its own. 400 deliberately is NOT in either set:
# Anthropic returns it both for a malformed request (a real bug the eval must
# not hide) and for "credit balance is too low", so that case is reached by
# phrase instead.
QUOTA_STATUS: frozenset[int] = frozenset({402, 429})
CREDENTIAL_STATUS: frozenset[int] = frozenset({401, 403})

_ACTIONS = {
    "quota": (
        "Ask an Anthropic org admin to raise the spend limit at "
        "claude.ai/admin-settings/usage, or wait for the stated reset, then "
        "re-run. Do NOT rotate the repository secrets and do NOT treat the "
        "scorecard as a model regression - nothing was measured."
    ),
    "credential": (
        "Run `claude setup-token` locally and update the secret with "
        "`gh secret set ANTHROPIC_API_KEY --repo amd/gaia`. Do NOT ask for a "
        "spend-limit increase - the budget is not the problem."
    ),
    "unclassified": (
        "Read the full error below and act on it directly: it matches BOTH the "
        "quota and the credential signature, and those are different fixes by "
        "different people. Either way the judge never scored anything, so the "
        "run is not a quality signal."
    ),
}

_WHERE = {
    "quota": "https://claude.ai/admin-settings/usage and amd/gaia issue #4080",
    "credential": "repository secret ANTHROPIC_API_KEY",
    "unclassified": "src/gaia/eval/judge_outage.py and the failing step's log",
}


class JudgeOutageError(RuntimeError):
    """The judge could not be reached — so nothing was scored.

    Subclasses ``RuntimeError``, never ``ValueError``, and that is load-bearing:
    :func:`gaia.eval.draft_quality.judge_drafts` turns a ``ValueError`` into an
    ``ERRORED`` scorecard row, which is right for an unparseable verdict and
    exactly wrong for an outage. An outage has to abort the run instead of
    quietly filling the scorecard with rows that read as failures.
    """

    def __init__(self, kind: str, detail: str, status_code: int | None = None) -> None:
        self.kind = kind
        self.detail = detail
        self.status_code = status_code
        # One line, so this still reads well as the last line of a traceback.
        # The full report is message(), which the CLI guard prints instead.
        super().__init__(f"{self.headline()} — {detail}")

    def headline(self) -> str:
        label = {
            "quota": "JUDGE QUOTA EXHAUSTED",
            "credential": "JUDGE CREDENTIAL REJECTED",
            "unclassified": "JUDGE UNREACHABLE (cause ambiguous)",
        }[self.kind]
        status = f" [HTTP {self.status_code}]" if self.status_code else ""
        return f"{label}{status}"

    def message(self) -> str:
        lines = [
            textwrap.fill(
                f"{self.headline()} — the Claude judge never scored anything, so "
                "this run is an INFRASTRUCTURE OUTAGE, NOT a quality regression. "
                "Any scorecard from it is meaningless.",
                width=_WRAP,
            ),
            "",
        ]
        for label, body in (
            ("What to do", _ACTIONS[self.kind]),
            ("Where", _WHERE[self.kind]),
            ("Judge error", self.detail),
        ):
            lines.append(
                textwrap.fill(
                    body,
                    width=_WRAP,
                    initial_indent=f"  {label + ':':<13}",
                    subsequent_indent=" " * 15,
                    # Keep URLs clickable — a wrapped link is a dead link.
                    break_long_words=False,
                    break_on_hyphens=False,
                )
            )
        return "\n".join(lines)


def _exception_text(exc: BaseException) -> str:
    """Everything the SDK might have put the rejection phrase in.

    ``anthropic.APIStatusError`` carries the server's JSON on ``.body`` and the
    human string on ``.message``; ``str(exc)`` usually holds both, but not on
    every SDK version, so all three are scanned.
    """
    parts = [str(exc)]
    for attr in ("message", "body"):
        value = getattr(exc, attr, None)
        if value is not None:
            parts.append(str(value))
    return " ".join(parts).lower()


def classify_judge_exception(exc: BaseException) -> JudgeOutageError | None:
    """Return a :class:`JudgeOutageError` if ``exc`` means the judge is down.

    ``None`` means "not an outage" — the caller must re-raise the original so a
    genuine bug (a malformed request, a broken prompt) still fails loudly
    instead of being relabelled as someone else's billing problem.

    Duck-typed on ``status_code`` rather than importing ``anthropic``: this
    module stays importable without the ``[eval]`` extras, and the unit tests
    can exercise it with a stub exception.
    """
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        status = None

    if status in QUOTA_STATUS:
        return JudgeOutageError("quota", _detail(exc), status)
    if status in CREDENTIAL_STATUS:
        return JudgeOutageError("credential", _detail(exc), status)

    text = _exception_text(exc)
    quota = any(p in text for p in QUOTA_PHRASES)
    credential = any(p in text for p in CREDENTIAL_PHRASES)

    if quota and credential:
        return JudgeOutageError("unclassified", _detail(exc), status)
    if quota:
        return JudgeOutageError("quota", _detail(exc), status)
    if credential:
        return JudgeOutageError("credential", _detail(exc), status)
    return None


def _detail(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {' '.join(str(exc).split())[:400]}"


def judge_completion_text(client: object, prompt: str) -> str:
    """One judge completion, flattened to text, with outages named.

    The single call site that turns an SDK exception into a
    :class:`JudgeOutageError`. Shared by the drafting, briefing, and
    action-item judges, which differ in how they *parse* the verdict but not in
    how they fetch it — three copies of this would be three chances to fix the
    outage path in only two of them.

    Anything :func:`classify_judge_exception` does not recognise is re-raised
    untouched, so a genuine bug still fails loudly on its own terms.
    """
    try:
        content = client.get_completion(prompt)
    except Exception as exc:
        outage = classify_judge_exception(exc)
        if outage is None:
            raise
        raise outage from exc
    # Anthropic returns a list of content blocks; the verdict is the text ones.
    return "".join(
        getattr(block, "text", "") for block in content if hasattr(block, "text")
    )


def run_with_outage_guard(main: Callable[[], int]) -> int:
    """Run an eval report ``main()``, printing an outage as a named error.

    Without this the process dies in an SDK traceback whose last line is a 400,
    and the reader has to know that a 400 can mean "out of credit" to make sense
    of it. Returns ``main()``'s own exit code otherwise.
    """
    try:
        return main()
    except JudgeOutageError as exc:
        print(f"\n[EVAL] ERROR: {exc.message()}", file=sys.stderr)
        return 1
