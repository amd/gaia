# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""What first-run setup will do on this PC, before it starts.

Answers the setup screen's questions — which Lemonade, which chat model, how
much to download — with the same functions ``gaia init`` decides them with
(:func:`resolve_init_chat_model`, :func:`with_chat_model`,
:func:`pending_download_sizes`), so the screen can never name a model setup
does not pull. Nothing here loads or downloads a model.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from gaia.logger import get_logger

logger = get_logger(__name__)


class SetupPlanError(Exception):
    """The plan could not be worked out, with what to do next."""


def _lemonade() -> Dict[str, Any]:
    """The server ``gaia init`` will use: the user's, or GAIA's own.

    GAIA's own has no address until it runs. ``url`` is None then, never the
    default port: that may be another Lemonade, whose downloads aren't GAIA's.
    """
    from gaia.config import GaiaConfigError
    from gaia.llm import lemonade_choice as choice
    from gaia.llm.lemonade_client import running_embedded_lemonade_url

    try:
        info = choice.describe()
    except (choice.LemonadeChoiceError, GaiaConfigError) as e:
        raise SetupPlanError(str(e)) from e
    external = info["mode"] == choice.MODE_EXTERNAL
    return {
        "mode": info["mode"],
        "url": info["base_url"] if external else running_embedded_lemonade_url(),
    }


def _client(url: Optional[str]):
    """A client for *url* when its server answers, else None."""
    from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError

    if url is None:
        return None
    client = LemonadeClient(base_url=url, verbose=False)
    try:
        client.health_check()
    except LemonadeClientError as e:
        logger.info("Setup plan: no Lemonade at %s yet (%s)", url, e)
        return None
    return client


class _NoServer:
    """Stands in for a server that isn't running, so nothing else is asked."""

    def get_system_info(self, **_kwargs):
        from gaia.llm.lemonade_client import LemonadeClientError

        raise LemonadeClientError("the model server is not running yet")


def _registry_size(model_id: str) -> Optional[float]:
    from gaia.llm.lemonade_client import find_model_requirement

    mr = find_model_requirement(model_id)
    return mr.size_gb if mr and mr.size_gb else None


def _options(client, capacity) -> List[Dict[str, Any]]:
    """Each default chat model, and whether this PC and its Lemonade can run it.

    Judged as :func:`recommend_default_chat_model` judges them (registry size,
    the server's version, then the memory free now); the size shown is the
    server's catalog size.
    """
    from gaia.llm.lemonade_client import (
        DEFAULT_MODEL_LADDER,
        _free_memory_rules_out,
        _model_ids_match,
        find_model_requirement,
        kv_cache_for,
        lemonade_server_version,
    )
    from gaia.llm.model_fit import check_fit, check_server_supports

    catalog: Dict[str, float] = {}
    server_version = None
    if client is not None:
        for entry in client.list_models(show_all=True).get("data", []):
            for model_id in DEFAULT_MODEL_LADDER:
                if _model_ids_match(entry.get("id"), model_id) and entry.get("size"):
                    catalog[model_id] = float(entry["size"])
        server_version = lemonade_server_version(client)

    options = []
    for model_id in DEFAULT_MODEL_LADDER:
        mr = find_model_requirement(model_id)
        size = _registry_size(model_id)
        fits: Optional[bool] = None
        reason = None
        if capacity is not None and size:
            verdict = check_fit(
                size, capacity, kv_cache_for(mr, capacity) if mr else 0.0
            )
            fits, reason = verdict.fits, verdict.reason or None
        if fits and client is not None and mr and mr.min_lemonade_version:
            supported = check_server_supports(mr.min_lemonade_version, server_version)
            if not supported.fits:
                fits, reason = False, supported.reason
        if fits and client is not None and mr and model_id != DEFAULT_MODEL_LADDER[-1]:
            busy = _free_memory_rules_out(client, mr, capacity)
            if busy:
                fits, reason = False, busy
        options.append(
            {
                "id": model_id,
                "size_gb": catalog.get(model_id, size),
                "fits": fits,
                "reason": reason,
            }
        )
    return options


def build_plan() -> Dict[str, Any]:
    """The Lemonade, chat model and downloads ``gaia init`` will use, for the
    profile this PC is set up with (``configured_setup_profile``).

    Until the server setup will use answers, the hardware cannot be read, so the
    chat model is the one setup starts from and ``decided_after_server`` says
    the final pick comes once the server answers.
    """
    from gaia.config import GaiaConfigError
    from gaia.installer.init_command import (
        INIT_PROFILES,
        configured_setup_profile,
        is_embedding_model_id,
        pending_download_sizes,
        resolve_init_chat_model,
        with_chat_model,
    )
    from gaia.llm.lemonade_client import (
        LemonadeClientError,
        recommend_default_chat_model,
        recommend_npu_chat_model,
    )

    lemonade = _lemonade()
    client = _client(lemonade["url"])
    lemonade["reachable"] = client is not None
    try:
        profile = configured_setup_profile()
        # With no server this still answers: the user's model, else the floor.
        choice = resolve_init_chat_model(
            client or _NoServer(),
            reset_corrupt=False,
            enforce_fit=False,
            profile=profile,
        )
        chat = {"id": choice.model_id, "user_set": choice.user_set}
        model_ids = with_chat_model(
            profile, INIT_PROFILES[profile]["models"], lambda: choice
        )
        if client is None:
            recommended, capacity = None, None
            sizes = {m: _registry_size(m) for m in model_ids}
        else:
            recommend = (
                recommend_npu_chat_model
                if profile == "npu"
                else recommend_default_chat_model
            )
            recommended, _, capacity = recommend(client)
            sizes = pending_download_sizes(client, model_ids)
    except GaiaConfigError as e:
        raise SetupPlanError(str(e)) from e
    except LemonadeClientError as e:
        raise SetupPlanError(
            f"The Lemonade Server at {lemonade['url']} answered but could not "
            f"list its models: {e}"
        ) from e

    chat["size_gb"] = sizes.get(chat["id"], _registry_size(chat["id"]))
    chat["downloaded"] = sizes.get(chat["id"]) == 0.0 if client else None
    return {
        "lemonade": lemonade,
        "chat_model": chat,
        "recommended_model": recommended,
        "decided_after_server": client is None and not chat["user_set"],
        "memory_gb": round(capacity.memory_gb) if capacity else None,
        "memory_source": capacity.memory_source if capacity else None,
        "options": _options(client, capacity),
        "models": [
            {
                "id": m,
                "role": "embedding" if is_embedding_model_id(m) else "chat",
                "size_gb": sizes.get(m),
            }
            for m in model_ids
        ],
        "download_gb": (
            round(sum(s for s in sizes.values() if s), 1)
            if all(s is not None for s in sizes.values())
            else None
        ),
    }


def choose_chat_model(model_id: str) -> None:
    """Record the user's pick: the hardware's recommendation clears ``default_model``.

    Clearing it keeps setup's own behaviour for its pick (Gemma is still pulled
    for images); any other ladder model is saved as the user's ``default_model``.
    The recommendation compared against is judged on capacity alone, so a pick
    made while memory is busy is not undone by a later, idler `gaia init`.
    """
    from gaia.config import GaiaConfig, GaiaConfigError
    from gaia.llm.lemonade_client import (
        DEFAULT_MODEL_LADDER,
        LemonadeClientError,
        recommend_default_chat_model,
    )

    if model_id not in DEFAULT_MODEL_LADDER:
        raise SetupPlanError(
            f"{model_id} is not one of setup's chat models "
            f"({', '.join(DEFAULT_MODEL_LADDER)}). Pick another model after "
            "setup in Settings → Model."
        )
    client = _client(_lemonade()["url"])
    if client is None:
        raise SetupPlanError(
            "The model server is not running yet, so this PC's memory can't be "
            "checked. Start setup; you can change the chat model once it is up."
        )
    try:
        recommended, _, capacity = recommend_default_chat_model(
            client, judge_free_memory=False
        )
    except LemonadeClientError as e:
        raise SetupPlanError(
            f"Could not read this PC's memory from Lemonade: {e}"
        ) from e
    if model_id != DEFAULT_MODEL_LADDER[-1]:
        option = next(o for o in _options(client, capacity) if o["id"] == model_id)
        if option["fits"] is False:
            raise SetupPlanError(f"{model_id} won't fit this PC: {option['reason']}.")
    try:
        GaiaConfig.save_key(
            "default_model", None if model_id == recommended else model_id
        )
    except GaiaConfigError as e:
        raise SetupPlanError(str(e)) from e
