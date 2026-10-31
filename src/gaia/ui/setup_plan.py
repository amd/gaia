# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""What first-run setup will do on this PC, and the choices that change it.

Answers the setup screen's questions — which Lemonade, which models, how much
to download, whether it fits — with the same functions ``gaia init`` decides
them with (:func:`resolve_init_chat_model`, :func:`with_chat_model`,
:func:`pending_download_sizes`), so the Agent UI, the TUI (``gaia init --plan
--json``) and ``gaia init`` itself can never disagree. Nothing here loads or
downloads a model; :func:`remove_model` is the one call that deletes one, and
only when asked.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from gaia.installer import model_choice
from gaia.logger import get_logger

logger = get_logger(__name__)

PROFILE = "gaia"


class SetupPlanError(Exception):
    """The plan could not be worked out, with what to do next."""


# Re-exported: the router and CLI answer a needed confirmation from here.
NeedsAcknowledgement = model_choice.NeedsAcknowledgement


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


def _is_local(lemonade: Dict[str, Any]) -> bool:
    """GAIA's own server, or the user's on this PC: its model drive is readable here."""
    from gaia.llm.lemonade_client import _is_local_url

    return lemonade["mode"] != "external" or bool(
        lemonade["url"] and _is_local_url(lemonade["url"])
    )


def _known_chat_models() -> List[str]:
    from gaia.llm.lemonade_client import (
        DEFAULT_MODEL_LADDER,
        FLASH_OPTION_MODEL_NAME,
        OPTIONAL_CHAT_MODELS,
    )

    return [*DEFAULT_MODEL_LADDER, *OPTIONAL_CHAT_MODELS, FLASH_OPTION_MODEL_NAME]


def _profile_models(profile: str, client) -> List[str]:
    from gaia.installer.init_command import INIT_PROFILES

    config = INIT_PROFILES[profile]
    if config["models"]:
        return list(config["models"])
    if client is None:
        raise SetupPlanError(
            f"The '{profile}' profile's models come from the model server, which "
            "is not running yet. Run `gaia init --skip-models` to start it first."
        )
    return client.get_required_models(config["agent"])


def _display_name(model_id: str, entry: Optional[Dict[str, Any]]) -> str:
    from gaia.llm.lemonade_client import find_model_requirement

    mr = find_model_requirement(model_id)
    if mr and mr.display_name:
        return mr.display_name
    return str((entry or {}).get("name") or model_id.removeprefix("user."))


def _catalog(
    client,
    profile: str,
    chat_id: str,
    planned: List[str],
    recommended: Optional[str],
    capacity,
    store_path: Optional[str],
    skipped: List[str],
    selected: List[str],
) -> List[Dict[str, Any]]:
    """Every model setup can pull or use: the plan, GAIA's chat models, and
    each chat model already downloaded in this Lemonade (FLM included)."""
    from gaia.installer.init_command import (
        INIT_PROFILES,
        bytes_already_on_disk,
        is_embedding_model_id,
    )

    same = model_choice.ids_match
    lemonade_list = (
        client.list_models(show_all=True).get("data", []) if client is not None else []
    )

    def entry_for(model_id: str) -> Optional[Dict[str, Any]]:
        return next((e for e in lemonade_list if same(e.get("id"), model_id)), None)

    def listed(model_id: str, ids: List[str]) -> bool:
        return any(same(model_id, m) for m in ids)

    order: List[str] = []
    for model_id in [*planned, *_known_chat_models()]:
        if not listed(model_id, order):
            order.append(model_id)
    for e in lemonade_list:
        model_id = e.get("id")
        if (
            model_id
            and e.get("downloaded")
            and model_choice.is_chat_catalog_entry(e)
            and not listed(model_id, order)
        ):
            order.append(model_id)

    pending = [
        m
        for m in order
        if client is not None and not (entry_for(m) or {}).get("downloaded")
    ]
    on_disk = bytes_already_on_disk(client, pending, store_path) if pending else {}
    base = INIT_PROFILES[profile]["models"] or []

    catalog = []
    for model_id in order:
        entry = entry_for(model_id)
        downloaded = bool(entry.get("downloaded")) if entry else False
        full = entry.get("size") if entry else None
        if not isinstance(full, (int, float)):
            full = _registry_size(model_id)
        partial_gb = 0.0 if downloaded else round((on_disk.get(model_id) or 0) / 1e9, 2)
        if downloaded:
            remaining: Optional[float] = 0.0
        elif full is None:
            remaining = None
        else:
            remaining = round(max(0.0, float(full) - partial_gb), 2)
        is_planned = listed(model_id, planned)
        roles = model_choice.roles_for(model_id, chat_id)
        catalog.append(
            {
                "id": model_id,
                "name": _display_name(model_id, entry),
                "roles": roles,
                "purpose": model_choice.purpose(roles),
                "if_skipped": (
                    model_choice.if_skipped(model_id, roles)
                    if is_planned or set(roles) - {model_choice.ROLE_CHAT}
                    else None
                ),
                "recipe": (entry or {}).get("recipe"),
                "size_gb": remaining,
                "full_size_gb": float(full) if full is not None else None,
                "partial": partial_gb > 0,
                "partial_gb": partial_gb,
                "downloaded": downloaded if client is not None else None,
                "in_lemonade": entry is not None,
                "recommended": bool(
                    (recommended and same(model_id, recommended))
                    or listed(model_id, base)
                ),
                "planned": is_planned,
                "selected": listed(model_id, selected),
                "skipped": model_choice.is_skipped(model_id, skipped),
                "chat_choice": not is_embedding_model_id(model_id)
                and (entry is None or model_choice.is_chat_catalog_entry(entry)),
                **model_choice.memory_fit(model_id, full, capacity),
            }
        )
    return catalog


def build_plan(profile: str = PROFILE, skip_chat_model: bool = False) -> Dict[str, Any]:
    """The Lemonade, models and downloads ``gaia init --profile <profile>`` will use.

    Until the server setup will use answers, the hardware cannot be read, so the
    chat model is the one setup starts from and ``decided_after_server`` says
    the final pick comes once the server answers. ``catalog`` is every model
    the user can pick from; ``models`` is what setup will download, without
    the ones in ``skipped_models``.
    """
    from gaia.config import GaiaConfigError
    from gaia.installer.init_command import (
        INIT_PROFILES,
        is_embedding_model_id,
        resolve_init_chat_model,
        with_chat_model,
    )
    from gaia.llm.lemonade_client import (
        LemonadeClientError,
        recommend_default_chat_model,
    )

    if profile not in INIT_PROFILES:
        raise SetupPlanError(
            f"Unknown profile '{profile}'. Valid profiles: {', '.join(INIT_PROFILES)}"
        )
    lemonade = _lemonade()
    client = _client(lemonade["url"])
    lemonade["reachable"] = client is not None
    try:
        # With no server this still answers: the user's model, else the floor.
        choice = resolve_init_chat_model(
            client or _NoServer(),
            reset_corrupt=False,
            enforce_fit=False,
        )
        chat = {"id": choice.model_id, "user_set": choice.user_set}
        planned = with_chat_model(
            profile, _profile_models(profile, client), lambda: choice
        )
        if skip_chat_model:
            planned = [m for m in planned if is_embedding_model_id(m)]
        skipped = model_choice.skipped_models()
        selected, _ = model_choice.split_skipped(planned, skipped)
        system_info = None
        if client is None:
            recommended, capacity = None, None
        else:
            recommended, _, capacity = recommend_default_chat_model(client)
            try:
                system_info = client.get_system_info(timeout=15)
            except LemonadeClientError as e:
                logger.warning("Setup plan: /system-info failed: %s", e)
        disk = model_choice.model_store(system_info, local=_is_local(lemonade))
        catalog = _catalog(
            client,
            profile,
            chat["id"],
            planned,
            recommended,
            capacity,
            disk["path"] if disk["source"] == "lemonade" else None,
            skipped,
            selected,
        )
    except GaiaConfigError as e:
        raise SetupPlanError(str(e)) from e
    except LemonadeClientError as e:
        raise SetupPlanError(
            f"The Lemonade Server at {lemonade['url']} answered but could not "
            f"list its models: {e}"
        ) from e

    by_id = {e["id"]: e for e in catalog}
    models = [
        {**by_id[m], "role": "embedding" if is_embedding_model_id(m) else "chat"}
        for m in selected
    ]
    chat_entry = by_id.get(chat["id"]) or {}
    chat["size_gb"] = chat_entry.get("size_gb", _registry_size(chat["id"]))
    chat["downloaded"] = chat_entry.get("downloaded") if client else None
    sizes = [m["size_gb"] for m in models]
    shortfall = model_choice.disk_shortfall(
        [(m["id"], m["size_gb"]) for m in models], disk
    )
    return {
        "profile": profile,
        "lemonade": lemonade,
        "chat_model": chat,
        "recommended_model": recommended,
        "decided_after_server": client is None and not chat["user_set"],
        "memory_gb": round(capacity.memory_gb) if capacity else None,
        "memory_source": capacity.memory_source if capacity else None,
        "capacity_gb": round(capacity.memory_gb, 1) if capacity else None,
        "disk": disk,
        "download_gb": (
            round(sum(s for s in sizes if s), 1)
            if all(s is not None for s in sizes)
            else None
        ),
        "disk_warning": shortfall["message"] if shortfall else None,
        "skipped_models": skipped,
        "options": _options(client, capacity),
        "models": models,
        "catalog": catalog,
    }


def check_disk(
    profile: str = PROFILE,
    skip_chat_model: bool = False,
    acknowledge_low_disk: bool = False,
) -> Dict[str, Any]:
    """The plan, after raising :class:`NeedsAcknowledgement` when the selected
    downloads need more disk than the model drive has and nobody confirmed."""
    plan = build_plan(profile, skip_chat_model)
    shortfall = model_choice.disk_shortfall(
        [(m["id"], m["size_gb"]) for m in plan["models"]], plan["disk"]
    )
    if shortfall and not acknowledge_low_disk:
        raise NeedsAcknowledgement(shortfall)
    return plan


def _require_client():
    client = _client(_lemonade()["url"])
    if client is None:
        raise SetupPlanError(
            "The model server is not running yet, so this PC's memory can't be "
            "checked. Start setup; you can change the chat model once it is up."
        )
    return client


def choose_chat_model(model_id: str, acknowledge_no_fit: bool = False) -> None:
    """Make *model_id* the chat model setup uses, saved as ``default_model``.

    Any of GAIA's chat models, or any chat model this Lemonade has downloaded
    (FLM included). The hardware's own recommendation clears ``default_model``
    instead, which keeps setup's own behaviour for it. One that may not fit
    this PC raises :class:`NeedsAcknowledgement` until *acknowledge_no_fit*.
    Choosing a model also takes it off ``skipped_models``.
    """
    from gaia.config import GaiaConfig, GaiaConfigError
    from gaia.llm.lemonade_client import (
        DEFAULT_MODEL_LADDER,
        LemonadeClientError,
        recommend_default_chat_model,
    )

    client = _require_client()
    try:
        recommended, _, capacity = recommend_default_chat_model(
            client, judge_free_memory=False
        )
    except LemonadeClientError as e:
        raise SetupPlanError(
            f"Could not read this PC's memory from Lemonade: {e}"
        ) from e
    entry = next(
        (
            e
            for e in build_plan()["catalog"]
            if model_choice.ids_match(e["id"], model_id)
        ),
        None,
    )
    if entry is None or not entry["chat_choice"]:
        raise SetupPlanError(
            f"{model_id} is not a chat model GAIA knows or one this Lemonade "
            "Server has downloaded. Download it first (Settings → Model in the "
            "Agent UI), then pick it."
        )
    model_id = entry["id"]
    fits, reason = entry["fits"], entry["fit_reason"]
    if model_id in DEFAULT_MODEL_LADDER and model_id != DEFAULT_MODEL_LADDER[-1]:
        # The ladder also checks the server's version and the memory free now.
        option = next(o for o in _options(client, capacity) if o["id"] == model_id)
        fits, reason = option["fits"], option["reason"]
    if fits is False and not acknowledge_no_fit:
        raise NeedsAcknowledgement(
            {
                "code": "no_fit",
                "message": (f"{model_id} may not run on this PC: {reason}."),
                "model_id": model_id,
                "need_gb": entry["need_gb"],
                "capacity_gb": entry["capacity_gb"],
                "acknowledge": "acknowledge_no_fit",
            }
        )
    try:
        GaiaConfig.save_key(
            "default_model", None if model_id == recommended else model_id
        )
        if entry["skipped"]:
            model_choice.set_skipped(model_id, False)
    except GaiaConfigError as e:
        raise SetupPlanError(str(e)) from e


def set_model_skipped(model_id: str, skipped: bool) -> None:
    """Skip one of setup's models, or take it back; saved in ``skipped_models``."""
    from gaia.config import GaiaConfigError

    if skipped:
        planned = [e["id"] for e in build_plan()["catalog"] if e["planned"]]
        if not any(model_choice.ids_match(model_id, m) for m in planned):
            raise SetupPlanError(
                f"{model_id} is not one of the models setup downloads "
                f"({', '.join(planned)}), so there is nothing to skip."
            )
    try:
        model_choice.set_skipped(model_id, skipped)
    except GaiaConfigError as e:
        raise SetupPlanError(str(e)) from e


def remove_model(model_id: str) -> float:
    """Delete *model_id* from Lemonade at the user's request; returns GB freed.

    It joins ``skipped_models`` so the next setup does not pull it back.
    """
    from gaia.config import GaiaConfigError
    from gaia.llm.lemonade_client import LemonadeClientError

    client = _require_client()
    try:
        entry = next(
            (
                e
                for e in client.list_models(show_all=True).get("data", [])
                if model_choice.ids_match(e.get("id"), model_id)
            ),
            None,
        )
        if entry is None or not entry.get("downloaded"):
            raise SetupPlanError(
                f"{model_id} is not downloaded, so there is nothing to remove."
            )
        client.delete_model(entry["id"])
    except LemonadeClientError as e:
        raise SetupPlanError(f"Lemonade could not remove {model_id}: {e}") from e
    try:
        model_choice.set_skipped(model_id, True)
    except GaiaConfigError as e:
        raise SetupPlanError(
            f"{model_id} was removed, but GAIA could not record it as skipped, so "
            f"the next setup downloads it again: {e}"
        ) from e
    size = entry.get("size")
    return round(float(size), 1) if isinstance(size, (int, float)) else 0.0


def prepare_download(model_id: str, acknowledge_low_disk: bool = False):
    """``(client, model id)`` for downloading one model now, taken off
    ``skipped_models``. Raises :class:`NeedsAcknowledgement` when it needs more
    disk than is free and nobody confirmed."""
    from gaia.config import GaiaConfigError

    plan = build_plan()
    entry = next(
        (e for e in plan["catalog"] if model_choice.ids_match(e["id"], model_id)),
        None,
    )
    if entry is None:
        raise SetupPlanError(
            f"{model_id} is not a model GAIA knows or one in this Lemonade "
            "Server. Add it in Settings → Model first."
        )
    shortfall = model_choice.disk_shortfall(
        [(entry["id"], entry["size_gb"])], plan["disk"]
    )
    if shortfall and not acknowledge_low_disk:
        raise NeedsAcknowledgement(shortfall)
    client = _require_client()
    try:
        model_choice.set_skipped(entry["id"], False)
    except GaiaConfigError as e:
        raise SetupPlanError(str(e)) from e
    return client, entry["id"]
