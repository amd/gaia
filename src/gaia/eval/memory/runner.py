# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``gaia eval memory``: replay the scenarios, write results.json + report.md, gate.

``pr`` is offline and deterministic (scripted model, hashed embedder): it
measures the memory pipeline — what is stored, superseded, forgotten, refused
and shown — and gates PRs on a committed baseline. ``full`` asks the local
model to extract, consolidate and answer, with the real embedder: it measures
whether memory works for a real user, and is too noisy to gate on.
"""

from __future__ import annotations

import json
import platform
import shutil
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from gaia.eval.memory.harness import (
    CheckResult,
    HashEmbedder,
    Replay,
    ScriptedChat,
    new_workdir,
)
from gaia.eval.memory.scenario import Scenario, load_scenarios

SUITES = ("pr", "full")

#: The checks each headline metric is made of.
METRICS = {
    "shown_recall": ("seen",),  # what should reach the agent, does
    "shown_clean": ("unseen",),  # stale, forgotten and planted text stays out
    "store_accuracy": ("stored", "not_stored"),  # the database holds the truth
    "answer_accuracy": ("mentions", "avoids"),  # the answer uses it (full only)
}


@dataclass
class SuiteResult:
    suite: str
    model: str
    started: float
    seconds: float
    scenarios: List[str]
    checks: List[Dict[str, Any]]
    metrics: Dict[str, Optional[float]]
    by_kind: Dict[str, float]
    memories_at_end: Dict[str, int]
    host: Dict[str, str] = field(default_factory=dict)
    #: full suite: what memory logged as failing (a model reply it could not
    #: parse, an op it refused). The pr suite fails on any instead.
    warnings: Dict[str, List[str]] = field(default_factory=dict)

    def failing(self) -> List[str]:
        return sorted(c["key"] for c in self.checks if not c["passed"])


def _rate(checks: Sequence[CheckResult]) -> Optional[float]:
    return round(sum(c.passed for c in checks) / len(checks), 4) if checks else None


def _real_model_parts(model: Optional[str]):
    """The real chat SDK and an answer function, for the ``full`` suite."""
    from gaia.chat.sdk import AgentConfig, AgentSDK
    from gaia.llm.lemonade_client import no_thinking_kwargs, resolve_default_chat_model

    model_id = model or resolve_default_chat_model()
    chat = AgentSDK(AgentConfig(model=model_id, max_tokens=4096))

    def answer(view: str, query: str) -> str:
        reply = chat.send_messages(
            messages=[{"role": "user", "content": query}],
            system_prompt=(
                "You are GAIA, the user's personal assistant. Use what you know "
                "about the user. Answer in under 120 words.\n\n" + view
            ),
            temperature=0.2,
            max_tokens=400,
            **no_thinking_kwargs(model_id),
        )
        return reply.text

    return model_id, chat, answer


def run_suite(
    suite: str,
    *,
    only: Optional[Sequence[str]] = None,
    model: Optional[str] = None,
    keep_workdir: bool = False,
) -> SuiteResult:
    if suite not in SUITES:
        raise ValueError(f"unknown suite {suite!r}; choose from {SUITES}")
    scenarios: List[Scenario] = load_scenarios(only)
    workdir = new_workdir()
    started = time.time()
    checks: List[CheckResult] = []
    memories: Dict[str, int] = {}
    warnings: Dict[str, List[str]] = {}
    if suite == "pr":
        model_id = ScriptedChat.effective_model
        real_chat = answer = None
    else:
        model_id, real_chat, answer = _real_model_parts(model)
    try:
        for scenario in scenarios:
            if suite == "pr":
                replay = Replay(
                    scenario,
                    workdir,
                    chat_factory=lambda resolve, s=scenario: ScriptedChat(s, resolve),
                    embedder=HashEmbedder(),
                    strict=True,
                )
            else:
                replay = Replay(
                    scenario,
                    workdir,
                    chat_factory=lambda resolve: real_chat,
                    embedder=None,
                    answer=answer,
                )
            outcome = replay.run()
            checks.extend(outcome.checks)
            memories[scenario.id] = outcome.memories_at_end
            if outcome.warnings:
                warnings[scenario.id] = outcome.warnings
    finally:
        if not keep_workdir:
            shutil.rmtree(workdir, ignore_errors=True)

    metrics = {
        name: _rate([c for c in checks if c.check in kinds])
        for name, kinds in METRICS.items()
    }
    metrics["overall"] = _rate(checks)
    grouped: Dict[str, List[CheckResult]] = defaultdict(list)
    for c in checks:
        grouped[c.kind].append(c)
    return SuiteResult(
        suite=suite,
        model=model_id,
        started=started,
        seconds=round(time.time() - started, 1),
        scenarios=[s.id for s in scenarios],
        checks=[{**asdict(c), "key": c.key} for c in checks],
        metrics=metrics,
        by_kind={k: _rate(v) for k, v in sorted(grouped.items())},
        memories_at_end=memories,
        host={"python": platform.python_version(), "platform": platform.platform()},
        warnings=warnings,
    )


@dataclass
class GateResult:
    regressions: List[str]
    fixed: List[str]
    new_checks: List[str]
    missing_checks: List[str]

    @property
    def passed(self) -> bool:
        return not self.regressions and not self.missing_checks


def gate(result: SuiteResult, baseline: Dict[str, Any]) -> GateResult:
    """Compare check by check: a check the baseline passed must still pass.

    A check the baseline does not know is new (reported, not failing). A
    baseline check that no longer runs fails the gate: a scenario edit that
    drops a check must update the baseline in the same change. Only the
    scenarios this run replayed are compared, so ``--scenario`` can gate too.
    """
    if baseline.get("suite") != result.suite:
        raise ValueError(
            f"baseline is for suite {baseline.get('suite')!r}, result is {result.suite!r}"
        )
    ran = set(result.scenarios)
    before = {
        c["key"]: c["passed"]
        for c in baseline["checks"]
        if c["key"].split("/", 1)[0] in ran
    }
    after = {c["key"]: c["passed"] for c in result.checks}
    return GateResult(
        regressions=sorted(k for k, ok in after.items() if before.get(k) and not ok),
        fixed=sorted(k for k, ok in after.items() if before.get(k) is False and ok),
        new_checks=sorted(set(after) - set(before)),
        missing_checks=sorted(set(before) - set(after)),
    )


def write_outputs(
    result: SuiteResult, out_dir: Path, gated: Optional[GateResult]
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(
        json.dumps(asdict(result), indent=2), encoding="utf-8"
    )
    (out_dir / "report.md").write_text(render_report(result, gated), encoding="utf-8")


def _pct(value: Optional[float]) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


def render_report(result: SuiteResult, gated: Optional[GateResult] = None) -> str:
    lines = [
        f"# Memory bench — `{result.suite}` suite",
        "",
        f"Model: `{result.model}` · {len(result.scenarios)} scenarios · "
        f"{len(result.checks)} checks · {result.seconds}s",
        "",
        "| Metric | Score | What it means |",
        "|---|---|---|",
        f"| Shown recall | {_pct(result.metrics['shown_recall'])} | what the agent should be shown about the user, it is |",
        f"| Shown clean | {_pct(result.metrics['shown_clean'])} | stale, forgotten and planted text stays out of what it is shown |",
        f"| Store accuracy | {_pct(result.metrics['store_accuracy'])} | the database holds the current truth and nothing it shouldn't |",
        f"| Answer accuracy | {_pct(result.metrics['answer_accuracy'])} | the model's answer uses it (full suite only) |",
        f"| Overall | {_pct(result.metrics['overall'])} | |",
        "",
        "## By kind",
        "",
        "| Kind | Pass rate |",
        "|---|---|",
    ]
    lines += [f"| {k} | {_pct(v)} |" for k, v in result.by_kind.items()]
    if result.warnings:
        lines += ["", "## Memory warnings", ""]
        for scenario, messages in result.warnings.items():
            lines += [f"- `{scenario}`: {m}" for m in messages]
    if gated is not None:
        lines += ["", "## Gate", ""]
        lines.append("**PASS**" if gated.passed else "**FAIL**")
        for title, keys in (
            ("Regressions (passed in the baseline, fail now)", gated.regressions),
            ("Checks the baseline has that no longer ran", gated.missing_checks),
            ("Fixed since the baseline", gated.fixed),
            ("New checks", gated.new_checks),
        ):
            if keys:
                lines += ["", f"### {title}", ""] + [f"- `{k}`" for k in keys]
    failing = [c for c in result.checks if not c["passed"]]
    if failing:
        lines += ["", "## Failing checks", ""]
        for c in failing:
            detail = f" — {c['detail']}" if c["detail"] else ""
            lines.append(f"- `{c['key']}` ({c['kind']}){detail}")
    return "\n".join(lines) + "\n"
