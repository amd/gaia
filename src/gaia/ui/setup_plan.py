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

import os
from typing import Any, Dict, List, Optional

from gaia.installer import model_choice
from gaia.logger import get_logger

logger = get_logger(__name__)

PROFILE = "gaia"


class SetupPlanError(Exception):
    """The plan could not be worked out, with what to do next."""


class ServerNotRunning(SetupPlanError):
    """The answer needs the model server, which isn't running yet."""


class UnknownModel(SetupPlanError):
    """The model can't be used that way: unknown, not downloaded, not planned."""


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


_FREE_MEMORY_UNREAD = (
    "GAIA couldn't read this PC's free memory, so it can't tell whether this "
    "model would load now. Details are in the GAIA server log."
)


def _options(client, capacity) -> List[Dict[str, Any]]:
    """Each default chat model, and whether this PC and its Lemonade can run it.

    Judged as :func:`recommend_default_chat_model` judges them (registry size,
    the server's version, then the memory free now); the size shown is the
    server's catalog size.
    """
    from gaia.llm.lemonade_client import (
        DEFAULT_MODEL_LADDER,
        LemonadeClientError,
        _model_ids_match,
        find_model_requirement,
        free_memory_shortfall,
        kv_cache_for,
        lemonade_server_version,
    )
    from gaia.llm.model_fit import (
        ModelFitError,
        check_fit,
        check_server_supports,
        required_memory_gb,
    )

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
            kv = kv_cache_for(mr, capacity) if mr else 0.0
            verdict = check_fit(size, capacity, kv)
            fits, reason = verdict.fits, verdict.reason or None
            need = required_memory_gb(size, kv)
            if need > capacity.memory_gb:
                # The memory reason in the plan's decimal GB, like its numbers.
                reason = model_choice.memory_reason(need, capacity)
        if fits and client is not None and mr and mr.min_lemonade_version:
            supported = check_server_supports(mr.min_lemonade_version, server_version)
            if not supported.fits:
                fits, reason = False, supported.reason
        if fits and client is not None and mr and model_id != DEFAULT_MODEL_LADDER[-1]:
            try:
                busy = free_memory_shortfall(client, mr, capacity)
            except (ModelFitError, LemonadeClientError):
                # The screen shows numbers; the reading error goes to the log.
                logger.exception("Setup plan: could not read free memory")
                fits, reason = None, _FREE_MEMORY_UNREAD
            else:
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
        raise ServerNotRunning(
            f"The '{profile}' profile's models come from the model server, which "
            "is not running yet. Run `gaia init --skip-models` to start it first."
        )
    return client.get_required_models(config["agent"])


def _display_name(model_id: str) -> str:
    """The name the model picker and the sidebar show, so a model reads the same everywhere."""
    from gaia.ui.local_models import friendly_name

    return friendly_name(model_id)


def _model_source(entry: Optional[Dict[str, Any]]) -> Optional[str]:
    """Where a model downloads from, as Lemonade's catalog names it."""
    checkpoint = (entry or {}).get("checkpoint")
    if not isinstance(checkpoint, str) or "/" not in checkpoint:
        return None
    return f"huggingface.co/{checkpoint.split(':')[0]}"


def _components(lemonade: Dict[str, Any], skip_models: bool) -> List[Dict[str, Any]]:
    """Everything besides the models that setup fetches, so the screen can say
    what comes to this PC, how big it is and from where. ``size_gb`` is None
    when nothing here can know it before the download starts."""
    from gaia.hub import installer as hub_installer
    from gaia.llm.lemonade_embedded import (
        BUNDLED_ARCHIVE_DIR_ENV,
        GITHUB_RELEASE_BASE,
        EmbeddedLemonade,
    )
    from gaia.version import LEMONADE_VERSION

    components: List[Dict[str, Any]] = []
    if lemonade["mode"] != "external":
        components.append(
            {
                "id": "server",
                "version": LEMONADE_VERSION,
                "size_gb": 0.007,
                "source": (
                    "included with GAIA"
                    if os.environ.get(BUNDLED_ARCHIVE_DIR_ENV)
                    else GITHUB_RELEASE_BASE.removeprefix("https://")
                ),
                "installed": EmbeddedLemonade().is_installed(),
            }
        )
    if not skip_models:
        components.append(
            {
                "id": "backend",
                "version": None,
                "size_gb": 0.141,
                "source": "github.com/ggml-org/llama.cpp",
                "installed": None,
            }
        )
    components.append(
        {
            "id": "packages",
            "version": None,
            "size_gb": None,
            "source": "pypi.org",
            "installed": None,
        }
    )
    components.append(
        {
            "id": "agent",
            "version": None,
            "size_gb": None,
            "source": "hub.amd-gaia.ai",
            "installed": hub_installer.read_sentinel("gaia") is not None,
        }
    )
    return components


def _backends(system_info: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Each backend Lemonade reports for this PC, and whether it is here yet."""
    backends = []
    for recipe, info in ((system_info or {}).get("recipes") or {}).items():
        default = info.get("default_backend")
        for name, backend in (info.get("backends") or {}).items():
            backends.append(
                {
                    "recipe": recipe,
                    "backend": name,
                    "state": backend.get("state"),
                    "default": name == default,
                }
            )
    return backends


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
        remaining_download_gb,
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
    offered = [*model_choice.VISION_ONLY_MODELS, *model_choice.OPTIONAL_MODELS]
    known = [*planned, *_known_chat_models(), *offered]
    # GAIA's own models: a Lemonade-only model the user made the chat model is
    # planned, but stays in the Lemonade list where they found it.
    gaia_known = [
        *(m for m in planned if not same(m, chat_id)),
        *(INIT_PROFILES[profile]["models"] or []),
        *_known_chat_models(),
        *offered,
    ]
    for model_id in known:
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
        remaining: Optional[float] = (
            0.0 if downloaded else remaining_download_gb(full, on_disk.get(model_id))
        )
        is_planned = listed(model_id, planned)
        roles = model_choice.roles_for(model_id, chat_id)
        catalog.append(
            {
                "id": model_id,
                "name": _display_name(model_id),
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
                # GAIA's own model, not one only this Lemonade has.
                "gaia_model": listed(model_id, gaia_known),
                "selected": listed(model_id, selected),
                "skipped": model_choice.is_skipped(model_id, skipped),
                "chat_choice": not is_embedding_model_id(model_id)
                and not model_choice.optional_role(model_id)
                and not model_choice.is_vision_only(model_id)
                and (entry is None or model_choice.is_chat_catalog_entry(entry)),
                "support": model_choice.support_label(model_id),
                "reads_images": model_choice.reads_images(model_id),
                "feature_tier": model_choice.feature_tier(model_id),
                "source": _model_source(entry),
                **model_choice.memory_fit(model_id, full, capacity),
            }
        )
    return catalog


def build_plan(
    profile: str = PROFILE,
    skip_chat_model: bool = False,
    extra_models: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """The Lemonade, models and downloads ``gaia init --profile <profile>`` will use.

    Until the server setup will use answers, the hardware cannot be read, so the
    chat model is the one setup starts from and ``decided_after_server`` says
    the final pick comes once the server answers. ``catalog`` is every model
    the user can pick from; ``models`` is what setup will download, without
    the ones in ``skipped_models``. *extra_models* are downloaded beside the
    profile's, as ``gaia init --download-model`` does.
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
        recommend_npu_chat_model,
    )
    from gaia.llm.model_fit import ModelFitError

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
            profile=profile,
        )
        chat = {"id": choice.model_id, "user_set": choice.user_set}
        planned = with_chat_model(
            profile, _profile_models(profile, client), lambda: choice
        )
        if skip_chat_model:
            planned = [m for m in planned if is_embedding_model_id(m)]
        for model_id in extra_models or []:
            if not any(model_choice.ids_match(model_id, m) for m in planned):
                planned.append(model_id)
        skipped = model_choice.skipped_models()
        selected, _ = model_choice.split_skipped(planned, skipped)
        system_info = None
        if client is None:
            recommended, capacity = None, None
        else:
            recommend = (
                recommend_npu_chat_model
                if profile == "npu"
                else recommend_default_chat_model
            )
            recommended, _, capacity = recommend(client)
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
    except (GaiaConfigError, ModelFitError) as e:
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
        "memory_gb": (
            round(model_choice.to_decimal_gb(capacity.memory_gb)) if capacity else None
        ),
        "memory_source": capacity.memory_source if capacity else None,
        "capacity_gb": (
            round(model_choice.to_decimal_gb(capacity.memory_gb), 1)
            if capacity
            else None
        ),
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
        "components": _components(lemonade, skip_models=False),
        "backends": _backends(system_info),
        "vision_model": _saved_vision_model(),
    }


def run_disk_warning(
    profile: str = PROFILE,
    skip_chat_model: bool = False,
    extra_models: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """The low-disk warning for the selected downloads, or None when they fit."""
    plan = build_plan(profile, skip_chat_model, extra_models)
    return model_choice.disk_shortfall(
        [(m["id"], m["size_gb"]) for m in plan["models"]], plan["disk"]
    )


def check_extra_models(model_ids: List[str]) -> List[str]:
    """*model_ids* as the catalog names them; raises for one nobody has."""
    if not model_ids:
        return []
    catalog = build_plan()["catalog"]
    out = []
    for model_id in model_ids:
        entry = next(
            (e for e in catalog if model_choice.ids_match(e["id"], model_id)), None
        )
        if entry is None:
            raise UnknownModel(
                f"{model_id} is not a model GAIA knows or one in this Lemonade "
                "Server, so setup can't download it."
            )
        out.append(entry["id"])
    return out


def _require_client(action: str):
    """A client for the server setup uses; raises when it isn't running yet."""
    client = _client(_lemonade()["url"])
    if client is None:
        raise ServerNotRunning(
            f"The model server is not running yet, so GAIA can't {action}. "
            "Start setup first; this works once the server is up."
        )
    return client


def chat_model_warning(model_id: str) -> Optional[Dict[str, Any]]:
    """The ``no_fit`` warning for *model_id*, or None when nothing needs confirming.

    Raises :class:`SetupPlanError` when it can't be the chat model at all.
    """
    try:
        choose_chat_model(model_id, acknowledge_no_fit=False, save=False)
    except NeedsAcknowledgement as e:
        return dict(e.detail)
    return None


def choose_chat_model(
    model_id: str, acknowledge_no_fit: bool = False, save: bool = True
) -> None:
    """Make *model_id* the chat model setup uses, saved as ``default_model``.

    Any of GAIA's chat models, or any chat model this Lemonade has downloaded
    (FLM included). The hardware's own recommendation clears ``default_model``
    instead, which keeps setup's own behaviour for it. One that may not fit
    this PC raises :class:`NeedsAcknowledgement` until *acknowledge_no_fit*.
    Choosing a model also takes it off ``skipped_models``. ``save=False``
    only checks.
    """
    from gaia.config import GaiaConfig, GaiaConfigError
    from gaia.llm.lemonade_client import (
        DEFAULT_MODEL_LADDER,
        LemonadeClientError,
        recommend_default_chat_model,
    )

    client = _require_client("check this PC's memory for that model")
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
        raise UnknownModel(
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
    if not save:
        return
    try:
        GaiaConfig.save_key(
            "default_model", None if model_id == recommended else model_id
        )
        if entry["skipped"]:
            model_choice.set_skipped(model_id, False)
    except GaiaConfigError as e:
        raise SetupPlanError(str(e)) from e


def _saved_vision_model() -> Optional[str]:
    from gaia.config import GaiaConfig, GaiaConfigError

    try:
        return GaiaConfig.load().vision_model
    except GaiaConfigError as e:
        raise SetupPlanError(str(e)) from e


def choose_vision_model(model_id: Optional[str]) -> None:
    """Save the model that reads images; None goes back to GAIA's choice."""
    from gaia.config import GaiaConfig, GaiaConfigError

    if model_id is not None:
        entry = next(
            (
                e
                for e in build_plan()["catalog"]
                if model_choice.ids_match(e["id"], model_id)
            ),
            None,
        )
        if entry is None or not (
            entry["reads_images"] or model_choice.ROLE_VISION in entry["roles"]
        ):
            raise UnknownModel(f"{model_id} can't read images.")
        model_id = entry["id"]
    try:
        GaiaConfig.save_key("vision_model", model_id)
    except GaiaConfigError as e:
        raise SetupPlanError(str(e)) from e


def _job_model(job: str) -> Optional[str]:
    """The model *job* would load now, or None when no local model is involved."""
    from gaia.config import GaiaConfig
    from gaia.llm.lemonade_client import (
        DEFAULT_EMBEDDING_MODEL,
        cloud_model_provider,
        resolve_default_chat_model,
        resolve_default_vision_model,
    )

    if job == model_choice.ROLE_CHAT:
        config = GaiaConfig.load()
        model = config.last_model or resolve_default_chat_model()
        cloud = model.startswith("claude-") or cloud_model_provider(model)
        return None if cloud else model
    if job == model_choice.ROLE_VISION:
        return resolve_default_vision_model()
    if job == model_choice.ROLE_EMBEDDING:
        return DEFAULT_EMBEDDING_MODEL
    return next(
        (m for m, role in model_choice.OPTIONAL_MODELS.items() if role == job), None
    )


def model_need(job: str) -> Dict[str, Any]:
    """Whether *job* needs a model that isn't here, and which: the prompt's facts.

    With no server reachable nothing can be said, so nothing is asked and the
    job reports the server's own error.
    """
    from gaia.config import GaiaConfigError

    try:
        model_id = _job_model(job)
    except GaiaConfigError as e:
        raise SetupPlanError(str(e)) from e
    plan = build_plan()
    if model_id is None or not plan["lemonade"]["reachable"]:
        return {"job": job, "needed": False, "model": None, "disk_free_gb": None}
    entry = next(
        (e for e in plan["catalog"] if model_choice.ids_match(e["id"], model_id)),
        None,
    )
    if entry is None or entry["downloaded"]:
        return {"job": job, "needed": False, "model": None, "disk_free_gb": None}
    return {
        "job": job,
        "needed": True,
        "model": {
            "id": entry["id"],
            "name": entry["name"],
            "size_gb": entry["size_gb"],
            "source": entry["source"],
        },
        "disk_free_gb": (plan.get("disk") or {}).get("free_gb"),
    }


def set_model_skipped(model_id: str, skipped: bool, profile: str = PROFILE) -> None:
    """Skip one of *profile*'s models, or take it back; saved in ``skipped_models``."""
    from gaia.config import GaiaConfigError

    if skipped:
        planned = [e["id"] for e in build_plan(profile)["catalog"] if e["planned"]]
        if not any(model_choice.ids_match(model_id, m) for m in planned):
            raise UnknownModel(
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

    client = _require_client(f"remove {model_id}")
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
            raise UnknownModel(
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


def _download_entry(model_id: str):
    plan = build_plan()
    entry = next(
        (e for e in plan["catalog"] if model_choice.ids_match(e["id"], model_id)),
        None,
    )
    if entry is None:
        raise UnknownModel(
            f"{model_id} is not a model GAIA knows or one in this Lemonade "
            "Server. Add it in Settings → Model first."
        )
    return plan, entry


def download_disk_warning(model_id: str) -> Optional[Dict[str, Any]]:
    """The low-disk warning for downloading *model_id* now, or None."""
    plan, entry = _download_entry(model_id)
    return model_choice.disk_shortfall([(entry["id"], entry["size_gb"])], plan["disk"])


def prepare_download(model_id: str, acknowledge_low_disk: bool = False):
    """``(client, model id)`` for downloading one model now, taken off
    ``skipped_models``. Raises :class:`NeedsAcknowledgement` when it needs more
    disk than is free and nobody confirmed."""
    from gaia.config import GaiaConfigError

    _, entry = _download_entry(model_id)
    if not acknowledge_low_disk:
        shortfall = download_disk_warning(entry["id"])
        if shortfall:
            raise NeedsAcknowledgement(shortfall)
    client = _require_client(f"download {entry['id']}")
    try:
        model_choice.set_skipped(entry["id"], False)
    except GaiaConfigError as e:
        raise SetupPlanError(str(e)) from e
    return client, entry["id"]
