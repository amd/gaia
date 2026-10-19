#!/usr/bin/env python3
# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Check that the SWE-bench model answers before any task runs.

One tiny request through the embedded Lemonade. If that fails, the same request
goes straight to Fireworks to tell the two causes apart:

- Fireworks itself does not serve the model (404), is rate limiting, is down or
  is unreachable: the cause is upstream, not in GAIA. A pull request run skips
  the tasks with a warning, because no change in a PR can fix it. A dispatched
  run fails, because whoever started it asked for a measurement.
- Fireworks answers but Lemonade does not: GAIA's routing is broken. Fails.
- Fireworks refuses the key (401/403): the secret is wrong. Fails.

Writes ``ready=true|false`` to ``$GITHUB_OUTPUT`` and exits 1 on a failure.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

FIREWORKS_CHAT_URL = "https://api.fireworks.ai/inference/v1/chat/completions"
LEMONADE_PREFIX = "fireworks."
WORKFLOW = ".github/workflows/eval_swebench.yml"
RATE_CARD = "src/gaia/eval/bench/metering.py"


@dataclass(frozen=True)
class Decision:
    ready: bool
    fail: bool
    level: str  # GitHub annotation level: notice | warning | error
    title: str
    message: str


def fireworks_model_id(model: str) -> str:
    """``fireworks.x`` -> ``accounts/fireworks/models/x``; full paths unchanged."""
    name = model[len(LEMONADE_PREFIX) :] if model.startswith(LEMONADE_PREFIX) else model
    return name if name.startswith("accounts/") else f"accounts/fireworks/models/{name}"


def upstream_unavailable(direct: int) -> bool:
    """Fireworks cannot serve the model right now, whatever GAIA does."""
    return direct in (0, 404, 429) or direct >= 500


def decide(
    event: str, model: str, via_lemonade: int, direct: Optional[int]
) -> Decision:
    if via_lemonade == 200:
        return Decision(
            True, False, "notice", "SWE-bench model ready", f"{model} answered."
        )
    if direct is None:
        raise ValueError(
            "the direct Fireworks probe must run when Lemonade did not answer 200"
        )
    codes = f"HTTP {via_lemonade} through the embedded Lemonade, HTTP {direct} from Fireworks directly"
    if direct in (401, 403):
        return Decision(
            False,
            True,
            "error",
            "SWE-bench Fireworks key refused",
            f"{model}: {codes}. | WHAT TO DO: rotate FIREWORKS_API_KEY "
            f"(gh secret set FIREWORKS_API_KEY --repo amd/gaia). | WHERE TO LOOK: {WORKFLOW}",
        )
    if direct == 200:
        return Decision(
            False,
            True,
            "error",
            "SWE-bench model unreachable through Lemonade",
            f"{model}: {codes}. Fireworks serves the model, so GAIA's embedded Lemonade "
            f"routing is broken. | WHAT TO DO: check `gaia lemonade embedded status` and "
            f"the Lemonade log in this job. | WHERE TO LOOK: {WORKFLOW}",
        )
    if upstream_unavailable(direct):
        why = (
            "Fireworks does not serve this model to the FIREWORKS_API_KEY account "
            "(retired or not deployed)"
            if direct == 404
            else "Fireworks is rate limiting, down or unreachable"
        )
        action = (
            f"pick a served model and update SWE_MODEL and the rate card in {RATE_CARD}"
            if direct == 404
            else "re-run once Fireworks recovers"
        )
        if event == "pull_request":
            return Decision(
                False,
                False,
                "warning",
                "SWE-bench skipped: model unavailable upstream",
                f"{model}: {codes}. {why}, which no PR can fix, so no task ran and "
                f"nothing was measured. | WHAT TO DO: {action}. | WHERE TO LOOK: {WORKFLOW}",
            )
        return Decision(
            False,
            True,
            "error",
            "SWE-bench model unavailable",
            f"{model}: {codes}. {why}, so no task can run. | WHAT TO DO: {action}. "
            f"| WHERE TO LOOK: {WORKFLOW}",
        )
    return Decision(
        False,
        True,
        "error",
        "SWE-bench model preflight failed",
        f"{model}: {codes}. | WHAT TO DO: read the response bodies in this step's log. "
        f"| WHERE TO LOOK: {WORKFLOW}",
    )


def probe(url: str, key: str, model: str, timeout: float = 60) -> int:
    """POST a one-token chat request; the HTTP status, or 0 if nothing answered."""
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": "ok"}],
            "max_tokens": 1,
        }
    ).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        print(f"{url} -> HTTP {e.code}: {e.read(500).decode('utf-8', 'replace')}")
        return e.code
    except (urllib.error.URLError, OSError) as e:
        print(f"{url} -> no answer: {e}")
        return 0


def main() -> int:
    env = os.environ
    model = env["SWE_MODEL"]
    via_lemonade = probe(
        env["LEMONADE_BASE_URL"].rstrip("/") + "/chat/completions",
        env["LEMONADE_API_KEY"],
        model,
    )
    direct = None
    if via_lemonade != 200:
        direct = probe(
            FIREWORKS_CHAT_URL,
            env["LEMONADE_FIREWORKS_API_KEY"],
            fireworks_model_id(model),
        )
    d = decide(env.get("GITHUB_EVENT_NAME", ""), model, via_lemonade, direct)
    print(f"::{d.level} title={d.title}::{d.message}")
    if env.get("GITHUB_OUTPUT"):
        with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
            out.write(f"ready={'true' if d.ready else 'false'}\n")
    if not d.ready and env.get("GITHUB_STEP_SUMMARY"):
        with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as out:
            out.write(f"### {d.title}\n\n{d.message.replace(' | ', chr(10) * 2)}\n\n")
    return 1 if d.fail else 0


if __name__ == "__main__":
    sys.exit(main())
