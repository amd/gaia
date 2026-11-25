# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Move a running agent to another model without losing its conversation.

:meth:`gaia.agents.base.agent.Agent.switch_model` is the public entry. The new
client is built, and Lemonade confirmed to serve the model, before anything on
the agent moves; the move itself is snapshotted and rolled back as a unit, so a
failed switch leaves the agent on the model it had.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple

from gaia.agents.base.readiness import (
    probe_model_present,
    resolve_probe_base,
    start_advice,
)
from gaia.llm import create_client
from gaia.llm.lemonade_client import (
    LemonadeClient,
    LemonadeClientError,
    _model_ids_match,
    cloud_model_provider,
)
from gaia.llm.lemonade_launcher import describe_client_hint
from gaia.logger import get_logger

logger = get_logger(__name__)

#: Claude ids a switch accepts, with the name to show. An unlisted id is refused.
CLAUDE_MODELS: Dict[str, str] = {
    "claude-opus-5": "Opus 5",
    "claude-sonnet-5": "Sonnet 5",
    "claude-haiku-4-5": "Haiku 4.5",
    "claude-fable-5": "Fable 5",
}

#: Lemonade catalog labels that mark a model as NOT a chat target. Offering one
#: as a `/model` switch would report success and break silently on the NEXT
#: turn, far from the mistake. Whisper (``transcription``) was offered.
_NON_CHAT_LABELS = frozenset(
    {
        "embeddings",
        "image",
        "reranker",
        "reranking",
        "transcription",
        "realtime-transcription",
        "tts",
        "audio-generation",
        "voice-design",
        "classification",
        "upscaling",
        "3d",
    }
)


def _is_chat_target(entry: Dict[str, Any]) -> bool:
    """``chat`` wins (an omni model also transcribes); unlabeled models count."""
    labels = set(entry.get("labels") or [])
    return "chat" in labels or not _NON_CHAT_LABELS & labels


def _lemonade_models(base_url: Optional[str]) -> Dict[str, Dict[str, Any]]:
    """Downloaded local and discovered Fireworks/AMD chat models Lemonade serves.

    Maps each id, in sorted order, to its catalog entry, so a switch can hand
    the entry to the new client. Raises RuntimeError naming the URL and the
    fix when Lemonade is unreachable; callers show it to the user as is.
    """
    client = LemonadeClient(base_url=base_url, verbose=False)
    try:
        # Only the full catalog carries `labels` and `downloaded`.
        catalog = client.list_models(show_all=True)
    except LemonadeClientError as exc:
        raise RuntimeError(
            f"Lemonade Server is not reachable at {client.base_url} ({exc}). "
            f"{start_advice()}"
        ) from exc
    switchable = {
        m["id"]: m
        for m in catalog.get("data", [])
        if m.get("id")
        and (m.get("downloaded") or cloud_model_provider(m["id"], m))
        and cloud_model_provider(m["id"], m) in {None, "fireworks", "amd"}
        and _is_chat_target(m)
    }
    return {model_id: switchable[model_id] for model_id in sorted(switchable)}


#: Snapshot of everything a switch mutates, for an all-or-nothing apply.
_SwitchState = Dict[str, Any]


_ABSENT = object()


def _switch_config(agent: Any) -> Any:
    """The settings object whose model fields follow a switch, or None.

    An ``Agent`` says which through ``_model_switch_config``; a ``config``
    attribute on some other subclass is its own and is never written.
    """
    hook = getattr(type(agent), "_model_switch_config", None)
    return hook(agent) if callable(hook) else getattr(agent, "config", None)


def _snapshot_switch_state(agent: Any) -> _SwitchState:
    chat = agent.chat
    cfg = _switch_config(agent)
    return {
        "llm_client": chat.llm_client,
        "chat_use_claude": chat.config.use_claude,
        "chat_model": chat.config.model,
        "chat_claude_model": chat.config.claude_model,
        "use_claude": agent._use_claude,
        "claude_model": vars(agent).get("_claude_model", _ABSENT),
        "model_id": agent.model_id,
        "cfg_use_claude": getattr(cfg, "use_claude", None) if cfg is not None else None,
        "cfg_model_id": getattr(cfg, "model_id", None) if cfg is not None else None,
        "cfg_claude_model": (
            getattr(cfg, "claude_model", None) if cfg is not None else None
        ),
        "resend_reasoning": getattr(agent, "resend_reasoning_across_requests", None),
        "model_features": getattr(agent, "model_features", None),
        "instance_tools": _copy_or_none(getattr(agent, "_instance_tools", None)),
        "withheld_tools": _copy_or_none(getattr(agent, "_withheld_tool_entries", None)),
        "tool_overrides": _copy_or_none(getattr(agent, "_tool_overrides", None)),
        "followers": _snapshot_followers(agent),
    }


#: What ``apply_context_eviction`` moves with the model on every agent.
_FOLLOWER_ATTRS = ("context_eviction_enabled", "_context_evictor")


def snapshot_followers(agent: Any) -> Dict[str, Any]:
    """The base agent's own state that moves with the model, for a rollback."""
    return {name: vars(agent)[name] for name in _FOLLOWER_ATTRS if name in vars(agent)}


def restore_followers(agent: Any, state: Dict[str, Any]) -> None:
    for name, value in state.items():
        setattr(agent, name, value)


def _snapshot_followers(agent: Any) -> Dict[str, Any]:
    # An agent that moves more with its model adds it in its own hook.
    hook = getattr(agent, "_snapshot_model_followers", None)
    return hook() if callable(hook) else snapshot_followers(agent)


def _restore_followers(agent: Any, state: Dict[str, Any]) -> None:
    hook = getattr(agent, "_restore_model_followers", None)
    if callable(hook):
        hook(state)
    else:
        restore_followers(agent, state)


def _copy_or_none(mapping: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return dict(mapping) if mapping is not None else None


def _restore_switch_state(agent: Any, snapshot: _SwitchState) -> None:
    chat = agent.chat
    chat.llm_client = snapshot["llm_client"]
    chat.config.use_claude = snapshot["chat_use_claude"]
    chat.config.model = snapshot["chat_model"]
    chat.config.claude_model = snapshot["chat_claude_model"]
    agent._use_claude = snapshot["use_claude"]
    if snapshot["claude_model"] is not _ABSENT:
        agent._claude_model = snapshot["claude_model"]
    elif "_claude_model" in vars(agent):
        del agent._claude_model
    agent.model_id = snapshot["model_id"]
    cfg = _switch_config(agent)
    if cfg is not None:
        cfg.use_claude = snapshot["cfg_use_claude"]
        cfg.model_id = snapshot["cfg_model_id"]
        cfg.claude_model = snapshot["cfg_claude_model"]
    if snapshot["resend_reasoning"] is not None:
        agent.resend_reasoning_across_requests = snapshot["resend_reasoning"]
    if snapshot["model_features"] is not None:
        agent.model_features = snapshot["model_features"]
    if snapshot["instance_tools"] is not None:
        agent._instance_tools = snapshot["instance_tools"]
    if snapshot["withheld_tools"] is not None:
        agent._withheld_tool_entries = snapshot["withheld_tools"]
    if snapshot["tool_overrides"] is not None:
        agent._tool_overrides = snapshot["tool_overrides"]
    elif "_tool_overrides" in vars(agent):
        # The switch created it; the old model had none.
        del agent._tool_overrides
    _restore_followers(agent, snapshot["followers"])


def _apply_switch(
    agent: Any,
    *,
    use_claude: bool,
    model_id: Optional[str],
    claude_model: Optional[str],
    new_client: Any,
) -> None:
    """Move ``agent``/``agent.chat`` onto the already-built *new_client*, all
    or nothing.

    Snapshots everything first and restores it on ANY exception —
    ``rebuild_system_prompt()`` runs inside this same guarded block, so a bug
    in prompt composition rolls back the client swap too, instead of leaving
    the session on a working new client with a half-composed prompt.
    """
    snapshot = _snapshot_switch_state(agent)
    chat = agent.chat
    try:
        chat.config.use_claude = use_claude
        if model_id is not None:
            chat.config.model = model_id
        if claude_model is not None:
            chat.config.claude_model = claude_model
        chat.llm_client = new_client
        agent._use_claude = use_claude
        if claude_model is not None:
            agent._claude_model = claude_model
        agent.model_id = model_id if model_id is not None else claude_model
        cfg = _switch_config(agent)
        if cfg is not None:
            cfg.use_claude = use_claude
            if model_id is not None:
                cfg.model_id = model_id
            if claude_model is not None:
                cfg.claude_model = claude_model
        # Earlier turns' reasoning belongs to the old model: it can't hit the new
        # model's cache, and some reasoner APIs reject it in input.
        if (
            getattr(agent, "resend_reasoning_across_requests", False)
            and agent.model_id != snapshot["model_id"]
        ):
            agent.resend_reasoning_across_requests = False
        # The new model may be offered more features than the old one, or fewer.
        if hasattr(agent, "apply_model_features"):
            agent.apply_model_features(agent.model_id)
        # A chat started on a model with no reasoning-effort setting has no tool for it.
        if hasattr(agent, "register_reasoning_effort_tools") and (
            "set_reasoning_effort"
            not in (getattr(agent, "_instance_tools", None) or {})
        ):
            agent.register_reasoning_effort_tools()
        # getattr: tests build agents without running Agent.__init__.
        if getattr(agent, "_context_eviction_mode", None) is not None:
            lookup = getattr(new_client, "cloud_model_provider", None)
            agent.apply_context_eviction(
                agent.model_id,
                cloud=use_claude or bool(callable(lookup) and lookup(agent.model_id)),
            )
        # Image reading and document summaries were set up for the old model.
        if hasattr(agent, "follow_model_switch"):
            agent.follow_model_switch()
        # The cached prompt carries the old backend's tool-call format.
        agent.rebuild_system_prompt()
    except Exception as exc:
        _restore_switch_state(agent, snapshot)
        raise RuntimeError(
            f"Model switch failed while finishing the change "
            f"({type(exc).__name__}: {exc}) — rolled back to the previous model."
        ) from exc


def _apply_claude_switch(agent: Any, target: str) -> str:
    """Swap the live client to Claude model *target*; raise on any failure.

    Builds the new client BEFORE touching ``agent`` — a bad credential never
    leaves the session half-swapped, only unchanged.
    """
    if target not in CLAUDE_MODELS:
        raise RuntimeError(
            f"Unknown Claude model '{target}'. Valid ids: "
            f"{', '.join(CLAUDE_MODELS)}."
        )
    chat = agent.chat
    try:
        new_client = create_client(
            use_claude=True,
            model=target,
            base_url=chat.config.base_url,
            system_prompt=chat.config.system_prompt,
        )
    except (ValueError, ImportError) as exc:
        # ValueError: ANTHROPIC_API_KEY missing (claude.py's own actionable
        # copy). ImportError: the anthropic package itself isn't installed.
        raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc

    _apply_switch(
        agent,
        use_claude=True,
        model_id=None,
        claude_model=target,
        new_client=new_client,
    )
    return CLAUDE_MODELS[target]


def _replayed_cloud_key(base_url: Optional[str], target: str) -> bool:
    """Whether Lemonade lists cloud model *target* once handed its remembered key.

    A restarted Lemonade forgets runtime keys and discovers nothing; the
    readiness probe is the one place that gives back a key GAIA remembers for
    that provider. False
    for a local id, or when the probe cannot reach Lemonade — the caller's
    "Unknown Lemonade model" refusal is then the actionable answer.
    """
    import requests

    if cloud_model_provider(target) is None:
        return False
    try:
        return probe_model_present(resolve_probe_base(base_url), target)
    except requests.RequestException as exc:
        logger.debug("[lemonade] cloud key replay probe for %r failed: %s", target, exc)
        return False


def _lemonade_url(agent: Any) -> Optional[str]:
    """The Lemonade URL the live client talks to now.

    ``chat.config.base_url`` keeps the address the agent started with, which is
    dead once the daemon restarts GAIA's Lemonade on a new port; the live
    client follows it there. A Claude client has no such URL.
    """
    chat = agent.chat
    current = getattr(getattr(chat, "llm_client", None), "current_base_url", None)
    if not callable(current):
        return chat.config.base_url
    try:
        return current()
    except LemonadeClientError as exc:
        raise RuntimeError(f"{str(exc).rstrip('.')}. {start_advice()}") from exc


def _wait_for_gaia_lemonade(
    agent: Any, target: str, on_status: Optional[Callable[[str], None]]
) -> Optional[str]:
    """Wait, bounded, for GAIA's own server to answer again; return its URL.

    None when the server is not GAIA's own (the user's, or a remote
    ``LEMONADE_BASE_URL``): the caller's own error then stands. Goes through
    the live Lemonade client when there is one, so it follows the restarted
    server too. A server already back by now -- it came up while the failed
    request was still retrying -- returns its URL at once.
    """
    chat = agent.chat
    client = getattr(chat, "llm_client", None)
    if not callable(getattr(client, "current_base_url", None)):
        client = LemonadeClient(base_url=chat.config.base_url, verbose=False)
    wait = getattr(client, "wait_for_gaia_lemonade", None)
    if not callable(wait) or not getattr(client, "follows_gaia_lemonade", False):
        return None
    try:
        wait(target, on_status=on_status)
        return client.current_base_url()
    except LemonadeClientError as exc:
        raise RuntimeError(str(exc)) from exc


def _reachable_lemonade_models(
    agent: Any, target: str, on_status: Optional[Callable[[str], None]]
) -> Tuple[Optional[str], Dict[str, Dict[str, Any]]]:
    """``(base_url, models)`` from the server the live client talks to.

    GAIA's own server is briefly unreachable while the daemon restarts it, so
    that is waited out instead of refusing the switch.
    """
    try:
        base_url = _lemonade_url(agent)
        return base_url, _lemonade_models(base_url)
    except RuntimeError:
        base_url = _wait_for_gaia_lemonade(agent, target, on_status)
        if base_url is None:
            raise
    return base_url, _lemonade_models(base_url)


def _apply_local_switch(
    agent: Any, target: str, on_status: Optional[Callable[[str], None]] = None
) -> str:
    """Swap the live client to a local or cloud Lemonade model."""
    chat = agent.chat
    # Raises if unreachable, after waiting out a restart of GAIA's own server.
    base_url, available = _reachable_lemonade_models(agent, target, on_status)
    # Lemonade lists a registered user.X model as X; switch to the listed name.
    target = next((m for m in available if _model_ids_match(m, target)), target)
    if target not in available and _replayed_cloud_key(base_url, target):
        available = _lemonade_models(base_url)
    if target not in available:
        raise RuntimeError(
            f"Unknown Lemonade model '{target}'. Downloaded local or discovered cloud "
            "Lemonade models: "
            + (
                ", ".join(available)
                if available
                else f"(none — {describe_client_hint('pull', target).instruction.rstrip('.')})"
            )
            + "."
        )
    try:
        new_client = create_client(
            use_claude=False,
            model=target,
            base_url=base_url,
            system_prompt=chat.config.system_prompt,
            # The switch message and the rebuilt prompt name the provider from it.
            model_metadata=[available[target]],
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # Reachability was already confirmed above — this covers whatever else
        # LemonadeClient's constructor could still reject. Nothing on
        # agent/chat has moved yet.
        raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc

    _apply_switch(
        agent,
        use_claude=False,
        model_id=target,
        claude_model=None,
        new_client=new_client,
    )
    if hasattr(agent, "unload_skills_needing_withheld_tools"):
        dropped = agent.unload_skills_needing_withheld_tools()
        if dropped and on_status is not None:
            on_status(
                f"Unloaded {', '.join(dropped)}: {target} isn't offered the tools "
                "they need. Switch to a larger model to use them."
            )
    return target


def is_claude_model(model_id: str) -> bool:
    """Whether *model_id* names a Claude model, i.e. goes to Anthropic."""
    return model_id.startswith("claude-")


def switch_concrete_model(
    agent: Any, target: str, on_status: Optional[Callable[[str], None]] = None
) -> str:
    """Move *agent* to the model named *target*; see ``Agent.switch_model``."""
    from gaia.llm.local_only import SETTINGS_HINT, cloud_blocked

    if cloud_blocked() and (is_claude_model(target) or cloud_model_provider(target)):
        raise RuntimeError(
            f"Local only is on, so GAIA won't switch to the cloud model {target}. "
            f"{SETTINGS_HINT}"
        )
    if is_claude_model(target):
        return _apply_claude_switch(agent, target)
    return _apply_local_switch(agent, target, on_status)
