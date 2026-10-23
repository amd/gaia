# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The gate an adapter must pass before it is used.

Probes are asked of the model exactly as Lemonade serves it — the quantized
GGUF with the adapter applied — with thinking off and temperature 0, so the
scores compare like with like:

- **learned probes** — held-out questions from every learned item. A
  ``mentions`` probe passes when the reply contains every required phrase; a
  ``first_tool`` probe passes when the model's first move is the right tool.
- **regression probes** — general questions with fixed answers
  (:data:`REGRESSION_PROBES`). The adapter must not lose any the base model
  gets right.

A candidate is promoted only if it scores higher on learned probes than the
model it would replace, loses no regression probe, and its held-out loss is
below the base model's.
"""

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from gaia.learning.analyze import Probe

REGRESSION_PROBES: List[Probe] = [
    Probe("What is 12 multiplied by 12? Reply with the number.", must_mention=["144"]),
    Probe("What is the capital of Japan?", must_mention=["Tokyo"]),
    Probe("Translate 'thank you' into Spanish.", must_mention=["gracias"]),
    Probe("Who wrote the play Romeo and Juliet?", must_mention=["Shakespeare"]),
    Probe("What is the chemical symbol for gold?", must_mention=["Au"]),
    Probe("How many days are in a leap year?", must_mention=["366"]),
    Probe("What is the largest planet in our solar system?", must_mention=["Jupiter"]),
    Probe("In Python, which keyword defines a function?", must_mention=["def"]),
    Probe(
        "What gas do plants absorb from the air for photosynthesis?",
        must_mention=["carbon dioxide"],
    ),
    Probe(
        "What is the boiling point of water at sea level in Celsius?",
        must_mention=["100"],
    ),
    Probe("Spell the word 'necessary' backwards.", must_mention=["yrassecen"]),
    Probe(
        "Reply with exactly the word READY and nothing else.", must_mention=["READY"]
    ),
]


@dataclass
class ProbeResult:
    question: str
    kind: str
    passed: bool
    reply: str
    opportunity: str = ""


@dataclass
class Score:
    label: str
    learned_passed: int = 0
    learned_total: int = 0
    regression_passed: int = 0
    regression_total: int = 0
    results: List[ProbeResult] = field(default_factory=list)

    @property
    def learned_rate(self) -> float:
        return self.learned_passed / self.learned_total if self.learned_total else 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["learned_rate"] = round(self.learned_rate, 3)
        return d


def _passes(probe: Probe, message: Dict[str, Any]) -> bool:
    if probe.kind == "first_tool":
        calls = message.get("tool_calls") or []
        return (
            bool(calls)
            and calls[0].get("function", {}).get("name") == probe.expect_tool
        )
    reply = (message.get("content") or "").lower()
    return all(m.lower() in reply for m in probe.must_mention)


def _reply_text(message: Dict[str, Any]) -> str:
    calls = message.get("tool_calls") or []
    if calls:
        f = calls[0].get("function", {})
        return f"[tool call] {f.get('name')}({f.get('arguments')})"
    return (message.get("content") or "")[:500]


def ask(client, model: str, probe: Probe) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if probe.tools:
        kwargs["tools"] = probe.tools
    response = client.chat_completions(
        model=model,
        messages=[{"role": "user", "content": probe.question}],
        temperature=0.0,
        max_tokens=400,
        auto_download=False,
        **kwargs,
    )
    return response["choices"][0]["message"]


def score(client, model: str, label: str, probes: List[Probe], *, stop=None) -> Score:
    """Ask every learned probe and every regression probe of the loaded model.

    *stop* is checked between probes; it raises to abandon the run.
    """
    s = Score(label=label)
    for group, items in (("learned", probes), ("regression", REGRESSION_PROBES)):
        for probe in items:
            if stop is not None:
                stop()
            message = ask(client, model, probe)
            ok = _passes(probe, message)
            s.results.append(
                ProbeResult(
                    question=probe.question,
                    kind=group,
                    passed=ok,
                    reply=_reply_text(message),
                    opportunity=probe.opportunity,
                )
            )
            if group == "learned":
                s.learned_total += 1
                s.learned_passed += int(ok)
            else:
                s.regression_total += 1
                s.regression_passed += int(ok)
    return s


@dataclass
class Decision:
    promote: bool
    reason: str


def decide(
    candidate: Score,
    incumbent: Score,
    base: Score,
    metrics: Dict[str, Any],
) -> Decision:
    """Promote only on a real, measured gain with nothing lost."""
    lost = [
        r.question
        for r, b in zip(
            [r for r in candidate.results if r.kind == "regression"],
            [r for r in base.results if r.kind == "regression"],
        )
        if b.passed and not r.passed
    ]
    if lost:
        return Decision(
            False,
            f"lost {len(lost)} general question(s) the base model gets right: {lost[0]!r}",
        )
    base_loss, loss = metrics.get("base_eval_loss"), metrics.get("eval_loss")
    if base_loss is None or loss is None:
        # Legitimately absent only when every item was too small to hold one
        # example out; the probes then carry the decision alone.
        if metrics.get("eval_examples") != 0:
            return Decision(False, "training reported no held-out loss to compare")
    elif loss >= base_loss:
        return Decision(
            False,
            f"held-out loss {loss:.3f} is not below the base model's {base_loss:.3f}",
        )
    if candidate.learned_total == 0:
        return Decision(False, "there were no learned probes to measure a gain on")
    if candidate.learned_passed <= incumbent.learned_passed:
        return Decision(
            False,
            f"answered {candidate.learned_passed}/{candidate.learned_total} learned probes, "
            f"no better than the {incumbent.label} ({incumbent.learned_passed})",
        )
    return Decision(
        True,
        f"answered {candidate.learned_passed}/{candidate.learned_total} learned probes "
        f"(the {incumbent.label}: {incumbent.learned_passed}) and kept every general "
        "answer the base model gets right",
    )


def probes_from_jsonl(text: str) -> List[Probe]:
    return [Probe(**json.loads(line)) for line in text.splitlines() if line.strip()]
