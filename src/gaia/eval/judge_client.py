# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Judge transport selection for the judged evals.

Two credentials can drive the Claude judge, and they are not interchangeable at
the transport layer:

* ``ANTHROPIC_API_KEY`` — a console API key. Driven straight against the
  Messages API by :class:`gaia.eval.claude.ClaudeClient`, billed per token.
* ``CLAUDE_CODE_OAUTH_TOKEN`` — a Claude Code subscription token, which
  authenticates the ``claude`` CLI. It is driven through ``claude -p``, the same
  transport :mod:`gaia.eval.runner` already uses to run eval scenarios.

Both yield an object exposing ``get_completion(prompt)`` that returns a list of
content blocks with a ``.text`` attribute, so the three judge factories
(``draft_quality`` / ``action_item_quality`` / ``briefing_quality``) consume
either without caring which is in play.

There is no fallback between them: whichever credential is present is used, and
a rejected credential raises rather than degrading to a weaker scorer.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass

from dotenv import load_dotenv

from gaia.eval.config import DEFAULT_CLAUDE_MODEL
from gaia.logger import get_logger

load_dotenv()

log = get_logger(__name__)

#: Seconds a single judge call may take. Judge prompts carry a full case inbox,
#: and the CLI pays a process cold-start the SDK does not.
DEFAULT_CLI_TIMEOUT = 300.0

#: Env vars that can carry a judge credential, in the order they are preferred
#: by :func:`make_judge_client`.
JUDGE_CREDENTIAL_VARS = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")

#: Replaces Claude Code's agent system prompt so the CLI judge scores from the
#: rubric alone, like the SDK judge whose baselines are committed.
_JUDGE_SYSTEM_PROMPT = (
    "You are an evaluation judge. Answer the user message exactly as it asks, "
    "using only the information it contains. Emit nothing else — no preamble, "
    "no commentary, no code fences."
)

#: Scoring must not become an agent loop. A name that no longer exists simply
#: matches nothing, so a stale entry is harmless.
_DISALLOWED_TOOLS = (
    "Bash,Read,Write,Edit,Glob,Grep,WebFetch,WebSearch,Task,TodoWrite,NotebookEdit"
)

MISSING_CREDENTIAL_ERROR = (
    "No Claude judge credential found: neither CLAUDE_CODE_OAUTH_TOKEN nor "
    "ANTHROPIC_API_KEY is set.\n\n"
    "The judged evals (drafting, action-item, briefing) score every case with a "
    "Claude judge and cannot produce a result without one. Set either:\n"
    "  1. SUBSCRIPTION (recommended if you have Claude Code Max):\n"
    "     Run `claude setup-token`, then export the printed token:\n"
    "       export CLAUDE_CODE_OAUTH_TOKEN=<token>\n"
    "     This drives the judge through the `claude` CLI, so the CLI must be on\n"
    "     PATH (`npm install -g @anthropic-ai/claude-code`).\n"
    "  2. API KEY (billed to your Anthropic console):\n"
    "       export ANTHROPIC_API_KEY=sk-ant-...\n"
    "Either can also live in a `.env` file in the repo root.\n"
    "In CI, add the matching repository secret — see the judge-credential "
    "preflight in .github/workflows/test_email_agent_eval.yml.\n"
)


def judge_credential_present() -> bool:
    """True when some credential could drive the judge.

    The eval report scripts guard on this before spending Lemonade time. It
    deliberately does not say whether the credential will be *accepted* — for
    that, probe it (see :func:`probe_judge_credential`).
    """
    return any((os.getenv(var) or "").strip() for var in JUDGE_CREDENTIAL_VARS)


@dataclass(frozen=True)
class TextBlock:
    """One text content block, shaped like the Anthropic SDK's."""

    text: str


class ClaudeCliClient:
    """A judge transport that shells out to ``claude -p``.

    Used when the credential is a Claude Code subscription token, which
    authenticates the CLI rather than the Messages API.
    """

    def __init__(self, model: str | None = None, timeout: float = DEFAULT_CLI_TIMEOUT):
        self._bin = shutil.which("claude")
        if not self._bin:
            raise RuntimeError(
                "CLAUDE_CODE_OAUTH_TOKEN is set but the `claude` CLI is not on "
                "PATH, so the judge has no transport.\n"
                "Install it with `npm install -g @anthropic-ai/claude-code`, or "
                "set ANTHROPIC_API_KEY instead to judge over the Messages API.\n"
                "See src/gaia/eval/judge_client.py for how the transport is "
                "selected."
            )
        self.model = model or DEFAULT_CLAUDE_MODEL
        self.timeout = timeout
        # An empty cwd is what keeps the repo's CLAUDE.md out of the judge's
        # context. Cleanup errors are ignored because Windows can still hold a
        # handle from the just-exited CLI child.
        self._workdir = tempfile.TemporaryDirectory(
            prefix="gaia-judge-", ignore_cleanup_errors=True
        )
        log.info(
            "Judge transport: `claude -p` (subscription token), model %s", self.model
        )

    def _argv(self) -> list[str]:
        return [
            self._bin,
            "-p",
            "--model",
            self.model,
            # JSON, not text: on an API error the CLI exits 0 and prints the error
            # as the result, so `is_error` is the only reliable failure signal.
            "--output-format",
            "json",
            # Replace Claude Code's agent prompt so the judge sees the rubric and
            # little else — the SDK judge's baselines were scored that way.
            "--system-prompt",
            _JUDGE_SYSTEM_PROMPT,
            # Isolate the judge: no project/user settings, no MCP servers, no
            # tools, no session files. Each would shift verdicts or litter the
            # long-lived runner.
            "--setting-sources",
            "",
            "--strict-mcp-config",
            "--mcp-config",
            json.dumps({"mcpServers": {}}),
            "--disallowed-tools",
            _DISALLOWED_TOOLS,
            "--no-session-persistence",
        ]

    def get_completion(self, prompt: str) -> list[TextBlock]:
        """Run one judge prompt and return its reply as content blocks."""
        # The prompt goes over stdin, not argv: a judge prompt carries a whole
        # case inbox and would blow the Windows command-line length limit.
        try:
            proc = subprocess.run(
                self._argv(),
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                cwd=self._workdir.name,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(
                f"The Claude judge timed out after {self.timeout:.0f}s "
                f"(`claude -p --model {self.model}`). Raise the timeout via "
                "ClaudeCliClient(timeout=...), or check that the CLI is not "
                "waiting on an interactive prompt."
            ) from e

        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip() or "(no output)"
            raise RuntimeError(
                f"{self._failure_preamble()} It exited {proc.returncode}.\n"
                f"CLI output: {detail[:2000]}"
            )

        try:
            payload = json.loads(proc.stdout or "")
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"{self._failure_preamble()} It exited 0 but its output was not "
                f"the JSON `--output-format json` promises, so no verdict could be "
                f"read.\nCLI output: {(proc.stdout or '(empty)')[:2000]}"
            ) from e

        if payload.get("is_error"):
            raise RuntimeError(
                f"{self._failure_preamble()} It reported is_error=true "
                f"(api_error_status={payload.get('api_error_status')}).\n"
                f"Result: {str(payload.get('result'))[:2000]}"
            )

        text = str(payload.get("result") or "").strip()
        if not text:
            raise RuntimeError(
                f"{self._failure_preamble()} It succeeded but returned an empty "
                "result, so this case cannot be scored. Re-run; if it persists, "
                "check the CLI with `claude -p 'reply with: ok'`."
            )
        return [TextBlock(text=text)]

    def _failure_preamble(self) -> str:
        return (
            f"The Claude judge failed (`claude -p --model {self.model}`). Check "
            "that CLAUDE_CODE_OAUTH_TOKEN is set and unexpired (regenerate with "
            "`claude setup-token`), and that the model id is one your account can "
            "reach."
        )


def make_judge_client(model: str | None = None):
    """Build the judge client the available credential can actually drive.

    ``ANTHROPIC_API_KEY`` wins when both are set — the workflow blanks it when
    it wants the subscription path, matching every other Claude workflow here.
    Raises when neither credential is present; never returns a degraded scorer.
    """
    api_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    oauth_token = (os.getenv("CLAUDE_CODE_OAUTH_TOKEN") or "").strip()

    if api_key:
        from gaia.eval.claude import ClaudeClient

        return ClaudeClient(model=model)
    if oauth_token:
        return ClaudeCliClient(model=model)
    raise ValueError(MISSING_CREDENTIAL_ERROR)


def probe_judge_credential(model: str | None = None) -> str:
    """Send the judge one trivial prompt and return its reply.

    Asserts the credential is *accepted*, not merely present — a non-empty
    variable says nothing about whether the account behind it still works.

    Caveat on a dev box: the `claude` CLI can fall back to a keychain session,
    so a passing probe there proves "some Claude auth works", not "this token
    works". CI has no keychain, so the probe is exact where it matters.
    """
    client = make_judge_client(model=model)
    content = client.get_completion("Reply with exactly: ok")
    reply = "".join(
        getattr(block, "text", "") for block in content if hasattr(block, "text")
    ).strip()
    if not reply:
        raise RuntimeError(
            "The judge credential was accepted but the probe reply was empty, so "
            "the eval could not be scored. Re-run, or check the judge model id."
        )
    return reply


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Judge credential utilities for the judged evals."
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Send one trivial prompt to the judge; exit non-zero if rejected.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=f"Judge model id (default: {DEFAULT_CLAUDE_MODEL}).",
    )
    args = parser.parse_args(argv)

    if not args.probe:
        parser.error("nothing to do — pass --probe")

    try:
        reply = probe_judge_credential(model=args.model)
    except (ValueError, RuntimeError) as e:
        print(f"Judge credential probe FAILED:\n{e}", file=sys.stderr)
        return 1
    print(f"Judge credential accepted by a live probe. Reply: {reply[:80]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
