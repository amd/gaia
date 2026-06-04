# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Behavior-E2E harness: assert tool side-effects, not just agent replies.

The class of bug "agent claims success but the tool never ran" (see #1428) is
invisible to unit tests that mock the LLM and to UI-render E2E tests that only
assert the agent produced a reply.

Pattern:
1. Drive each tool-using agent through the real server with a real model.
2. Repeat each scenario N× — the failure is output-format-dependent and
   non-deterministic, so a single pass is not sufficient evidence.
3. Assert the tool's **side-effect actually occurred** — not merely that the
   agent produced a reply.
4. Planted unguessable names (``secrets.token_hex(4)``) so a cached or
   hallucinated reply cannot accidentally pass.
5. False-success is a hard fail: "success" reply with no side-effect is worse
   than honest failure — it masks the regression.

Usage (live test, requires real Lemonade server)::

    from gaia.eval.behavior_harness import BehaviorHarness, BUILDER_SCENARIOS
    harness = BehaviorHarness(base_url="http://localhost:4200")
    result = harness.run_scenario(BUILDER_SCENARIOS[0], home_dir=Path("..."))
    assert result["passed"]

New agents plug in by adding a ``Scenario`` to ``BUILDER_SCENARIOS`` (or their
own list) and providing a ``side_effect_check`` that inspects the file system /
REST API for evidence the tool executed.
"""

import logging
import re
import secrets
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional

from gaia.agents.builder.agent import (
    _normalize_agent_id,
    _split_camel_case,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Verdict enum
# ---------------------------------------------------------------------------


class Verdict(Enum):
    """Classification for a single scenario run."""

    true_success = "true_success"
    """Agent replied success AND the side-effect is present — genuine pass."""

    false_success = "false_success"
    """Agent replied success but side-effect is ABSENT — the #1428 class of bug.
    This is a **hard fail** and always dominates the aggregation."""

    honest_failure = "honest_failure"
    """Agent did not claim success and the side-effect is absent — the tool
    failed but at least reported it honestly."""

    error = "error"
    """The harness itself encountered an error (network failure, bad response,
    etc.)."""


# ---------------------------------------------------------------------------
# Success-marker detection
# ---------------------------------------------------------------------------


_SUCCESS_PATTERNS = re.compile(
    r"\b("
    r"success(fully)?|"
    r"created|"
    r"done|"
    r"ready|"
    r"available|"
    r"has been created|"
    r"is now"
    r")\b",
    re.IGNORECASE,
)


def _success_markers(reply: str) -> bool:
    """Return True if *reply* contains language claiming the action succeeded."""
    return bool(_SUCCESS_PATTERNS.search(reply))


# ---------------------------------------------------------------------------
# Scenario dataclass
# ---------------------------------------------------------------------------


@dataclass
class Scenario:
    """A single reusable behavior-E2E scenario.

    Attributes:
        agent_type: The ``agent_type`` string sent to ``POST /api/sessions``.
        prompt_factory: Callable ``(idx: int) -> str`` that returns a distinct
            prompt per run.  Must embed an unguessable planted name so the
            agent cannot satisfy the check via a cached/hallucinated response.
        side_effect_check: Callable ``(home_dir: Path, reply: str,
            planted_name: str) -> Verdict`` that inspects the filesystem and/or
            the REST API to determine whether the tool actually executed.
        repeats: Number of independent runs for this scenario.  Defaults to 5
            because the failure is output-format-dependent and non-deterministic.
    """

    agent_type: str
    prompt_factory: Callable[[int], str]
    side_effect_check: Callable[[Path, str, str], Verdict]
    repeats: int = 5


# ---------------------------------------------------------------------------
# Builder scenario
# ---------------------------------------------------------------------------


def _builder_prompt_factory(_idx: int) -> str:
    """Return a prompt that plants a unique, unguessable agent name per call."""
    token = secrets.token_hex(4)  # e.g. "3f6aa1b2" — 8 hex chars
    name = f"test-{token}"
    return f"Create an agent named '{name}'. No tools, no MCP. Create it now."


def _builder_side_effect_check(
    home_dir: Path, reply: str, planted_name: str
) -> Verdict:
    """Check whether the builder actually wrote the agent files.

    A ``true_success`` requires:
    - ``<home_dir>/.gaia/agents/<id>/agent.py`` exists on disk.

    The REST API check (``GET /api/agents``) is performed only by the live
    harness (``BehaviorHarness``), not this pure-function helper, so that
    unit tests do not need a running server.
    """
    # Derive the agent id exactly as the builder does.
    agent_id = _normalize_agent_id(_split_camel_case(planted_name.strip()))

    agent_py = home_dir / ".gaia" / "agents" / agent_id / "agent.py"
    file_exists = agent_py.exists()

    if file_exists:
        return Verdict.true_success

    # File is absent — classify by what the agent claimed.
    if _success_markers(reply):
        return Verdict.false_success

    return Verdict.honest_failure


BUILDER_SCENARIOS: List[Scenario] = [
    Scenario(
        agent_type="builder",
        prompt_factory=_builder_prompt_factory,
        side_effect_check=_builder_side_effect_check,
        repeats=5,
    )
]


# ---------------------------------------------------------------------------
# Verdict aggregation
# ---------------------------------------------------------------------------


def _aggregate_verdicts(verdicts: List[Verdict]) -> Dict:
    """Aggregate a list of per-run Verdicts into an overall result.

    Rules:
    - Any ``false_success`` → ``hard_fail=True, passed=False``.
    - Any ``honest_failure`` (with no false_success) → ``passed=False``.
    - All ``true_success`` → ``passed=True``.

    Returns a dict with keys:
    - ``passed``: bool
    - ``hard_fail``: bool — True only when ``false_success`` is present
    - ``counts``: Dict[Verdict, int]
    """
    counts: Dict[Verdict, int] = {v: 0 for v in Verdict}
    for v in verdicts:
        counts[v] += 1

    hard_fail = counts[Verdict.false_success] > 0
    passed = (
        (not hard_fail)
        and counts[Verdict.honest_failure] == 0
        and counts[Verdict.error] == 0
    )

    return {
        "passed": passed,
        "hard_fail": hard_fail,
        "counts": counts,
    }


# ---------------------------------------------------------------------------
# Live harness (requires real server + real Lemonade backend)
# ---------------------------------------------------------------------------


class BehaviorHarness:
    """Drive a scenario against the real GAIA Agent UI server.

    Instantiate with the base URL of a running server::

        harness = BehaviorHarness(base_url="http://127.0.0.1:4200")

    Then call ``run_scenario`` with a ``Scenario`` and an isolated ``home_dir``
    that the server's agent registry points at.

    This class is deliberately thin: all classification logic is in the pure
    helpers above so that unit tests cover it without a real server.
    """

    def __init__(self, base_url: str, timeout: int = 120):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def _post(self, path: str, body: dict) -> dict:
        """POST ``path`` and return the JSON response, raising on HTTP errors."""
        import requests  # local import — only needed when the harness actually runs

        url = f"{self._base_url}{path}"
        resp = requests.post(url, json=body, timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()

    def _get(self, path: str) -> dict:
        import requests

        url = f"{self._base_url}{path}"
        resp = requests.get(url, timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()

    def _create_session(self, agent_type: str) -> str:
        """Create a new chat session and return its id."""
        data = self._post(
            "/api/sessions",
            {"title": f"behavior-e2e-{agent_type}", "agent_type": agent_type},
        )
        return data["id"]

    def _send_message(self, session_id: str, message: str) -> str:
        """Send a message (non-streaming) and return the agent's reply text."""
        data = self._post(
            "/api/chat/send",
            {
                "session_id": session_id,
                "message": message,
                "stream": False,
                "agent_type": None,
            },
        )
        return data.get("content", "")

    def _agent_listed(self, agent_id: str) -> bool:
        """Return True if the agent appears in ``GET /api/agents``."""
        try:
            data = self._get("/api/agents")
            agents = data.get("agents", [])
            return any(a.get("id") == agent_id for a in agents)
        except Exception:
            logger.warning(
                "_agent_listed: /api/agents request failed",
                exc_info=True,
            )
            return False

    def run_scenario(
        self,
        scenario: Scenario,
        home_dir: Path,
        *,
        artifact_dir: Optional[Path] = None,
    ) -> Dict:
        """Run *scenario* N× and return aggregated results.

        Args:
            scenario: The scenario to run.
            home_dir: Isolated home directory; the server must use this as the
                agent registry root (i.e. ``~/.gaia/agents/``).
            artifact_dir: If provided and a run fails, server logs and
                transcripts are written here.

        Returns:
            A dict as returned by ``_aggregate_verdicts`` plus a
            ``"transcripts"`` key with per-run (prompt, reply, verdict) tuples.
        """
        verdicts: List[Verdict] = []
        transcripts = []

        for idx in range(scenario.repeats):
            prompt = scenario.prompt_factory(idx)
            # Extract the planted name from the prompt (inside single-quotes).
            match = re.search(r"'([^']+)'", prompt)
            planted_name = match.group(1) if match else f"run-{idx}"

            reply = ""
            verdict = Verdict.error
            try:
                session_id = self._create_session(scenario.agent_type)
                reply = self._send_message(session_id, prompt)
                verdict = scenario.side_effect_check(home_dir, reply, planted_name)

                # Extra check for the builder scenario: confirm registry listing.
                if verdict == Verdict.true_success:
                    agent_slug = _normalize_agent_id(
                        _split_camel_case(planted_name.strip())
                    )
                    if not self._agent_listed(agent_slug):
                        # File exists but registry doesn't know about it — treat
                        # as honest failure (tool ran but server didn't reload).
                        verdict = Verdict.honest_failure
                        logger.warning(
                            "run %d: agent file present but not in /api/agents", idx
                        )

            except Exception as exc:
                logger.error("run %d error: %s", idx, exc, exc_info=True)
                verdict = Verdict.error

            verdicts.append(verdict)
            transcripts.append(
                {"idx": idx, "prompt": prompt, "reply": reply, "verdict": verdict.value}
            )
            logger.info("run %d/%d: %s", idx + 1, scenario.repeats, verdict.value)

        result = _aggregate_verdicts(verdicts)
        result["transcripts"] = transcripts

        if not result["passed"] and artifact_dir is not None:
            self._dump_artifacts(artifact_dir, transcripts)

        return result

    def _dump_artifacts(self, artifact_dir: Path, transcripts: list) -> None:
        """Write transcript JSON to *artifact_dir* for post-mortem inspection."""
        import json

        artifact_dir.mkdir(parents=True, exist_ok=True)
        out = artifact_dir / "transcripts.json"
        out.write_text(json.dumps(transcripts, indent=2))
        logger.info("artifacts written to %s", out)
