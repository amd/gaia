# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""What fills a chat's context window, and where a turn's time went.

Feeds the Agent UI's Context & performance panel. Every number is measured or
absent: a value nobody reported is ``None``, which the panel shows as
"unknown", never as a zero or a guess.

Turn timing comes from what the agent already records on every turn — the
step timer's model / tool split and each model call's own backend stats — so
there is no second recorder. :func:`summarize_turn` is the one place that reads
them; pointing it at the per-turn span recorder (``turn_metrics``) once that is
always on changes nothing downstream.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

_ROUND = 3


# ── Context ────────────────────────────────────────────────────────────────


def _lemonade() -> Tuple[str, Dict[str, str]]:
    from gaia.llm.lemonade_client import (
        lemonade_auth_headers,
        resolve_lemonade_api_key,
        resolve_lemonade_base_url,
    )
    from gaia.llm.lemonade_manager import LemonadeManager

    base_url = LemonadeManager.get_base_url() or resolve_lemonade_base_url()
    return base_url, lemonade_auth_headers(resolve_lemonade_api_key(base_url=base_url))


def _loaded_chat_model(base_url: str, headers: Dict[str, str]) -> Optional[str]:
    import httpx

    from gaia.llm.model_server_info import is_chat_model_entry

    resp = httpx.get(f"{base_url}/health", timeout=5.0, headers=headers)
    resp.raise_for_status()
    for entry in resp.json().get("all_models_loaded") or []:
        if is_chat_model_entry(entry) and entry.get("recipe") != "cloud":
            return entry.get("model_name")
    return None


def where_model_runs(model_id: Optional[str]) -> Optional[str]:
    """``local``, a cloud provider name, or None when GAIA cannot tell yet.

    A dotted id that is neither a known local model nor in the cloud catalog
    stays unknown rather than being guessed local (and priced at $0).
    """
    from gaia.llm.lemonade_client import (
        cloud_model_provider,
        is_cloud_model,
        may_be_cloud_model,
    )

    if not model_id:
        return None
    metadata = {"recipe": "cloud"} if is_cloud_model(model_id) else None
    provider = cloud_model_provider(model_id, metadata)
    if provider:
        return provider
    return None if may_be_cloud_model(model_id) else "local"


def _same_model(a: Optional[str], b: Optional[str]) -> bool:
    def bare(name: Optional[str]) -> str:
        name = (name or "").lower()
        return name[5:] if name.startswith("user.") else name

    return bool(a and b) and bare(a) == bare(b)


class ModelTokenCounter:
    """Counts tokens with the loaded model's own tokenizer, via Lemonade.

    Only used when *model_id* is the chat model Lemonade has loaded: asking
    ``/tokenize`` never loads or swaps a model, and another model's tokenizer
    would be measuring the wrong thing.
    """

    def __init__(self, base_url: str, headers: Dict[str, str]) -> None:
        import httpx

        self._client = httpx.Client(timeout=10.0, headers=headers)
        self._url = f"{base_url}/tokenize"

    def __call__(self, text: str) -> int:
        if not text:
            return 0
        resp = self._client.post(self._url, json={"content": text})
        resp.raise_for_status()
        tokens = resp.json().get("tokens")
        if not isinstance(tokens, list):
            raise ValueError(
                f"Lemonade /tokenize returned no token list: {resp.text[:200]}"
            )
        return len(tokens)

    def close(self) -> None:
        self._client.close()


def measure_context(agent: Any, turns: list, model_id: Optional[str]) -> Dict[str, Any]:
    """``context_usage`` measured with the model's tokenizer where it can be.

    ``tokenizer.exact`` is False, with the reason, whenever the counts are the
    shared estimate instead — a cloud model, another model loaded, or a
    Lemonade that does not answer ``/tokenize``.
    """
    from gaia.agents.base.history import context_usage

    def estimate(reason: str) -> Dict[str, Any]:
        usage = context_usage(agent, turns)
        usage["tokenizer"] = {"exact": False, "reason": reason}
        return usage

    if where_model_runs(model_id) not in ("local", None):
        return estimate("The cloud model's tokenizer is not available here.")
    base_url, headers = _lemonade()
    try:
        loaded = _loaded_chat_model(base_url, headers)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning(
            "context: could not read Lemonade /health at %s: %s", base_url, exc
        )
        return estimate(f"The model server did not answer at {base_url}.")
    if not _same_model(loaded, model_id):
        return estimate(
            f"{model_id} is not loaded now, so its tokenizer cannot be asked."
        )
    counter = ModelTokenCounter(base_url, headers)
    try:
        usage = context_usage(agent, turns, count=counter)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("context: Lemonade /tokenize failed at %s: %s", base_url, exc)
        return estimate(f"The model server could not tokenize: {exc}")
    finally:
        counter.close()
    try:
        still = _loaded_chat_model(base_url, headers)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("context: could not re-read Lemonade /health: %s", exc)
        still = None
    if not _same_model(still, model_id):
        # The model changed under the count: the numbers are another tokenizer's.
        return estimate("The loaded model changed while measuring.")
    usage["tokenizer"] = {"exact": True, "model": loaded}
    return usage


def context_categories(usage: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The panel's categories, in order. Their tokens add up to ``usage['used']``."""
    system = usage["system"]
    skills = min(usage.get("skills", 0), system)
    memory = min(usage.get("memory", 0), system - skills)
    definitions = usage.get("tool_definitions", usage["tools"])
    rows = [
        ("system", "System prompt", system - skills - memory),
        ("tools", "Tool definitions", definitions),
        ("skills", "Skills", skills),
        ("memory", "Memory", memory),
        ("documents", "Documents", usage["documents"]),
        ("messages", "Messages", usage["history"] + usage["tools"] - definitions),
    ]
    if usage.get("query"):
        rows.append(("query", "Your message", usage["query"]))
    return [
        {"key": key, "label": label, "tokens": tokens} for key, label, tokens in rows
    ]


def tools_by_load(agent: Any) -> Dict[str, List[str]]:
    """Tools sent with every request, and the ones loaded only when needed."""
    registry = sorted(getattr(agent, "_tools_registry", None) or {})
    active = getattr(agent, "_active_tool_filter", None)
    if active is None:
        return {"loaded": registry, "on_demand": []}
    loaded = [t for t in active if t in registry]
    return {
        "loaded": loaded,
        "on_demand": [t for t in registry if t not in set(loaded)],
    }


#: ``context_state()`` keys and the integer fields each must carry.
CONTEXT_STATE_FIELDS = {
    "put_away": ("results", "tokens"),
    "summarized": ("turns", "tokens", "summary_tokens"),
}


def context_state(agent: Any) -> Optional[Dict[str, Any]]:
    """What the agent moved out of the window to keep going, or None if it can't say.

    The agent reports it through an optional ``context_state()`` method:
    ``put_away`` counts tool results already used and swapped for a re-read
    handle; ``summarized`` counts older turns folded into a rolling summary
    (``tokens`` before, ``summary_tokens`` after). Either may be None.
    """
    report = getattr(agent, "context_state", None)
    if not callable(report):
        return None
    state = report()
    if not isinstance(state, dict):
        raise TypeError(
            f"{type(agent).__name__}.context_state() returned {state!r}, not a dict"
        )
    out: Dict[str, Any] = {}
    for key, fields in CONTEXT_STATE_FIELDS.items():
        part = state.get(key)
        if part is None:
            out[key] = None
            continue
        bad = [f for f in fields if not isinstance(part.get(f), int) or part[f] < 0]
        if bad:
            raise ValueError(
                f"{type(agent).__name__}.context_state()['{key}'] needs non-negative "
                f"integers for {', '.join(bad)}; got {part!r}"
            )
        out[key] = {f: part[f] for f in fields}
    return out


# ── Turn timing ────────────────────────────────────────────────────────────


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _step_stats(conversation: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for entry in conversation or []:
        content = entry.get("content") if isinstance(entry, dict) else None
        if (
            entry.get("role") == "system"
            and isinstance(content, dict)
            and content.get("type") == "stats"
        ):
            out.append(content)
    return out


def _sum(values: Iterable[Optional[float]]) -> Optional[float]:
    """The total, or None unless every call reported it: a partial sum would undercount."""
    values = list(values)
    if not values or any(v is None for v in values):
        return None
    return sum(values)


def _call(stats: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """One model call's numbers, from either ``/stats`` or a response's usage."""
    prompt = _num(stats.get("prompt_tokens"))
    cached = _num(stats.get("cache_tokens"))
    if cached is None:
        cached = _num(stats.get("cached_tokens"))
    # /stats reports the uncached part as input_tokens; usage does not.
    new = _num(stats.get("input_tokens")) if "cache_tokens" in stats else None
    if new is None and prompt is not None and cached is not None:
        new = max(0.0, prompt - cached)
    if prompt is None and new is not None and cached is not None:
        prompt = new + cached
    out = _num(stats.get("completion_tokens"))
    if out is None:
        out = _num(stats.get("output_tokens"))
    return {
        "prompt": prompt,
        "cached": cached,
        "new": new,
        "output": out,
        "ttft": _num(stats.get("time_to_first_token")),
        "rate": _num(stats.get("tokens_per_second")) or None,
        "load": _num(stats.get("model_load_seconds")),
    }


def _cost(model_id: Optional[str], tokens_in, cached, tokens_out) -> Dict[str, Any]:
    where = where_model_runs(model_id)
    if where == "local":
        return {"kind": "local", "usd": 0.0}
    if where is None or tokens_in is None or tokens_out is None:
        return {"kind": "unknown", "usd": None}
    from gaia.eval.bench.metering import price

    usd = price(model_id, int(tokens_in), int(cached or 0), int(tokens_out))
    if usd is None:
        return {"kind": "unknown", "usd": None}
    return {"kind": "estimate", "usd": round(usd, 6)}


def summarize_turn(
    result: Dict[str, Any],
    *,
    wall_seconds: float,
    model_id: Optional[str],
    loads: Iterable[Dict[str, Any]] = (),
    attempts: int = 1,
) -> Dict[str, Any]:
    """One turn's performance, from the agent's result and the turn's wall time.

    *attempts* above 1 means the turn was retried: the failed attempt's model
    time was never recorded, so the model / GAIA split is unknown.

    ``model_seconds + gaia_seconds == wall_seconds`` exactly: model time is
    measured (each request's wall time, plus any model load before the turn),
    and GAIA's time is the rest of the measured wall time, split into tools,
    waiting on the user, and GAIA's own work.
    """
    timing = result.get("timing_summary") or {}
    steps = _step_stats(result.get("conversation") or [])
    calls = [_call(s.get("performance_stats") or {}) for s in steps]
    loads = [dict(entry) for entry in loads]
    for call in calls:
        if call["load"]:
            loads.append(
                {"model": model_id, "seconds": call["load"], "during": "request"}
            )
    preload = sum(e["seconds"] for e in loads if e.get("during") != "request")

    tools = [t for s in steps for t in s.get("tools") or []]
    waited = sum(_num(t.get("approval_wait_seconds")) or 0.0 for t in tools)
    tool_s = _num(timing.get("tool_seconds")) or 0.0
    llm_s = _num(timing.get("llm_seconds"))

    wall = round(wall_seconds, _ROUND)
    model = (
        round(llm_s + preload, _ROUND) if llm_s is not None and attempts == 1 else None
    )
    split: Dict[str, Any] = {
        "wall_seconds": wall,
        "model_seconds": model,
        "gaia_seconds": None,
        "tool_seconds": round(tool_s - waited, _ROUND),
        "waiting_seconds": round(waited, _ROUND),
        "own_seconds": None,
    }
    if model is not None:
        model = min(model, wall)
        split["model_seconds"] = model
        split["gaia_seconds"] = round(wall - model, _ROUND)
        split["own_seconds"] = round(
            split["gaia_seconds"] - split["tool_seconds"] - split["waiting_seconds"],
            _ROUND,
        )

    new = _sum(c["new"] for c in calls)
    prefill_pairs = [(c["new"], c["ttft"]) for c in calls if c["new"] and c["ttft"]]
    prefill = (
        round(sum(n for n, _ in prefill_pairs) / sum(t for _, t in prefill_pairs), 1)
        if prefill_pairs
        else None
    )
    gen_pairs = [(c["output"], c["rate"]) for c in calls if c["output"] and c["rate"]]
    generation = (
        round(sum(o for o, _ in gen_pairs) / sum(o / r for o, r in gen_pairs), 1)
        if gen_pairs
        else None
    )
    tokens_in = _sum(c["prompt"] for c in calls)
    cached = _sum(c["cached"] for c in calls)
    tokens_out = _sum(c["output"] for c in calls)
    return {
        "model": model_id,
        "time": split,
        "first_token_seconds": calls[0]["ttft"] if calls else None,
        "prefill_tokens_per_second": prefill,
        "generation_tokens_per_second": generation,
        "model_calls": int(timing.get("llm_calls") or len(calls)),
        "tokens_in": tokens_in,
        "tokens_new": new,
        "tokens_cached": cached,
        "tokens_out": tokens_out,
        # The whole templated prompt of the turn's last request, as the server counted it.
        "last_prompt_tokens": calls[-1]["prompt"] if calls else None,
        "tool_calls": len(tools),
        "tools": [
            {"name": t.get("name"), "seconds": _num(t.get("seconds"))} for t in tools
        ],
        "loads": loads,
        "cost": _cost(model_id, tokens_in, cached, tokens_out),
    }


def chat_totals(turns: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Sums over a chat's measured turns; a total of nothing measured is None."""

    def total(get: Callable[[Dict[str, Any]], Any]) -> Optional[float]:
        values = [_num(get(t)) for t in turns]
        known = [v for v in values if v is not None]
        return round(sum(known), _ROUND) if known else None

    costs = [t.get("cost") or {} for t in turns]
    return {
        "turns": len(turns),
        "wall_seconds": total(lambda t: t["time"]["wall_seconds"]),
        "model_seconds": total(lambda t: t["time"]["model_seconds"]),
        "gaia_seconds": total(lambda t: t["time"]["gaia_seconds"]),
        "tokens_in": total(lambda t: t.get("tokens_in")),
        "tokens_cached": total(lambda t: t.get("tokens_cached")),
        "tokens_out": total(lambda t: t.get("tokens_out")),
        "tool_calls": sum(t.get("tool_calls") or 0 for t in turns),
        "loads": sum(len(t.get("loads") or []) for t in turns),
        "load_seconds": total(
            lambda t: sum(e["seconds"] for e in t.get("loads") or []) or None
        ),
        "usd": round(sum(c.get("usd") or 0.0 for c in costs), 6),
        "unpriced_turns": sum(1 for c in costs if c.get("kind") == "unknown"),
    }


# ── The machine, now ───────────────────────────────────────────────────────


def model_now(model_id: Optional[str]) -> Dict[str, Any]:
    """Where *model_id* runs and the memory it holds, read from Lemonade now."""
    from gaia.llm.lemonade_client import (
        LemonadeClient,
        LemonadeClientError,
        find_model_requirement,
    )
    from gaia.llm.model_server_info import describe_loaded_model

    where = where_model_runs(model_id)
    out: Dict[str, Any] = {
        "model": model_id,
        "provider": where or "unknown",
        "loaded": None,
        "device": None,
        "backend": None,
        "ctx_size": None,
        "weights_gb": None,
        "kv_cache_gb": None,
        "free_gb": None,
        "error": None,
    }
    if model_id is None or where not in ("local", None):
        return out
    client = LemonadeClient(verbose=False)
    try:
        health = client.health_check(timeout=5)
        system_info = client.get_system_info(timeout=5)
    except LemonadeClientError as exc:
        out["error"] = f"The model server did not answer at {client.base_url}: {exc}"
        return out
    entry = next(
        (
            m
            for m in health.get("all_models_loaded") or []
            if _same_model(m.get("model_name"), model_id)
        ),
        None,
    )
    out["loaded"] = entry is not None
    if entry is not None and where is None:
        out["provider"] = "cloud" if entry.get("recipe") == "cloud" else "local"
    if entry is not None:
        loaded = describe_loaded_model(entry, system_info)
        out.update(
            device=loaded.get("device"),
            backend=loaded.get("backend"),
            ctx_size=loaded.get("ctx_size"),
        )
    requirement = find_model_requirement(model_id)
    if requirement is not None and requirement.size_gb:
        out["weights_gb"] = requirement.size_gb
        if requirement.kv_bytes_per_token and out["ctx_size"]:
            from gaia.llm.model_fit import kv_cache_gb

            out["kv_cache_gb"] = round(
                kv_cache_gb(requirement.kv_bytes_per_token, out["ctx_size"]), 2
            )
    from gaia.llm.model_fit import ModelFitError, free_memory, read_os_memory

    try:
        free = free_memory(system_info, read_os_memory())
    except ModelFitError as exc:
        logger.warning("context: free memory unknown: %s", exc)
        free = None
    if free is not None:
        out["free_gb"] = round(free.free_gb, 1)
    return out
