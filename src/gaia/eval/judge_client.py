# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Judge transport selection for the judged evals.

Two credentials can drive the Claude judge, and they are not interchangeable at
the transport layer:

* ``ANTHROPIC_API_KEY`` — a console API key. Driven straight against the
  Messages API by :class:`gaia.eval.claude.ClaudeClient`, billed per token.
* A Claude Code subscription — ``CLAUDE_CODE_OAUTH_TOKEN``, or the login the
  ``claude`` CLI already holds. It is driven through ``claude -p``, the same
  transport :mod:`gaia.eval.runner` uses to run eval scenarios, and like the
  runner it needs no environment variable when the CLI is logged in.

Both yield an object exposing ``get_completion(prompt)`` that returns a list of
content blocks with a ``.text`` attribute, so the three judge factories
(``draft_quality`` / ``action_item_quality`` / ``briefing_quality``) consume
either without caring which is in play.

The rule is the runner's: an exported API key means the API; otherwise the
subscription, through the CLI. There is no fallback between them — a rejected
credential raises rather than retrying on the other one.
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

from gaia.env import load_env
from gaia.eval.claude_outage import driver_api_key
from gaia.eval.claude_usage import TOKEN_FIELDS, usage_from_cli
from gaia.eval.config import DEFAULT_CLAUDE_MODEL, MODEL_PRICING
from gaia.llm.local_only import assert_provider_permitted
from gaia.logger import get_logger

load_env()

log = get_logger(__name__)

#: Seconds a single judge call may take. Judge prompts carry a full case inbox,
#: and the CLI pays a process cold-start the SDK does not.
DEFAULT_CLI_TIMEOUT = 300.0

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
    "No Claude judge available: the `claude` CLI is not on PATH and "
    "ANTHROPIC_API_KEY is not set.\n\n"
    "The judged evals (drafting, action-item, briefing, sessions, corpus "
    "generation) need Claude and cannot produce a result without it. Use either:\n"
    "  1. SUBSCRIPTION (recommended if you have Claude Code Max): install the\n"
    "     CLI (`npm install -g @anthropic-ai/claude-code`) and log in (`claude`,\n"
    "     then `/login`). Where no interactive login is possible (CI), run\n"
    "     `claude setup-token` and export CLAUDE_CODE_OAUTH_TOKEN=<token>.\n"
    "  2. API KEY (billed to your Anthropic console):\n"
    "       export ANTHROPIC_API_KEY=sk-ant-...\n"
    "In CI, add the matching repository secret — see the judge-credential "
    "preflight in .github/workflows/test_email_agent_eval.yml.\n"
)


def judge_credential_present() -> bool:
    """True when some transport could drive the judge.

    An API key, a subscription token, or a ``claude`` CLI on PATH (which may be
    logged in). The eval report scripts guard on this before spending Lemonade
    time. It deliberately does not say whether the credential will be
    *accepted* — for that, probe it (see :func:`probe_judge_credential`).
    """
    return bool(
        driver_api_key()
        or (os.getenv("CLAUDE_CODE_OAUTH_TOKEN") or "").strip()
        or shutil.which("claude") is not None
    )


@dataclass(frozen=True)
class TextBlock:
    """One text content block, shaped like the Anthropic SDK's."""

    text: str


class ClaudeCliClient:
    """A judge transport that shells out to ``claude -p``.

    Used for a Claude Code subscription: ``CLAUDE_CODE_OAUTH_TOKEN`` or the
    CLI's own login, which authenticate the CLI rather than the Messages API.
    """

    def __init__(self, model: str | None = None, timeout: float = DEFAULT_CLI_TIMEOUT):
        self._bin = shutil.which("claude")
        if not self._bin:
            raise RuntimeError(
                "The subscription judge runs through the `claude` CLI, which is "
                "not on PATH (CLAUDE_CODE_OAUTH_TOKEN alone cannot drive it).\n"
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
        log.info("Judge transport: `claude -p` (subscription), model %s", self.model)

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
        text, _payload = self._complete(prompt)
        return [TextBlock(text=text)]

    def get_completion_with_usage(self, prompt: str) -> dict:
        """``get_completion`` plus token usage and cost, shaped like ``ClaudeClient``'s.

        ``total_cost`` is what the CLI reported (on a subscription, the list
        price of the tokens, not money spent); it is split into input and
        output by the model's per-token prices.
        """
        text, payload = self._complete(prompt)
        record = usage_from_cli(payload)
        if record is None:
            raise RuntimeError(
                f"{self._failure_preamble()} Its JSON carried no `usage` block, so "
                "the tokens this call used are unknown. Update the CLI "
                "(`npm install -g @anthropic-ai/claude-code`)."
            )
        output_tokens = record["output_tokens"]
        input_tokens = sum(record[f] for f in TOKEN_FIELDS) - output_tokens
        return {
            "content": [TextBlock(text=text)],
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            },
            "cost": _split_cost(
                self.model, input_tokens, output_tokens, record["cost_usd"]
            ),
        }

    def _complete(self, prompt: str) -> tuple[str, dict]:
        assert_provider_permitted("the Claude Code CLI")
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
        return text, payload

    def _failure_preamble(self) -> str:
        return (
            f"The Claude judge failed (`claude -p --model {self.model}`). Check "
            "that the CLI is logged in (`claude`, then `/login`) or that "
            "CLAUDE_CODE_OAUTH_TOKEN is set and unexpired (regenerate with "
            "`claude setup-token`), and that the model id is one your account can "
            "reach."
        )


def _split_cost(model: str, input_tokens: int, output_tokens: int, total: float):
    """``total`` split into input and output in proportion to their list price."""
    pricing = MODEL_PRICING.get(model, MODEL_PRICING["default"])
    weight_in = input_tokens * pricing["input_per_mtok"]
    weight_out = output_tokens * pricing["output_per_mtok"]
    weight = weight_in + weight_out
    input_cost = total * weight_in / weight if weight else 0.0
    return {
        "input_cost": round(input_cost, 6),
        "output_cost": round(total - input_cost, 6),
        "total_cost": round(total, 6),
    }


def make_judge_client(model: str | None = None, max_tokens: int | None = None):
    """Build the judge client the available credential can actually drive.

    The runner's rule: ``ANTHROPIC_API_KEY`` means the Messages API (the
    workflow blanks it when it wants the subscription path). Otherwise the
    subscription through ``claude -p`` — a ``CLAUDE_CODE_OAUTH_TOKEN`` or the
    CLI's own login. Raises when neither is available; never returns a
    degraded scorer. ``max_tokens`` applies to the API transport only.
    """
    api_key = driver_api_key()
    oauth_token = (os.getenv("CLAUDE_CODE_OAUTH_TOKEN") or "").strip()

    if api_key:
        from gaia.eval.claude import ClaudeClient

        if max_tokens is None:
            return ClaudeClient(model=model)
        return ClaudeClient(model=model, max_tokens=max_tokens)
    if oauth_token or shutil.which("claude"):
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
    from gaia.logger import configure

    configure()
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
