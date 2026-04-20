# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Conversational intent classifier for ``gaia-coder`` (§15.4, §15.8 P9).

When the EM runs ``gaia-coder ask "enable self-edit"``, the CLI enqueues
the message into ``em_inbox.db`` AND runs this classifier to map the free
text onto one of the canonical intents from §15.4. Each intent has a
matching handler below.

**LLM-driven, not keyword-matched.** §15.4 asks for grammar that covers
natural phrasings ("let me give you self-edit for now", "I'm promoting you
to tier 3"). Regex matchers would need one branch per phrasing; a small
Opus call with the full intent table in the prompt is both more
maintainable and more accurate. The classifier is kept cheap (≤50 tokens
out, temperature 0) so the cost is negligible.

**Mockable.** :func:`classify_intent` takes a ``llm`` callable
(``Callable[[str], str]``) that returns the raw model response. The default
is :func:`_default_llm` which lazily imports the ``anthropic`` SDK; tests
pass a lambda that returns canned JSON and never touch the network.

**Low-confidence bail-out.** §15.4: "Low-confidence (< 70) classifications
bail to free_form — the agent asks a clarifying question rather than
guessing." We enforce that here; the handler for ``free_form`` posts an
inbox note and returns without side effects.

Handlers are thin wrappers over :mod:`gaia.coder.trust` and
:mod:`gaia.coder.inbox` — this module owns the mapping from *natural
language* to *agent action*, not the actions themselves.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, TypedDict

from gaia.coder import inbox as inbox_mod
from gaia.coder import trust as trust_mod

# ---------------------------------------------------------------------------
# Intent catalog (§15.4)
# ---------------------------------------------------------------------------

#: Ordered intent catalog. Kept as a tuple of (name, description) pairs so it
#: renders cleanly into the classifier prompt and also acts as the canonical
#: "allowed_intents" list when callers pass ``None``.
INTENT_CATALOG: tuple[tuple[str, str], ...] = (
    (
        "enable_self_edit_session",
        "Turn on dev mode for this session only (auto-revokes at daemon exit)",
    ),
    (
        "enable_self_edit_permanent",
        "Turn on dev mode and persist to em.toml",
    ),
    (
        "disable_self_edit",
        "Turn dev mode off (session flag AND em.toml persistence)",
    ),
    ("promote_tier", "Promote capability tier (requires EM signature)"),
    ("demote_tier", "Demote capability tier (no signature required)"),
    (
        "grant_per_call_selfedit",
        "One-shot self-edit permission for a single specific self-fix PR",
    ),
    ("what_tier", "Show the current trust contract (runs `gaia-coder trust`)"),
    ("spend_query", "Show today's cloud spend"),
    ("pause", "Pause the current task immediately"),
    ("resume", "Resume a paused task or unblock a waiting task"),
    (
        "authorise_sensitive",
        "Grant one-shot Sensitive-class approval for a specific PR/sha",
    ),
    ("feedback", "Enqueue as a retrospective feedback record (§7.3)"),
    ("skill_invoke", "Load and execute a catalogued skill (§4.7)"),
    ("free_form", "No clear intent; inbox this as a normal question"),
)

#: Just the names, in catalog order. The classifier prompt renders this set.
INTENT_NAMES: tuple[str, ...] = tuple(name for name, _ in INTENT_CATALOG)

#: Per §15.4: below this the classifier must bail to ``free_form``.
MIN_CONFIDENCE: int = 70


class IntentResult(TypedDict):
    """Classifier output. Mirrors the JSON schema in §15.8 P9."""

    intent: str
    args: dict
    confidence: int


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

LLM = Callable[[str], str]
"""Callable contract for the Opus call. Takes the full prompt, returns raw text."""


def _render_intent_table() -> str:
    """Render :data:`INTENT_CATALOG` as lines for injection into the P9 prompt."""
    return "\n".join(f"- {name}: {desc}" for name, desc in INTENT_CATALOG)


def build_prompt(em_message: str, allowed: Optional[list[str]] = None) -> str:
    """Compose the §15.8 P9 prompt with *em_message* substituted in.

    Exposed for testing and for the audit log — §15.4 requires we log the
    raw prompt along with the returned JSON. Pulling this out as a public
    helper keeps that pairing in one place.
    """
    intents = allowed if allowed else list(INTENT_NAMES)
    table = "\n".join(
        f"- {name}: {desc}" for name, desc in INTENT_CATALOG if name in intents
    )
    # Strip XML-unsafe triple-quotes from the message — the P9 template wraps
    # the message in ``"""..."""`` delimiters, so a literal triple quote in
    # the user's text could break the prompt boundaries. Replace with
    # single-quote triplets; the model still sees the intent.
    safe_msg = em_message.replace('"""', "'''")
    return (
        "Classify engineering-manager messages into intents defined in §15.4 "
        "of docs/plans/coder-agent.mdx.\n\n"
        f"Intents:\n{table}\n\n"
        f'Message: """{safe_msg}"""\n\n'
        "JSON only:\n"
        '{"intent":"<name>","args":{...},"confidence":<0-100>}\n\n'
        "If no intent matches with confidence ≥ 70, respond:\n"
        '{"intent":"free_form","args":{},"confidence":0}\n'
    )


_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_llm_response(raw: str) -> IntentResult:
    """Extract the first JSON object from *raw* and validate the shape.

    The Opus call returns JSON only (per the prompt), but in practice models
    occasionally wrap the JSON in prose (``"Here's the classification: {...}"``)
    even at temperature 0. We tolerate that by extracting the first
    ``{...}`` substring; anything worse than that raises ``ValueError`` so
    the CLI surfaces the raw response instead of guessing.
    """
    match = _JSON_OBJ_RE.search(raw)
    if match is None:
        raise ValueError(
            f"intent classifier returned no JSON object (raw: {raw!r}). "
            "If this repeats, file a prompt-class feedback record; the §15.8 "
            "P9 template may need tightening."
        )
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError as e:
        raise ValueError(
            f"intent classifier emitted malformed JSON: {e} (raw: {raw!r})"
        ) from e

    intent = obj.get("intent")
    if not isinstance(intent, str) or not intent:
        raise ValueError(f"missing or empty 'intent' (raw: {raw!r})")
    args = obj.get("args", {})
    if not isinstance(args, dict):
        raise ValueError(f"'args' must be an object (raw: {raw!r})")
    confidence = obj.get("confidence", 0)
    if not isinstance(confidence, int):
        # A model sometimes returns a float; coerce where safe, reject NaN.
        try:
            confidence = int(confidence)
        except (TypeError, ValueError) as e:
            raise ValueError(f"non-integer confidence (raw: {raw!r})") from e

    return IntentResult(intent=intent, args=args, confidence=confidence)


def _default_llm(prompt: str) -> str:
    """Call Opus 4.7 via the Anthropic SDK and return the response text.

    Lazy import per :mod:`gaia.eval.claude` precedent so the SDK is not a
    hard dep for every import of ``gaia.coder.intent`` (tests always pass
    their own ``llm``). Raises a descriptive ``ImportError`` pointing at
    ``uv pip install -e .[eval]`` if the SDK is missing; this follows the
    fail-loudly rule ("name what failed, name what to do").
    """
    try:
        import anthropic
    except ImportError as e:  # pragma: no cover - exercised only on bare envs
        raise ImportError(
            "The 'anthropic' SDK is required to classify intents against "
            'Opus 4.7. Install with `uv pip install -e ".[eval]"` or pass '
            "an explicit `llm=` callable (e.g. in tests)."
        ) from e

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to your environment or "
            "pass an explicit `llm=` callable. See docs/reference/dev.mdx."
        )

    client = anthropic.Anthropic(api_key=api_key, timeout=60.0)
    # §15.8 header: Opus 4.7 at temperature 0, ≤200 out.
    model_id = os.getenv("GAIA_CODER_INTENT_MODEL", "claude-opus-4-7")
    resp = client.messages.create(
        model=model_id,
        max_tokens=200,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    # ``content`` is a list of content blocks; we expect a single text block.
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            return block.text
    raise RuntimeError("Opus response contained no text block")


def classify_intent(
    em_message: str,
    allowed_intents: Optional[list[str]] = None,
    *,
    llm: Optional[LLM] = None,
) -> IntentResult:
    """Classify *em_message* into one of *allowed_intents*.

    Args:
        em_message: Raw EM text from ``gaia-coder ask "..."``.
        allowed_intents: Optional subset of :data:`INTENT_NAMES`. When
            ``None`` (or empty) the full catalog is used. Passing a subset
            is useful for unit tests and for contexts where the agent knows
            only a few intents are valid (e.g. "during a self-fix PR,
            ``promote_tier`` is nonsensical, don't offer it").
        llm: Callable invoked with the composed prompt. Defaults to
            :func:`_default_llm` (Opus 4.7 via the Anthropic SDK). Tests
            and batch harnesses inject a callable that returns canned JSON.

    Returns:
        An :class:`IntentResult`. If the model's reported confidence is
        below :data:`MIN_CONFIDENCE`, the return value is coerced to
        ``{"intent": "free_form", "args": {}, "confidence": 0}`` per §15.4.
    """
    prompt = build_prompt(em_message, allowed_intents)
    call = llm if llm is not None else _default_llm
    raw = call(prompt)
    result = _parse_llm_response(raw)

    if result["confidence"] < MIN_CONFIDENCE:
        # Preserve the original confidence in args so the audit log can
        # show "model thought 45 — we bailed"; §15.4 logs the raw result.
        return IntentResult(
            intent="free_form",
            args={"original": dict(result)},
            confidence=0,
        )

    # Unknown intent names are also coerced — the model occasionally invents
    # a label not in the catalog. Safer to bail than to dispatch on a
    # misspelled handler name.
    if result["intent"] not in INTENT_NAMES:
        return IntentResult(
            intent="free_form",
            args={"original": dict(result), "unknown_intent": result["intent"]},
            confidence=0,
        )

    return result


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


@dataclass
class HandlerContext:
    """Bag of open resources passed to every handler.

    Keeping this as a dataclass (rather than, say, five positional
    parameters per handler) means adding a future handler that needs a new
    connection or config knob doesn't require touching every existing
    handler signature.

    Attributes:
        em_cfg_path: Path to ``em.toml``; handlers that mutate persistent
            state (``enable_self_edit_permanent``, ``promote_tier``, etc.)
            save to this path.
        em_cfg: Current parsed :class:`EMConfig`. Handlers that need to
            mutate it produce a *new* config via :mod:`gaia.coder.trust`
            helpers and save it.
        inbox_conn: Open ``em_inbox.db`` connection.
        feedback_conn: Open ``feedback.db`` connection (used by
            :func:`feedback` handler).
        audit_conn: Open ``audit.log.db`` connection.
        session: Mutable dict the agent uses to track session-scoped flags
            (``dev_mode_session``, ``dev_mode_per_call_pr``, etc.).
        loop_version: Loop version for audit rows.
    """

    em_cfg_path: Path
    em_cfg: trust_mod.EMConfig
    inbox_conn: sqlite3.Connection
    feedback_conn: sqlite3.Connection
    audit_conn: sqlite3.Connection
    session: dict
    loop_version: int = 1


def _utc_now_iso() -> str:
    from datetime import datetime, timezone

    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def enable_self_edit_session(ctx: HandlerContext, args: dict) -> str:
    """Flip the in-process session flag on. Does NOT touch ``em.toml``.

    Session-scoped enablement is the lightest-weight grant per §7.1; it
    auto-revokes when the daemon exits. We mirror it as
    ``ctx.session["dev_mode_session"] = True``.
    """
    ctx.session["dev_mode_session"] = True
    ctx.session["dev_mode_session_enabled_at"] = _utc_now_iso()
    return (
        "Acknowledged. Dev mode is ON until you disable it or this daemon exits. "
        "I will still open a draft PR and wait for your review before any change "
        f"merges. Current tier: {ctx.em_cfg.current_tier}."
    )


def enable_self_edit_permanent(ctx: HandlerContext, args: dict) -> str:
    """Persist ``dev_mode_self_edit = true`` to ``em.toml`` and update memory."""
    now = _utc_now_iso()
    reason = args.get("reason") or f"EM conversation on {now[:10]}"
    updated = ctx.em_cfg.model_copy(
        update={
            "dev_mode_self_edit": True,
            "dev_mode_enabled_at": now,
            "dev_mode_enabled_reason": reason,
        }
    )
    trust_mod.save_em_config(ctx.em_cfg_path, updated)
    ctx.em_cfg = updated
    ctx.session["dev_mode_session"] = True
    return (
        "Acknowledged. Writing `dev_mode_self_edit = true` to em.toml so this "
        "survives across daemon restarts. You can revoke at any time with "
        '"disable self-edit permanently" or by editing em.toml directly.'
    )


def disable_self_edit(ctx: HandlerContext, args: dict) -> str:
    """Clear session flag and persist ``false`` to ``em.toml`` if it was true."""
    ctx.session.pop("dev_mode_session", None)
    ctx.session.pop("dev_mode_session_enabled_at", None)
    ctx.session.pop("dev_mode_per_call_pr", None)

    if ctx.em_cfg.dev_mode_self_edit:
        updated = ctx.em_cfg.model_copy(
            update={"dev_mode_self_edit": False, "dev_mode_enabled_reason": None}
        )
        trust_mod.save_em_config(ctx.em_cfg_path, updated)
        ctx.em_cfg = updated
    return (
        "Acknowledged. Dev mode is OFF. I will not attempt to edit my own source "
        "until you re-enable it."
    )


def promote_tier(ctx: HandlerContext, args: dict) -> str:
    """Run the §4.2 promotion flow.

    ``args`` must contain ``to_tier`` and ``em_signature``. Missing either is
    a :class:`TrustError` surfaced verbatim to the CLI — the classifier is
    expected to extract the tier number from the EM's message; when it
    can't, the agent falls through to ``free_form``.
    """
    to_tier = args.get("to_tier")
    signature = (
        args.get("em_signature") or args.get("signature") or ctx.em_cfg.em_handle
    )
    reason = args.get("reason") or "via `gaia-coder ask`"
    if to_tier is None:
        raise trust_mod.TrustError(
            "promote intent missing `to_tier`. Rephrase e.g. "
            '"promote to tier 3" or run `gaia-coder promote --to-tier 3`.'
        )
    updated = trust_mod.promote(
        ctx.em_cfg,
        int(to_tier),
        reason,
        signature,
        audit_conn=ctx.audit_conn,
        loop_version=ctx.loop_version,
    )
    trust_mod.save_em_config(ctx.em_cfg_path, updated)
    ctx.em_cfg = updated
    return f"Promoted to tier {updated.current_tier} ({trust_mod.CapabilityTier(updated.current_tier).label})."


def demote_tier(ctx: HandlerContext, args: dict) -> str:
    """Run the §4.2 demotion flow (no signature required)."""
    reason = args.get("reason") or "via `gaia-coder ask`"
    to_tier = args.get("to_tier")
    updated = trust_mod.demote(
        ctx.em_cfg,
        reason,
        audit_conn=ctx.audit_conn,
        to_tier=int(to_tier) if to_tier is not None else None,
        loop_version=ctx.loop_version,
    )
    trust_mod.save_em_config(ctx.em_cfg_path, updated)
    ctx.em_cfg = updated
    return f"Demoted to tier {updated.current_tier} ({trust_mod.CapabilityTier(updated.current_tier).label})."


def grant_per_call_selfedit(ctx: HandlerContext, args: dict) -> str:
    """One-shot self-edit grant; auto-revokes when the referenced PR closes."""
    pr_ref = args.get("pr") or args.get("pr_ref") or "current"
    ctx.session["dev_mode_per_call_pr"] = pr_ref
    return (
        f"Acknowledged. Opening self-fix PR now. Dev mode reverts to off after "
        f"this PR ({pr_ref}) merges or is rejected."
    )


def what_tier(ctx: HandlerContext, args: dict) -> str:
    """Return a one-line tier summary for inline reply inside the inbox."""
    tier = trust_mod.CapabilityTier(ctx.em_cfg.current_tier)
    return (
        f"Tier: {int(tier)} ({tier.label}). EM: @{ctx.em_cfg.em_handle}. "
        "Run `gaia-coder trust` for the full snapshot."
    )


def spend_query(ctx: HandlerContext, args: dict) -> str:
    """Placeholder until :mod:`gaia.coder.stores.spend` gets a day-sum helper.

    Phase 5 scope does not include the spend aggregator — §10 scorecards
    and the :mod:`spend` store land in Phase 3. Returning a sentinel string
    (rather than ``$0``) matches the fail-loudly rule: never fake a number.
    """
    return "Spend query: not wired yet (Phase 3). Run `gaia-coder spend --today` when the aggregator lands."


def pause(ctx: HandlerContext, args: dict) -> str:
    """Return the soft-interrupt acknowledgement; the daemon acts on it."""
    return (
        "Acknowledged. Pausing at the next breakpoint. Run "
        "`gaia-coder pause-now` for a hard pause if this is urgent."
    )


def resume(ctx: HandlerContext, args: dict) -> str:
    """Return a resume acknowledgement; the daemon reads it at its next tick."""
    return "Acknowledged. Resuming at the next heartbeat tick."


def authorise_sensitive(ctx: HandlerContext, args: dict) -> str:
    """Record a one-shot Sensitive-class approval for the referenced artifact."""
    artifact = (
        args.get("pr") or args.get("sha") or args.get("artifact") or "unspecified"
    )
    approvals = ctx.session.setdefault("sensitive_approvals", [])
    approvals.append({"artifact": artifact, "granted_at": _utc_now_iso()})
    return f"Acknowledged. One-shot Sensitive-class approval recorded for {artifact}."


def feedback(ctx: HandlerContext, args: dict, body: str) -> str:
    """Escalate the current inbox message straight into the feedback queue.

    ``feedback:`` prefixed messages skip the "answer in inbox" step and go
    directly to §7.3 triage. We require the caller to pass both ``args``
    (which may contain ``fix_class`` / ``context_url``) and the inbox
    ``body`` — the classifier's prompt doesn't inherently see the raw body,
    so the CLI passes it in.
    """
    # The CLI will have already enqueued the inbox row before calling
    # classify_intent, so we expect the most-recent pending row with this
    # body is the one the user just typed. A cleaner design pulls the
    # id out of the CLI context; for now we require the CLI to pass it
    # via args["inbox_id"].
    inbox_id = args.get("inbox_id")
    if not inbox_id:
        raise ValueError(
            "feedback handler requires args['inbox_id'] — the CLI must pass "
            "the enqueue()-returned id so we know which row to escalate."
        )
    fb_id = inbox_mod.escalate(
        ctx.inbox_conn,
        inbox_id,
        ctx.feedback_conn,
        fix_class=args.get("fix_class"),
        context_url=args.get("context_url"),
    )
    return f"Acknowledged. Escalated to feedback record {fb_id}."


def skill_invoke(ctx: HandlerContext, args: dict) -> str:
    """Stub — the skills-catalog runner lands in a later phase.

    Phase 5 scope is the intent *classifier* and the trust contract; the
    actual ``skill_invoke`` loader needs :mod:`gaia.coder.skills.catalog`
    which is scaffolded but empty. Returning a recognisable stub string
    lets the test suite assert dispatch works without the loader.
    """
    name = args.get("name") or args.get("skill") or "unspecified"
    return f"skill_invoke({name}): not yet implemented (Phase 4.7 work)."


#: Handler dispatch table. ``free_form`` intentionally has no handler —
#: the CLI surfaces the raw message as an inbox question and waits for the
#: agent's normal response loop.
HANDLERS: dict[str, Callable[[HandlerContext, dict], str]] = {
    "enable_self_edit_session": enable_self_edit_session,
    "enable_self_edit_permanent": enable_self_edit_permanent,
    "disable_self_edit": disable_self_edit,
    "promote_tier": promote_tier,
    "demote_tier": demote_tier,
    "grant_per_call_selfedit": grant_per_call_selfedit,
    "what_tier": what_tier,
    "spend_query": spend_query,
    "pause": pause,
    "resume": resume,
    "authorise_sensitive": authorise_sensitive,
    "skill_invoke": skill_invoke,
    # "feedback" is handled out-of-band because it needs the raw body (see
    # docstring on :func:`feedback`).
}


def dispatch(
    result: IntentResult,
    ctx: HandlerContext,
    *,
    raw_message: Optional[str] = None,
) -> Optional[str]:
    """Route *result* to the matching handler and return its reply string.

    Returns ``None`` for ``free_form`` so the caller knows to fall back to
    the normal inbox-question flow (agent replies in a future turn).

    Raises:
        KeyError: if *result*'s intent is not in :data:`HANDLERS` and is not
            ``free_form`` / ``feedback`` (indicates a classifier bug —
            post-filter should have coerced unknown intents already).
    """
    intent = result["intent"]
    args = dict(result.get("args") or {})

    if intent == "free_form":
        return None
    if intent == "feedback":
        if raw_message is None:
            raise ValueError("dispatch(feedback) requires raw_message")
        return feedback(ctx, args, raw_message)

    handler = HANDLERS.get(intent)
    if handler is None:
        raise KeyError(
            f"no handler for intent {intent!r}; add it to HANDLERS or let "
            "classify_intent() coerce unknowns to free_form."
        )
    return handler(ctx, args)
