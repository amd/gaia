# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Run modes: where GAIA runs, chosen as **Local** or **Cloud**.

A mode is a named set of per-capability choices. Today chat is the only
capability with a proven cloud option (Fireworks AI, through Lemonade), so a
mode stores one thing, its chat model; every other capability runs on this PC
in both modes and is listed so the user can see that.

The running chat model decides the mode: a local model is Local, a cloud model
is Cloud. ``run_mode`` in ``~/.gaia/config.json`` records the last choice, and
``run_profiles`` holds each mode's chat model, so the mode the user is not in
remembers what it was set to::

    "run_mode": "cloud",
    "run_profiles": {"local": {"chat": "Qwen3.6-35B-A3B-GGUF"},
                     "cloud": {"chat": "fireworks.glm-5p3-flash"}}

Switching a mode picks that mode's chat model, which writes ``last_model``, the
key the Agent UI, the agent sidecar, ``gaia -p``, schedules and the TUI reopen
on. An install with no ``run_mode`` needs no migration: its mode is read off
the model it already runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

LOCAL = "local"
CLOUD = "cloud"
MODES = (LOCAL, CLOUD)

CHAT = "chat"

#: The provider Cloud uses unless the user picks a model from another one.
CLOUD_PROVIDER = "fireworks"
CLOUD_PROVIDER_NAME = "Fireworks AI"

#: Why a capability stays on this PC in Cloud mode.
NOT_OFFERED = "not_offered"  # the cloud provider has no such API
NOT_TESTED = "not_tested"  # it has one, but GAIA has not run it


class RunModeError(ValueError):
    """A run mode or one of its choices is not usable, with what to do."""


@dataclass(frozen=True)
class Capability:
    """One thing GAIA does, and why Cloud mode leaves it on this PC."""

    id: str
    cloud_note: Optional[str]
    #: The Agent UI settings section that changes it; None when nothing does.
    settings: Optional[str] = None


#: Every capability a mode covers, in the order the settings screen lists them.
CAPABILITIES = (
    Capability(CHAT, None),
    Capability("vision", NOT_TESTED),
    Capability("speech_to_text", NOT_OFFERED),
    Capability("read_aloud", NOT_OFFERED, "voice"),
    Capability("images", NOT_OFFERED, "images"),
    Capability("documents", NOT_TESTED),
)


def check_mode(mode: Any) -> str:
    """*mode* when it names a run mode, else raise with the valid ones."""
    if mode not in MODES:
        raise RunModeError(
            f"{mode!r} is not a run mode. Choose one of: {', '.join(MODES)}."
        )
    return mode


def check_profiles(value: Any, where: str) -> Dict[str, Dict[str, str]]:
    """*value* when it is a well-formed ``run_profiles`` block, else raise."""
    ok = isinstance(value, dict) and all(
        mode in MODES
        and isinstance(profile, dict)
        and all(
            key == CHAT and isinstance(model, str) and model.strip()
            for key, model in profile.items()
        )
        for mode, profile in value.items()
    )
    if not ok:
        raise RunModeError(
            f"{where}: 'run_profiles' must map {' or '.join(MODES)} to "
            f'{{"chat": "<model id>"}}, but it is {value!r}. Fix it by hand, or '
            "delete the key to go back to each mode's default model."
        )
    return value


def mode_for(remote: bool) -> str:
    """The mode a chat model puts GAIA in: cloud models are Cloud."""
    return CLOUD if remote else LOCAL


def default_chat(mode: str) -> str:
    """The chat model *mode* uses until the user picks another.

    Local is the model setup chose for this PC; Cloud is the first model GAIA
    recommends on Fireworks AI.
    """
    check_mode(mode)
    if mode == CLOUD:
        from gaia.llm.recommended_models import RECOMMENDED_MODELS

        return RECOMMENDED_MODELS[0].id
    from gaia.llm.lemonade_client import resolve_local_chat_model

    return resolve_local_chat_model()


def same_model(a: Optional[str], b: Optional[str]) -> bool:
    """Whether two ids name one model: ``user.X`` is ``X``, and Fireworks lists
    ``fireworks.x`` as ``fireworks.accounts/fireworks/models/x`` too."""
    from gaia.llm.lemonade_client import _model_ids_match
    from gaia.llm.recommended_models import _rank_key

    if not a or not b:
        return False
    return _model_ids_match(a, b) or _rank_key(a) == _rank_key(b)


def saved_chat(profiles: Dict[str, Dict[str, str]], mode: str) -> Optional[str]:
    """The chat model the user set for *mode*, or None for its default."""
    return (profiles.get(check_mode(mode)) or {}).get(CHAT) or None


def chat_for(profiles: Dict[str, Dict[str, str]], mode: str) -> str:
    """The chat model switching to *mode* runs."""
    return saved_chat(profiles, mode) or default_chat(mode)


def with_chat(
    profiles: Dict[str, Dict[str, str]], mode: str, model: Optional[str]
) -> Dict[str, Dict[str, str]]:
    """*profiles* with *mode*'s chat model set; None or its default clears it."""
    check_mode(mode)
    result = {m: dict(p) for m, p in profiles.items() if m != mode}
    if model and not same_model(model, default_chat(mode)):
        result[mode] = {CHAT: model}
    return result


def record_pick(doc: dict, model: str, remote: bool) -> None:
    """Note in the config document that *model* now runs chat.

    The mode follows the model, and the mode's chat model becomes *model*, so
    coming back to this mode later reopens on it.
    """
    mode = mode_for(remote)
    doc["run_mode"] = mode
    profiles = with_chat(doc.get("run_profiles") or {}, mode, model)
    if profiles:
        doc["run_profiles"] = profiles
    else:
        doc.pop("run_profiles", None)


def capability_rows(mode: str, chat_model: str) -> List[Dict[str, Any]]:
    """What serves each non-chat capability in *mode*, for the settings screen.

    Every one runs on this PC today. ``model`` is None and ``unavailable`` codes
    why when the capability is off here: ``platform`` (no voice on macOS),
    ``npu`` (no image reading while chat runs on the NPU) or ``chat_model`` (no
    image creation on a compact chat model).
    """
    from gaia.agents.base.model_features import features_for_model
    from gaia.agents.registry import machine_embedding_model
    from gaia.audio.lemonade_asr import DEFAULT_ASR_MODEL
    from gaia.audio.lemonade_tts import DEFAULT_TTS_MODEL
    from gaia.config import GaiaConfig, speech_output_unproven_reason
    from gaia.llm.lemonade_client import resolve_default_vision_model
    from gaia.sd.settings import DEFAULT_IMAGE_MODEL

    check_mode(mode)
    config = GaiaConfig.load()
    vision = resolve_default_vision_model(chat_model)
    creates_images = features_for_model(chat_model).image_generation
    served = {
        "vision": (vision, None if vision else "npu"),
        "speech_to_text": (DEFAULT_ASR_MODEL, None),
        "read_aloud": (
            DEFAULT_TTS_MODEL,
            "platform" if speech_output_unproven_reason() else None,
        ),
        "images": (
            config.image_model or DEFAULT_IMAGE_MODEL,
            None if creates_images else "chat_model",
        ),
        "documents": (machine_embedding_model(), None),
    }
    rows = []
    for capability in CAPABILITIES:
        if capability.id == CHAT:
            continue
        model, unavailable = served[capability.id]
        rows.append(
            {
                "id": capability.id,
                "place": LOCAL,
                "model": None if unavailable else model,
                "unavailable": unavailable,
                "cloud_note": capability.cloud_note if mode == CLOUD else None,
                "settings": capability.settings,
            }
        )
    return rows


def apply_from_cli(mode: str) -> Tuple[str, bool]:
    """Switch to *mode* from the command line.

    Returns the chat model now set, and whether anything changed: already in
    *mode*, the model in use is kept, as the Agent UI and the TUI keep it.

    Writes ``last_model``, which ``gaia -p``, schedules, the TUI and the next
    Agent UI start reopen on. Cloud is refused under Local only and, for a
    Fireworks AI model, without a key. Whether the model can run is not checked
    here: no model server need be up, so whatever starts it next reports that.
    """
    from gaia.config import GaiaConfig
    from gaia.llm import local_only
    from gaia.llm.lemonade_client import cloud_model_provider, resolve_chat_model

    check_mode(mode)
    leaving = resolve_chat_model()
    left = mode_for(_is_cloud_id(leaving))
    if left == mode:
        return leaving, False
    model = chat_for(GaiaConfig.load().run_profiles, mode)
    provider = cloud_model_provider(model) or LOCAL
    if mode == CLOUD:
        if local_only.cloud_blocked():
            raise RunModeError(
                "Local only is on, so Cloud can't be used. Run "
                "`gaia config set network_policy cloud_allowed` first."
            )
        if provider == CLOUD_PROVIDER:
            from gaia.llm.cloud_keys import env_key, recall_key

            if not (recall_key(provider) or env_key(provider)):
                raise RunModeError(
                    f"Cloud needs a {CLOUD_PROVIDER_NAME} key and none is saved. "
                    "Add one in the Agent UI under Settings › Agent & model, in "
                    "the TUI with /provider, or set FIREWORKS_API_KEY."
                )

    def _apply(doc: dict) -> None:
        # Coming back to the mode being left reopens on the model it runs now.
        profiles = with_chat(doc.get("run_profiles") or {}, left, leaving)
        if profiles:
            doc["run_profiles"] = profiles
        else:
            doc.pop("run_profiles", None)
        doc["last_provider"] = provider
        doc["last_model"] = model
        record_pick(doc, model, mode == CLOUD)

    GaiaConfig.edit_doc(_apply)
    return model, True


def _is_cloud_id(model: str) -> bool:
    """Whether *model* is a cloud model, judged by its id alone."""
    from gaia.llm.hybrid import is_hybrid_model
    from gaia.llm.lemonade_client import cloud_model_provider

    return bool(
        cloud_model_provider(model)
        or is_hybrid_model(model)
        or model.startswith("claude-")
    )
