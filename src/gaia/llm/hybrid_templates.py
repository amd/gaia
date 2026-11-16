# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Which models ``hybrid`` can use here, and the built-in pairings.

One place, so the Agent UI, the TUI and the CLI agree. Every provider counts:
models on this PC, the AMD LLM Gateway, Fireworks AI and Anthropic directly.
:func:`build_inventory` reads what is set up from the model server's own
answers (``/models?show_all=true`` and ``/system-info``); a template is
available only when every model it names is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from gaia.llm.hybrid import (
    DEFAULT_CHEAP_MODEL,
    DEFAULT_STRONG_MODEL,
    HybridConfigError,
    HybridSettings,
)
from gaia.logger import get_logger

log = get_logger(__name__)

LOCAL, AMD, FIREWORKS, CLAUDE = "local", "amd", "fireworks", "claude"
PROVIDERS = (LOCAL, AMD, FIREWORKS, CLAUDE)
PROVIDER_NAMES = {
    LOCAL: "this PC",
    AMD: "the AMD LLM Gateway",
    FIREWORKS: "Fireworks AI",
    CLAUDE: "Anthropic",
}
#: How to set up each provider, in the apps' words.
SETUP_HINTS = {
    LOCAL: "Download a model in Settings › Models",
    AMD: "Connect the AMD LLM Gateway in Settings › Models",
    FIREWORKS: "Add a Fireworks key in Settings › Models",
    CLAUDE: "Add an Anthropic API key (ANTHROPIC_API_KEY in GAIA's environment)",
}
SETTINGS_SCREEN = "Settings › Models › Hybrid"
#: The CLI's words for each set-up hint above.
CLI_SETUP_HINTS = {
    SETUP_HINTS[LOCAL]: "Download one with `gaia download <model id>`",
    SETUP_HINTS[AMD]: "Connect it with `gaia gateway install` and `gaia gateway auth`",
    SETUP_HINTS[FIREWORKS]: "Add a Fireworks key with /provider in `gaia-cli`",
}
_DOWNLOAD_IN_SETTINGS = re.compile(r"Download (\S+) in Settings › Models(?! ›)")
CLI_DOWNLOAD = r"Download \1 with `gaia download \1`"


def cli_text(text: str) -> str:
    """*text* with the apps' set-up advice reworded for the terminal."""
    for app, cli in CLI_SETUP_HINTS.items():
        text = text.replace(app, cli)
    return _DOWNLOAD_IN_SETTINGS.sub(CLI_DOWNLOAD, text)


GATEWAY_SONNET = "amd.Claude-Sonnet-5-5"
FIREWORKS_CHEAP = "fireworks.deepseek-v4p1-flash"
CLAUDE_CHEAP = "claude-haiku-5-5"
CLAUDE_STRONG = "claude-opus-5-5"
#: Anthropic models GAIA offers when an Anthropic key is set.
CLAUDE_MODELS = (CLAUDE_CHEAP, "claude-sonnet-5-5", CLAUDE_STRONG)
SMALL_LOCAL = "Gemma-4-E4B-it-GGUF"
LARGE_LOCAL = "Qwen3.6-35B-A3B-GGUF"

MEASURED_TEMPLATE = "gateway-deepseek-opus"
MEASURED_NOTE = (
    "This pairing hasn't been measured yet, so how much it saves and how well "
    "it answers are unknown."
)
CLASSIFIER_NOTE = (
    "The classifier runs on this PC; a small local model may route less "
    "accurately than a cloud one."
)

#: Model labels that are not chat models.
_NON_CHAT_LABELS = frozenset(
    {
        "embeddings",
        "embedding",
        "image",
        "reranker",
        "audio",
        "tts",
        "stt",
        "transcription",
        "realtime-transcription",
    }
)


def is_chat_entry(entry: Dict[str, Any]) -> bool:
    """True for a model-server catalog entry that can hold a conversation."""
    labels = {str(label).lower() for label in entry.get("labels") or []}
    if "single-turn" in labels:
        # FLM builds that answer one message at a time cannot hold a conversation.
        return False
    # ``chat`` wins: FLM omni models are labeled both ``chat`` and ``audio``.
    return "chat" in labels or not labels & _NON_CHAT_LABELS


def provider_of(model_id: str) -> str:
    """``local``, ``amd``, ``fireworks``, ``claude``, or another cloud prefix."""
    from gaia.llm.lemonade_client import cloud_model_provider

    if model_id.startswith("claude-"):
        return CLAUDE
    return cloud_model_provider(model_id) or LOCAL


def short_name(model_id: str) -> str:
    """A model's family name for a label: ``amd.Claude-Opus-5-5`` -> ``Opus``."""
    bare = model_id
    if provider_of(model_id) not in (LOCAL, CLAUDE):
        bare = model_id.split(".", 1)[1]
    bare = bare.removeprefix("user.").rsplit("/", 1)[-1]
    if bare.startswith("claude-"):
        bare = bare[len("claude-") :]
    parts = [p for p in re.split(r"[-_]", bare) if p and p.lower() != "claude"]
    first = parts[0] if parts else bare
    return first[:1].upper() + first[1:]


@dataclass
class Offer:
    """A model a set-up provider offers, and whether it can run now."""

    id: str
    provider: str
    available: bool
    reason: Optional[str] = None


@dataclass
class Inventory:
    """What every provider offers here."""

    offers: Dict[str, Offer] = field(default_factory=dict)
    #: provider -> None when set up, else how to set it up.
    setup: Dict[str, Optional[str]] = field(default_factory=dict)
    #: Local catalog models not downloaded yet -> why they can't be used.
    local_missing: Dict[str, str] = field(default_factory=dict)
    local_only: bool = False
    #: On AMD's network direct Claude is refused; the gateway's Claude is used.
    amd_network: bool = False

    def check(self, model_id: str) -> Optional[str]:
        """None when *model_id* can be used now, else why not."""
        offer = self.offers.get(model_id)
        if offer is not None:
            return None if offer.available else offer.reason
        provider = provider_of(model_id)
        if provider == LOCAL:
            from gaia.llm.lemonade_client import find_model_requirement

            if model_id in self.local_missing:
                return self.local_missing[model_id]
            if find_model_requirement(model_id) is not None:
                return f"Download {model_id} in Settings › Models"
            return f"{model_id} isn't a model on this PC"
        if self.local_only:
            return f"Local only is on, so {model_id} can't be used"
        if provider == CLAUDE and self.amd_network:
            from gaia.llm.amd_network import gateway_only_reason

            return gateway_only_reason(model_id)
        if provider not in PROVIDER_NAMES:
            return f"no provider set up here offers {model_id}"
        setup = self.setup.get(provider)
        if setup:
            return f"{model_id} needs {PROVIDER_NAMES[provider]}: {setup}"
        return f"{PROVIDER_NAMES[provider]} doesn't offer {model_id}"

    def checked(self) -> str:
        """The providers set up here, for an error message."""
        ready = [PROVIDER_NAMES[p] for p in PROVIDERS if not self.setup.get(p)]
        missing = [self.setup[p] for p in PROVIDERS if self.setup.get(p)]
        text = f"Checked {', '.join(ready)}."
        if missing:
            text += f" To use more models: {'; '.join(missing)}."
        return text


def _cloud_auth(system_info: Dict[str, Any]) -> Dict[str, bool]:
    providers = ((system_info or {}).get("cloud") or {}).get("providers") or []
    return {
        str(p.get("name")): bool(p.get("env_var_set") or p.get("runtime_key_set"))
        for p in providers
        if isinstance(p, dict)
    }


def _local_reason(
    entry: Dict[str, Any], capacity: Any, system_info: Any
) -> Optional[str]:
    """Why a downloaded local model can't run on this PC, or None."""
    from gaia.installer import model_choice

    model_id = entry["id"]
    size = model_choice.download_size_gb(model_id, entry.get("size"))
    fit = model_choice.backend_fit(
        model_choice.memory_fit(model_id, size, capacity),
        entry.get("recipe"),
        system_info,
    )
    return model_choice.cant_run_reason(fit)


def _fits(model_id: str, entry: Dict[str, Any], capacity: Any) -> Optional[bool]:
    from gaia.installer import model_choice

    size = model_choice.download_size_gb(model_id, entry.get("size"))
    return model_choice.memory_fit(model_id, size, capacity).get("fits")


def build_inventory(
    models_payload: Any,
    system_info: Any,
    *,
    anthropic_key: bool,
    local_only: bool,
) -> Inventory:
    """Every model a set-up provider offers, from the model server's answers.

    Raises:
        HybridConfigError: an answer is not in the model server's format.
    """
    from gaia.llm.model_fit import ModelFitError, capacity_from_system_info

    entries = models_payload.get("data") if isinstance(models_payload, dict) else None
    if not isinstance(entries, list) or not isinstance(system_info, dict):
        raise HybridConfigError(
            "The model server's model list or system info isn't in the format "
            "GAIA expects, so the models hybrid can use can't be listed."
        )
    try:
        capacity = capacity_from_system_info(system_info)
    except ModelFitError:
        capacity = None
    authed = _cloud_auth(system_info)
    inv = Inventory(local_only=local_only)
    for entry in entries:
        model_id = entry.get("id") if isinstance(entry, dict) else None
        if not isinstance(model_id, str) or not model_id or not is_chat_entry(entry):
            continue
        cloud = entry.get("recipe") == "cloud" or bool(entry.get("cloud_provider"))
        provider = (
            (entry.get("cloud_provider") or model_id.split(".", 1)[0])
            if cloud
            else LOCAL
        )
        if provider == LOCAL:
            if entry.get("downloaded"):
                reason = _local_reason(entry, capacity, system_info)
                inv.offers[model_id] = Offer(model_id, LOCAL, reason is None, reason)
            elif model_id == LARGE_LOCAL and _fits(model_id, entry, capacity) is False:
                inv.local_missing[model_id] = f"{model_id} doesn't fit this PC's memory"
            else:
                inv.local_missing[model_id] = (
                    f"Download {model_id} in Settings › Models"
                )
        elif provider in (AMD, FIREWORKS) and authed.get(provider):
            inv.offers[model_id] = Offer(
                model_id,
                provider,
                not local_only,
                (
                    f"Local only is on, so {model_id} can't be used"
                    if local_only
                    else None
                ),
            )
    from gaia.llm.amd_network import gateway_only_reason, on_amd_network

    inv.amd_network = on_amd_network()
    amd_block = gateway_only_reason(CLAUDE_STRONG) if inv.amd_network else None
    if anthropic_key or amd_block:
        for model_id in CLAUDE_MODELS:
            reason = gateway_only_reason(model_id) if amd_block else None
            if local_only:
                reason = f"Local only is on, so {model_id} can't be used"
            inv.offers[model_id] = Offer(model_id, CLAUDE, reason is None, reason)
    offered = {o.provider for o in inv.offers.values()}
    inv.setup = {
        LOCAL: None if LOCAL in offered else SETUP_HINTS[LOCAL],
        AMD: None if AMD in offered else SETUP_HINTS[AMD],
        FIREWORKS: None if FIREWORKS in offered else SETUP_HINTS[FIREWORKS],
        CLAUDE: amd_block or (None if anthropic_key else SETUP_HINTS[CLAUDE]),
    }
    return inv


def local_default_model() -> str:
    """The local chat model a new chat uses; Gemma when the default is a cloud one."""
    from gaia.config import GaiaConfig

    configured = GaiaConfig.load().default_model
    if configured and provider_of(configured) == LOCAL and configured != "hybrid":
        return configured
    from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

    return DEFAULT_MODEL_NAME


@dataclass(frozen=True)
class Template:
    id: str
    name: str
    description: str
    policy: str = "quality"


TEMPLATES = (
    Template(
        MEASURED_TEMPLATE,
        "Gateway: DeepSeek + Opus",
        "DeepSeek V4.1 Flash for routine work, Claude Opus 5.5 for the rest, "
        "both through the AMD LLM Gateway. The default.",
    ),
    Template(
        "gateway-deepseek-sonnet",
        "Gateway: DeepSeek + Sonnet",
        "As the default, with Claude Sonnet 5.5 as the cheaper strong model.",
    ),
    Template(
        "gateway-balanced",
        "Gateway: DeepSeek + Opus, balanced",
        "The default pair, also sending medium-difficulty code changes to DeepSeek.",
        policy="balanced",
    ),
    Template(
        "local-plus-cloud",
        "This PC + Opus",
        "Your local chat model for routine work and as the classifier, Claude "
        "Opus 5.5 through the gateway for hard work.",
    ),
    Template(
        "all-local",
        "All on this PC",
        f"{SMALL_LOCAL} for routine work and as the classifier, {LARGE_LOCAL} "
        "for hard work. Nothing leaves this PC, so it works under Local only.",
    ),
    Template(
        "fireworks",
        "Fireworks + Opus",
        "DeepSeek V4.1 Flash on Fireworks AI for routine work, Claude Opus 5.5 "
        "(through the gateway, else Anthropic directly) for the rest.",
    ),
    Template(
        "claude-direct",
        "Anthropic: Haiku + Opus",
        "Claude Haiku 5.5 for routine work, Claude Opus 5.5 for the rest, "
        "straight from Anthropic.",
    ),
)
TEMPLATE_IDS = tuple(t.id for t in TEMPLATES)


def _members(template: Template, inv: Inventory) -> Tuple[str, str, str]:
    """(cheap, strong, classifier) for *template* on this PC."""
    tid = template.id
    if tid in (MEASURED_TEMPLATE, "gateway-balanced"):
        return DEFAULT_CHEAP_MODEL, DEFAULT_STRONG_MODEL, DEFAULT_CHEAP_MODEL
    if tid == "gateway-deepseek-sonnet":
        return DEFAULT_CHEAP_MODEL, GATEWAY_SONNET, DEFAULT_CHEAP_MODEL
    if tid == "local-plus-cloud":
        local = local_default_model()
        return local, DEFAULT_STRONG_MODEL, local
    if tid == "all-local":
        return SMALL_LOCAL, LARGE_LOCAL, SMALL_LOCAL
    if tid == "fireworks":
        strong = (
            CLAUDE_STRONG
            if inv.check(DEFAULT_STRONG_MODEL) and not inv.check(CLAUDE_STRONG)
            else DEFAULT_STRONG_MODEL
        )
        return FIREWORKS_CHEAP, strong, FIREWORKS_CHEAP
    if tid == "claude-direct":
        return CLAUDE_CHEAP, CLAUDE_STRONG, CLAUDE_CHEAP
    raise KeyError(tid)


def notes(cheap: str, strong: str, classifier: str, policy: str) -> Dict[str, Any]:
    """``measured`` and the two notes the apps show for a configuration."""
    measured_members = _members(TEMPLATES[0], Inventory())
    measured = (cheap, strong, classifier, policy) == (*measured_members, "quality")
    return {
        "measured": measured,
        "measured_note": None if measured else MEASURED_NOTE,
        "classifier_note": (
            CLASSIFIER_NOTE if provider_of(classifier) == LOCAL else None
        ),
    }


def availability(models: Tuple[str, ...], inv: Inventory) -> Optional[str]:
    """None when every model in *models* can be used now, else why not."""
    unique = list(dict.fromkeys(models))
    cloud = [m for m in unique if provider_of(m) != LOCAL]
    if inv.local_only and cloud:
        return (
            f"Local only is on, and this sends turns to the cloud "
            f"({', '.join(cloud)}). Turn Local only off, or use the all-local "
            "pairing"
        )
    for model in unique:
        reason = inv.check(model)
        if reason:
            return reason
    return None


def describe(template: Template, inv: Inventory) -> Dict[str, Any]:
    """*template* resolved on this PC, with whether it can be used now."""
    from gaia.config import GaiaConfigError

    try:
        cheap, strong, classifier = _members(template, inv)
        reason = availability((classifier, cheap, strong), inv)
    except GaiaConfigError as e:
        # Only local-plus-cloud reads config.json, for the local default model.
        log.warning("Template %s: config.json can't be read: %s", template.id, e)
        cheap = strong = classifier = None
        detail = e.args[0] if e.args and isinstance(e.args[0], str) else None
        reason = "config.json can't be read, so your local model isn't known" + (
            f": {detail}" if detail else "; the GAIA log has the details"
        )
    if template.id == "fireworks" and inv.check(strong) and inv.check(CLAUDE_STRONG):
        needs = (
            "the AMD LLM Gateway"
            if inv.amd_network
            else "the AMD LLM Gateway or an Anthropic key"
        )
        reason = f"{reason}. The strong model needs {needs}" if reason else reason
    return {
        "id": template.id,
        "name": template.name,
        "description": template.description,
        "cheap_model": cheap,
        "strong_model": strong,
        "classifier_model": classifier,
        "policy": template.policy,
        "providers_needed": list(
            dict.fromkeys(provider_of(m) for m in (cheap, strong, classifier) if m)
        ),
        "available": reason is None,
        "reason": reason,
        **(
            notes(cheap, strong, classifier, template.policy)
            if cheap
            else {"measured": False, "measured_note": None, "classifier_note": None}
        ),
    }


def list_templates(inv: Inventory) -> List[Dict[str, Any]]:
    return [describe(t, inv) for t in TEMPLATES]


def template_settings(template_id: str, inv: Inventory) -> Dict[str, Any]:
    """The ``hybrid_*`` config values for *template_id*.

    Raises:
        KeyError: no template has that id.
        HybridConfigError: it can't be used here; the message says why.
    """
    template = next((t for t in TEMPLATES if t.id == template_id), None)
    if template is None:
        raise KeyError(
            f"There is no hybrid template {template_id!r}; the templates are "
            f"{', '.join(TEMPLATE_IDS)}."
        )
    described = describe(template, inv)
    if not described["available"]:
        raise HybridConfigError(
            f"{template.name} can't be used here: {described['reason']}."
        )
    values = {
        "hybrid_cheap_model": described["cheap_model"],
        "hybrid_strong_model": described["strong_model"],
        "hybrid_classifier_model": described["classifier_model"],
        "hybrid_policy": described["policy"],
    }
    # Validated as a whole before anything is saved.
    HybridSettings(
        cheap_model=values["hybrid_cheap_model"],
        strong_model=values["hybrid_strong_model"],
        classifier_model=values["hybrid_classifier_model"],
        policy=values["hybrid_policy"],
    )
    return values


def picker_entry(
    inv: Inventory, settings: Optional[HybridSettings], error: Optional[str]
):
    """The ``hybrid`` choice for a model picker, named from the configured pair."""
    templates = list_templates(inv)
    if settings is None:
        reason, name, remote = error, "Hybrid", None
    else:
        members = (
            settings.classifier_model,
            settings.cheap_model,
            settings.strong_model,
        )
        reason = availability(members, inv)
        name = (
            f"Hybrid ({short_name(settings.cheap_model)} + "
            f"{short_name(settings.strong_model)})"
        )
        remote = any(provider_of(m) != LOCAL for m in members)
    return {
        "id": "hybrid",
        "display_name": name,
        "remote": remote,
        "available": settings is not None and reason is None,
        "reason": reason,
        "shown": (settings is not None and reason is None)
        or any(t["available"] for t in templates),
    }
