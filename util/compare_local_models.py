# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Compare local chat models on this PC: speed, tool calling, and agent quality.

Decides which big model GAIA should default to on a given machine by measuring
each candidate on the machine itself, one model at a time, against GAIA's
Lemonade Server:

- speed: model load time, then time to first token, prompt tokens/s and
  generated tokens/s for a short prompt and for ~8K and ~32K token prompts;
- tool calling: fixed requests that each need one specific tool call with
  specific arguments, as the agent loop sends them;
- agent quality (``--tasks SUITE``): the flagship agent's scored task suite,
  ``gaia eval tasks run``, graded by the Claude judge.

A model that does not fit this PC, or that this Lemonade is too old to run, is
reported and skipped, never downloaded.

Usage::

    python util/compare_local_models.py                     # Flash vs Qwen3 30B
    python util/compare_local_models.py --tasks core        # plus agent quality
    python util/compare_local_models.py --models Qwen3-30B-A3B-Instruct-2507-HRX \\
        Qwen3-30B-A3B-Instruct-2507-GGUF                    # HRX vs llama.cpp
"""

import argparse
import json
import re
import secrets
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from gaia.llm.lemonade_client import (
    GPU_CTX_SIZE,
    LARGE_DEFAULT_MODEL_NAME,
    QWEN3_30B_MODEL_NAME,
    LemonadeClient,
    LemonadeClientError,
    find_model_requirement,
    lemonade_server_version,
)
from gaia.llm.model_fit import (
    ModelFitError,
    capacity_from_system_info,
    check_fit,
    check_server_supports,
)

DEFAULT_MODELS = (LARGE_DEFAULT_MODEL_NAME, QWEN3_30B_MODEL_NAME)
#: (label, approximate prompt tokens of filler). "short" has none.
PROMPT_SIZES = (("short", 0), ("8K", 8_000), ("32K", 32_000))
DECODE_TOKENS = 256
#: Reasoning models think before they call a tool; leave room for it.
TOOL_TOKENS = 4096
#: A 32K prompt on a slow model can take minutes to prefill.
REQUEST_TIMEOUT = 1800

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string"},
                    "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_event",
            "description": "Put an event on the user's calendar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "date": {"type": "string", "description": "YYYY-MM-DD"},
                    "duration_minutes": {"type": "integer"},
                },
                "required": ["title", "date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Find files under a directory whose names match a glob.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string"},
                    "pattern": {"type": "string"},
                },
                "required": ["directory", "pattern"],
            },
        },
    },
]

#: (request, expected tool, argument values it must carry, compared loosely).
TOOL_CASES = [
    (
        "What's the weather in Toronto right now, in celsius?",
        "get_weather",
        {"city": "toronto", "unit": "celsius"},
    ),
    (
        "Book 'Design review' on 2026-10-02 for 45 minutes.",
        "create_event",
        {"title": "design review", "date": "2026-10-02", "duration_minutes": 45},
    ),
    (
        "Find every .pdf file in ~/Documents.",
        "search_files",
        {"directory": "~/documents", "pattern": "*.pdf"},
    ),
    (
        "Is it colder in Oslo than usual? Check the weather there in fahrenheit.",
        "get_weather",
        {"city": "oslo", "unit": "fahrenheit"},
    ),
]


@dataclass
class SpeedResult:
    label: str
    prompt_tokens: Optional[int] = None
    ttft_s: Optional[float] = None
    prompt_tps: Optional[float] = None
    decode_tps: Optional[float] = None
    wall_s: Optional[float] = None
    error: Optional[str] = None


@dataclass
class ModelResult:
    model: str
    fits: Optional[bool] = None
    fit_reason: str = ""
    load_s: Optional[float] = None
    speed: List[SpeedResult] = field(default_factory=list)
    tool_calls_passed: int = 0
    tool_calls_total: int = 0
    tool_call_s: Optional[float] = None
    tool_failures: List[str] = field(default_factory=list)
    tasks: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


def _filler(tokens: int) -> str:
    """Distinct log-like text of roughly *tokens* tokens.

    A fresh nonce leads every prompt so the server's prompt cache cannot turn a
    prefill measurement into a cache hit.
    """
    lines = [f"run {secrets.token_hex(8)}"]
    i = 0
    # ~22 tokens per line with these word shapes (measured on Gemma 4).
    while len(lines) * 22 < tokens:
        i += 1
        lines.append(
            f"{i:05d} worker-{i % 7} processed batch {i * 31 % 997} "
            f"in {i % 89} ms status {'ok' if i % 11 else 'retry'}"
        )
    return "\n".join(lines)


def _stat(stats: Dict[str, Any], *names: str) -> Optional[float]:
    for name in names:
        value = stats.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def measure_speed(client: LemonadeClient, model: str, label: str, tokens: int):
    prompt = (_filler(tokens) + "\n\n" if tokens else "") + (
        "Summarize what the log above says about retries in two sentences."
        if tokens
        else "Explain in two sentences why the sky is blue."
    )
    result = SpeedResult(label=label)
    started = time.monotonic()
    try:
        client.chat_completions(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_completion_tokens=DECODE_TOKENS,
            timeout=REQUEST_TIMEOUT,
            auto_download=False,
        )
        stats = client.get_stats()
    except LemonadeClientError as e:
        result.error = str(e)
        return result
    result.wall_s = round(time.monotonic() - started, 2)
    result.prompt_tokens = int(_stat(stats, "input_tokens", "prompt_tokens") or 0)
    ttft = _stat(stats, "time_to_first_token")
    result.ttft_s = round(ttft, 2) if ttft is not None else None
    if ttft and result.prompt_tokens:
        result.prompt_tps = round(result.prompt_tokens / ttft, 1)
    decode = _stat(stats, "tokens_per_second")
    result.decode_tps = round(decode, 1) if decode is not None else None
    return result


def _loose(value: Any) -> Any:
    return value.strip().lower() if isinstance(value, str) else value


def check_tool_calls(client: LemonadeClient, model: str, result: ModelResult):
    started = time.monotonic()
    for request, tool, expected in TOOL_CASES:
        result.tool_calls_total += 1
        try:
            response = client.chat_completions(
                model=model,
                messages=[{"role": "user", "content": request}],
                temperature=0.0,
                max_completion_tokens=TOOL_TOKENS,
                tools=TOOLS,
                timeout=REQUEST_TIMEOUT,
                auto_download=False,
            )
        except LemonadeClientError as e:
            result.tool_failures.append(f"{request!r}: request failed: {e}")
            continue
        message = (response.get("choices") or [{}])[0].get("message") or {}
        calls = message.get("tool_calls") or []
        if not calls:
            result.tool_failures.append(f"{request!r}: no tool call")
            continue
        function = calls[0].get("function") or {}
        raw = function.get("arguments")
        try:
            args = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        except (ValueError, TypeError):
            result.tool_failures.append(f"{request!r}: arguments not JSON: {raw!r}")
            continue
        wrong = {
            k: args.get(k)
            for k, v in expected.items()
            if _loose(args.get(k)) != _loose(v)
        }
        if function.get("name") != tool or wrong:
            result.tool_failures.append(
                f"{request!r}: called {function.get('name')} with {args}"
            )
            continue
        result.tool_calls_passed += 1
    result.tool_call_s = round(time.monotonic() - started, 1)


def run_tasks(model: str, suite: str, out: Path, judge: bool) -> Dict[str, Any]:
    from gaia.eval import flagship_tasks as ft

    run_dir = out / f"tasks-{re.sub(r'[^A-Za-z0-9.]+', '-', model)}"
    cmd = [
        sys.executable,
        "-m",
        "gaia.cli",
        "eval",
        "tasks",
        "run",
        "--suite",
        suite,
        "--model",
        model,
        "--out",
        str(run_dir),
    ]
    if not judge:
        cmd.append("--no-judge")
    started = time.monotonic()
    proc = subprocess.run(cmd, check=False)
    card_path = run_dir / "scorecard.json"
    if not card_path.exists():
        return {
            "error": f"`{' '.join(cmd)}` exited {proc.returncode} with no scorecard"
        }
    summary = ft.summarize(json.loads(card_path.read_text(encoding="utf-8")))
    summary["wall_s"] = round(time.monotonic() - started, 1)
    summary["run_dir"] = str(run_dir)
    return summary


def compare(models, ctx: int, suite: Optional[str], judge: bool, out: Path):
    client = LemonadeClient(verbose=False)
    try:
        client.health_check()
        capacity = capacity_from_system_info(client.get_system_info(timeout=15))
    except (LemonadeClientError, ModelFitError) as e:
        raise SystemExit(
            f"Lemonade Server at {client.base_url} could not describe this PC: "
            f"{e}. Run `gaia init` first, then retry."
        ) from e
    server_version = lemonade_server_version(client)

    results = []
    for model in models:
        result = ModelResult(model=model)
        results.append(result)
        mr = find_model_requirement(model)
        size = mr.size_gb if mr else None
        if size:
            verdict = check_fit(size, capacity)
            result.fits, result.fit_reason = verdict.fits, verdict.reason
            if not verdict.fits:
                print(f"\n== {model}: skipped, {verdict.reason}")
                continue
        supported = check_server_supports(
            mr.min_lemonade_version if mr else None, server_version
        )
        if not supported.fits:
            result.fits, result.fit_reason = False, supported.reason
            print(f"\n== {model}: skipped, {supported.reason}")
            continue
        print(f"\n== {model}")
        try:
            print("   downloading if needed...")
            client.ensure_model_downloaded(
                model, timeout=7200 * 4, **(mr.pull_kwargs() if mr else {})
            )
            started = time.monotonic()
            client.load_model(model, ctx_size=ctx, timeout=1800, prompt=False)
            result.load_s = round(time.monotonic() - started, 1)
        except LemonadeClientError as e:
            result.error = f"could not download or load: {e}"
            print(f"   {result.error}")
            continue
        for label, tokens in PROMPT_SIZES:
            print(f"   speed: {label} prompt...")
            result.speed.append(measure_speed(client, model, label, tokens))
        print("   tool calls...")
        check_tool_calls(client, model, result)
        if suite:
            print(f"   agent tasks ({suite})...")
            result.tasks = run_tasks(model, suite, out, judge)
        try:
            client.unload_model(model, ignore_if_not_loaded=True)
        except LemonadeClientError as e:
            print(f"   warning: could not unload {model}: {e}")
    return results


def _cell(value, suffix=""):
    return "—" if value is None else f"{value}{suffix}"


def _speed_cell(s: Optional[SpeedResult], lead: str) -> str:
    """``TTFT / gen`` or ``prompt / gen`` for one prompt size."""
    if s is None or s.error:
        return "error" if s else "—"
    first = _cell(s.ttft_s, " s") if lead == "ttft" else _cell(s.prompt_tps, " t/s")
    return f"{first} / {_cell(s.decode_tps, ' t/s')}"


def report(results: List[ModelResult], ctx: int) -> str:
    lines = [
        f"Context size {ctx}. Speeds are Lemonade's own /stats for each request.",
        "",
        "| Model | Load | Short: TTFT / gen | 8K: prompt / gen | 32K: prompt / gen "
        "| Tool calls | Tasks passed | Quality |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        if r.fits is False or r.error:
            lines.append(f"| {r.model} | {r.error or r.fit_reason} ||||||| ")
            continue
        speed = {s.label: s for s in r.speed}
        tasks = r.tasks or {}
        passed = (
            f"{tasks['passed']}/{tasks['tasks']}"
            if "passed" in tasks
            else tasks.get("error", "—")
        )
        lines.append(
            f"| {r.model} | {_cell(r.load_s, ' s')} "
            f"| {_speed_cell(speed.get('short'), 'ttft')} "
            f"| {_speed_cell(speed.get('8K'), 'pp')} "
            f"| {_speed_cell(speed.get('32K'), 'pp')} "
            f"| {r.tool_calls_passed}/{r.tool_calls_total} "
            f"({_cell(r.tool_call_s, ' s')}) "
            f"| {passed} | {_cell(tasks.get('quality'))} |"
        )
    for r in results:
        for s in r.speed:
            if s.error:
                lines.append(f"\n{r.model} {s.label} prompt failed: {s.error}")
        for failure in r.tool_failures:
            lines.append(f"\n{r.model} tool call: {failure}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--models", nargs="+", default=list(DEFAULT_MODELS))
    parser.add_argument("--ctx", type=int, default=GPU_CTX_SIZE)
    parser.add_argument(
        "--tasks", metavar="SUITE", help="Also run `gaia eval tasks run --suite SUITE`"
    )
    parser.add_argument(
        "--no-judge", action="store_true", help="Run the task suite without grading"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("compare-models-" + time.strftime("%Y%m%d-%H%M%S")),
    )
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    results = compare(args.models, args.ctx, args.tasks, not args.no_judge, args.out)
    table = report(results, args.ctx)
    (args.out / "results.json").write_text(
        json.dumps([asdict(r) for r in results], indent=2), encoding="utf-8"
    )
    (args.out / "results.md").write_text(table + "\n", encoding="utf-8")
    print("\n" + table)
    print(f"\nSaved to {args.out}/results.md and results.json")
    # A failed request makes the comparison incomplete, not merely slower.
    broken = [
        r.model
        for r in results
        if r.error
        or any(s.error for s in r.speed)
        or any("request failed" in f for f in r.tool_failures)
        or (r.tasks or {}).get("error")
    ]
    if broken:
        print(f"Incomplete: requests failed for {', '.join(broken)} (see above).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
