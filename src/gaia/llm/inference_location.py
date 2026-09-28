# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Where this session's chat inference actually happens (#3674).

The flagship's system prompt opened with "a personal AI running locally on the
user's machine" no matter what was serving it, so a Fireworks-backed session
told the user its chat was local while the TUI header above it said remote.
Asked outright, the agent went looking through configuration files and still
could not say.

This module answers the question from the live client configuration, so the
prompt can state it and the agent never has to go hunting. One helper, so the
prompt, the TUI status event, and anything else that needs to say where a turn
is processed all give the same answer.

Local *tool* execution is not the same claim: files, shell, documents, RAG
embeddings and memory stay on the machine even when chat is remote. The
description keeps them apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from gaia.llm.lemonade_client import cloud_model_provider
from gaia.logger import get_logger

logger = get_logger(__name__)

#: Display names for the backends a GAIA session can be chatting through.
_DISPLAY_NAMES = {
    "amd": "AMD LLM Gateway",
    "claude": "Anthropic Claude",
    "fireworks": "Fireworks AI",
    "lemonade": "Lemonade on this machine",
    "openai": "OpenAI",
}

#: Cloud providers Lemonade itself routes to. Chat leaves the machine; the
#: Lemonade server the request goes through is still the local one.
_VIA_LEMONADE = frozenset({"amd", "fireworks"})


@dataclass(frozen=True)
class InferenceLocation:
    """Where the chat model runs, and what that does and does not cover."""

    provider: str
    """``lemonade`` | ``fireworks`` | ``amd`` | ``claude`` | ``openai``."""

    model: str
    """The model id actually sent to that provider."""

    remote: bool
    """True when the conversation leaves this machine to be answered."""

    @property
    def display(self) -> str:
        """Human name for the provider, for a prompt or a status line."""
        return _DISPLAY_NAMES.get(self.provider, self.provider)

    def describe(self) -> str:
        """One sentence naming the destination, honest about what is local."""
        if not self.remote:
            # "GAIA is not sending it anywhere", not "it cannot go anywhere":
            # Lemonade can itself be configured to route a plainly-named model
            # upstream, which nothing here can see. Claim only GAIA's own
            # routing, or the one line meant to settle this question becomes a
            # privacy assurance GAIA cannot make.
            return (
                f"Chat inference runs through Lemonade on this machine, model "
                f"`{self.model}`. GAIA is not sending this conversation to any "
                "cloud provider."
            )
        via = " via Lemonade" if self.provider in _VIA_LEMONADE else ""
        # Says what is SENT, not merely what is stored: memory and documents
        # live here, but whatever is read out of them to answer goes with the
        # conversation, and "memory runs on this machine" alone reads as a
        # promise that it does not.
        return (
            f"Chat inference runs on {self.display}{via} — a cloud provider — "
            f"using model `{self.model}`. Everything in this conversation is "
            "sent there to be answered, including whatever you read out of "
            "files, documents or memory. Those are stored and searched on this "
            "machine."
        )


def _ask(
    lookup: Optional[Callable[[str], Optional[str]]], model_id: str
) -> Optional[str]:
    """Classify *model_id*, preferring a live client's own classifier.

    A lookup that answers with anything but a provider name is not answering:
    say so in the log and fall through to the module rule, rather than putting
    whatever it returned into the sentence the user reads.
    """
    if lookup is None:
        return cloud_model_provider(model_id)
    answer = lookup(model_id)
    if answer is None or isinstance(answer, str):
        return answer or None
    logger.warning(
        "Ignoring a cloud-provider lookup that answered with %s, not a "
        "provider name; classifying %r by its id instead.",
        type(answer).__name__,
        model_id,
    )
    return cloud_model_provider(model_id)


def resolve_inference_location(
    model: Optional[str],
    *,
    use_claude: bool = False,
    use_openai: bool = False,
    cloud_provider_lookup: Optional[Callable[[str], Optional[str]]] = None,
) -> InferenceLocation:
    """Classify the backend a session's chat turns are answered by.

    ``model`` is the id actually sent to the provider — ``AgentSDK``'s
    ``effective_model``, not the configured local id, which differs whenever a
    remote backend is selected.

    ``cloud_provider_lookup`` is the live client's own classifier when there is
    one (``LemonadeProvider.cloud_model_provider``), which reads the catalog
    metadata and so recognises a provider discovered at runtime. Without it the
    module-level rule applies: ``<provider>.<id>`` for the two providers GAIA
    knows by name. That rule is what the TUI's own remote badge uses, so the two
    always agree.
    """
    model_id = (model or "").strip() or "unknown"
    if use_claude:
        return InferenceLocation("claude", model_id, remote=True)
    if use_openai:
        return InferenceLocation("openai", model_id, remote=True)
    provider = _ask(cloud_provider_lookup, model_id)
    if provider:
        return InferenceLocation(provider, model_id, remote=True)
    return InferenceLocation("lemonade", model_id, remote=False)
