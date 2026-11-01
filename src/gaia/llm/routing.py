# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Pick the model for a turn from a Lemonade router.

A Lemonade ``collection.router`` model is a routing policy registered as a
model: rules (and classifiers, such as an LLM that labels the request) choose
one of its candidate models. Lemonade applies the policy to every request, so
a multi-step agent turn could switch models between steps. GAIA instead asks
Lemonade for one decision per user turn, through ``/routing/validate``, and
runs the whole turn on that candidate: one tool format, one prompt cache, one
classifier call per turn.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from gaia.logger import get_logger

log = get_logger(__name__)

ROUTER_RECIPE = "collection.router"


class RoutingError(RuntimeError):
    """The router could not choose a model; the message says what to fix."""


@dataclass(frozen=True)
class RouteDecision:
    """Which candidate a router chose for a turn, and why."""

    router: str
    model: str
    rule: str
    label: Optional[str] = None
    rationale: Optional[str] = None
    #: False when a classifier the policy consulted gave no score: Lemonade then
    #: fails open to the default candidate, and that should be visible.
    classified: bool = True
    seconds: float = 0.0

    def as_record(self) -> Dict[str, Any]:
        return asdict(self)


def router_policy(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The policy document of a catalog entry, or None when it is not a router."""
    if entry.get("recipe") != ROUTER_RECIPE:
        return None
    routing = entry.get("routing")
    components = entry.get("components")
    if not isinstance(routing, dict) or not components:
        raise RoutingError(
            f"Router {entry.get('id')!r} is listed without its routing policy. "
            "Re-register it with `POST /api/v1/pull` and the policy JSON."
        )
    return {
        "version": str(entry.get("version") or "1"),
        "recipe": ROUTER_RECIPE,
        "components": list(components),
        "routing": routing,
    }


def _classifier_steps(decision: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        step
        for step in decision.get("trace") or []
        if isinstance(step, dict)
        and str(step.get("condition", "")).startswith("classifier")
    ]


def route_turn(
    client: Any,
    model: Optional[str],
    prompt: str,
    *,
    has_tools: bool = True,
    metadata: Optional[Dict[str, str]] = None,
) -> Optional[RouteDecision]:
    """The candidate *model*'s router picks for *prompt*, or None if it is no router.

    *client* is a :class:`~gaia.llm.lemonade_client.LemonadeClient` for the
    server the router is registered on.
    """
    if not model:
        return None
    try:
        entry = client.get_model_details(model)
    except Exception as exc:  # noqa: BLE001 - re-raised below for router-looking names
        if model.startswith("user."):
            raise RoutingError(f"Could not read model {model!r}: {exc}") from exc
        return None  # not in the catalog under that name: not a router
    policy = router_policy(entry)
    if policy is None:
        return None
    body: Dict[str, Any] = {"policy": policy, "prompt": prompt, "has_tools": has_tools}
    if metadata:
        body["metadata"] = dict(metadata)
    started = time.monotonic()
    try:
        reply = client._send_request(  # pylint: disable=protected-access
            "post", f"{client.base_url}/routing/validate", data=body
        )
    except Exception as exc:  # noqa: BLE001 - re-raised with the remedy
        raise RoutingError(
            f"Router {model!r} could not route this turn: {exc}. Check the policy "
            "with `POST /api/v1/routing/validate`, or run with a model name instead."
        ) from exc
    decision = (reply or {}).get("decision") or {}
    chosen = decision.get("route_to")
    if chosen not in policy["routing"].get("candidates", []):
        raise RoutingError(
            f"Router {model!r} chose {chosen!r}, which is not one of its candidates."
        )
    steps = _classifier_steps(decision)
    # A condition's label is the one it tested, so it names the choice only if it matched.
    matched = next((s for s in steps if s.get("result") is True), {})
    route = RouteDecision(
        router=model,
        model=chosen,
        rule=decision.get("matched_rule") or "default",
        label=matched.get("label"),
        rationale=matched.get("rationale"),
        classified=all("score" in s for s in steps),
        seconds=round(time.monotonic() - started, 2),
    )
    if not route.classified:
        log.warning(
            "Router %s: a classifier gave no answer, so Lemonade used its default %s",
            model,
            route.model,
        )
    log.info(
        "Router %s chose %s (rule %s, label %s)",
        model,
        route.model,
        route.rule,
        route.label,
    )
    return route
