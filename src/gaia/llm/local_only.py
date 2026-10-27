# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The user's network policy, and the guard that keeps "Local only" true.

``~/.gaia/policy.json`` holds ``network_policy``: ``cloud_allowed`` (the
default) or ``local_ai_only``. Under Local only no GAIA LLM request may reach a
cloud model. The guard is an allowlist: a model is permitted only when
Lemonade's catalog lists it with a recipe known to run on this machine. An
unknown id, a cloud or ``collection.*`` recipe, or a catalog that can't be read
is refused, because a blocklist keyed on id prefixes misses router aliases and
providers nobody listed.

The policy lives in its own file, not ``config.json``: ``GaiaConfig.save()``
rewrites only the fields it declares, so an older GAIA would erase it there.
See docs/plans/hybrid-local-cloud-routing.md §2.1.
"""

from __future__ import annotations

import functools
import inspect
import json
import os
import tempfile
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from gaia.logger import get_logger

log = get_logger(__name__)


class NetworkPolicy(str, Enum):
    """How much GAIA may send off the machine, least to most private."""

    CLOUD_ALLOWED = "cloud_allowed"
    LOCAL_AI_ONLY = "local_ai_only"
    OFFLINE = "offline"


#: Levels a user can choose today. Offline needs the zero-network guards
#: (plan §2.1.7); accepting it before they exist would promise more than GAIA
#: enforces.
SELECTABLE_POLICIES = (NetworkPolicy.CLOUD_ALLOWED, NetworkPolicy.LOCAL_AI_ONLY)

#: Lemonade recipes that run on this machine. Anything else is treated as
#: possibly remote: ``cloud`` is, and ``collection.*`` (router, omni) can
#: forward to a cloud member. A new local recipe is refused until it is listed
#: here — a loud error, never a leak.
LOCAL_RECIPES = frozenset(
    {
        "llamacpp",
        "llamacpp-hrx",
        "flm",
        "ryzenai-llm",
        "vllm",
        "onnxruntime",
        "sd-cpp",
        "whispercpp",
        "moonshine",
        "kokoro",
        "thenoise",
        "openmoss",
        "thinksound",
        "acestep",
        "trellis",
        "ds4",
    }
)

POLICY_KEY = "network_policy"
SETTINGS_HINT = (
    "To allow cloud models, change it in the Agent UI under Settings › Privacy, "
    "or run `gaia config set network_policy cloud_allowed`."
)


class CloudDisabledError(RuntimeError):
    """A request would reach a cloud model while Local only is on."""


def policy_path() -> Path:
    """Where the user's policy file lives; resolved per call so tests can move it."""
    override = os.environ.get("GAIA_POLICY_FILE", "").strip()
    if override:
        return Path(os.path.expanduser(override))
    config_dir = os.environ.get("GAIA_CONFIG_DIR", "").strip()
    if config_dir:
        return Path(os.path.expanduser(config_dir)) / "policy.json"
    from gaia.config import gaia_home

    return gaia_home() / "policy.json"


def _read(path: Path) -> NetworkPolicy:
    """Parse the file. Anything unreadable counts as Local only (fail closed)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        value = data.get(POLICY_KEY, NetworkPolicy.CLOUD_ALLOWED.value)
        return NetworkPolicy(value)
    except FileNotFoundError:
        return NetworkPolicy.CLOUD_ALLOWED
    except (OSError, ValueError, AttributeError) as e:
        log.error(
            f"Could not read the network policy in {path} ({e}). Treating it as "
            f"Local only until the file is fixed or rewritten with "
            f"`gaia config set network_policy <level>`."
        )
        return NetworkPolicy.LOCAL_AI_ONLY


def effective_policy() -> NetworkPolicy:
    """The policy in force now.

    Read on every call, never cached: the two levels serialize to the same
    length and Windows mtimes are coarse, so a stat-keyed cache can miss a
    switch to Local only. The file is tiny and inference calls are not.
    """
    return _read(policy_path())


def cloud_blocked() -> bool:
    """True while no LLM request may reach a cloud model."""
    return effective_policy() is not NetworkPolicy.CLOUD_ALLOWED


def set_policy(value: str) -> NetworkPolicy:
    """Validate and atomically write the user's policy. Returns what was stored."""
    try:
        policy = NetworkPolicy(str(value).strip().lower())
    except ValueError as e:
        choices = ", ".join(p.value for p in SELECTABLE_POLICIES)
        raise ValueError(
            f"Unknown network_policy {value!r}. Choose one of: {choices}."
        ) from e
    if policy not in SELECTABLE_POLICIES:
        raise ValueError(
            "network_policy 'offline' isn't available yet: GAIA can't yet block "
            "its non-AI network features, so it would promise more than it "
            "enforces. Use 'local_ai_only' to keep every AI request on this PC."
        )
    path = policy_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            # Rewriting is the documented repair for an unreadable file.
            data = {}
    data[POLICY_KEY] = policy.value
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".policy-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return policy


def is_local_recipe(recipe: Optional[str]) -> bool:
    return bool(recipe) and recipe in LOCAL_RECIPES


def assert_model_permitted(
    model: Optional[str], recipes_of: Callable[[str], List[Optional[str]]]
) -> None:
    """Refuse *model* under Local only unless the catalog says it runs here.

    *recipes_of* returns the catalog recipe of every entry *model* could name
    (empty when the catalog lists none) and may raise when the catalog can't
    be read. Every one must be local: an id that could resolve to a router
    collection as well as a local model is not confirmed local.
    """
    if not cloud_blocked():
        return
    if not model:
        raise CloudDisabledError(
            "Local only is on and this request names no model, so GAIA can't "
            f"confirm it stays on this PC. {SETTINGS_HINT}"
        )
    try:
        recipes = recipes_of(model)
    except Exception as e:  # any catalog failure means "unconfirmed"
        raise CloudDisabledError(
            f"Local only is on and GAIA couldn't read Lemonade's model catalog to "
            f"confirm that '{model}' runs on this PC ({e}). Start Lemonade and "
            f"retry. {SETTINGS_HINT}"
        ) from e
    if recipes and all(is_local_recipe(r) for r in recipes):
        return
    remote = [r for r in recipes if not is_local_recipe(r)]
    if not recipes:
        why = "Lemonade's catalog doesn't list it"
    elif len(set(recipes)) > 1:
        why = (
            f"its name matches several catalog entries ({', '.join(map(str, recipes))}) "
            "and not all of them run on this PC"
        )
    elif remote[0] is None:
        why = "Lemonade's catalog lists it without saying where it runs"
    elif remote[0] == "cloud":
        why = "it is a cloud model"
    elif str(remote[0]).startswith("collection."):
        why = f"it is a '{remote[0]}' model, which can forward requests to the cloud"
    else:
        why = f"its recipe '{remote[0]}' isn't one GAIA knows runs on this PC"
    raise CloudDisabledError(
        f"Local only is on, so '{model}' was blocked: {why}. Pick a local "
        f"model. {SETTINGS_HINT}"
    )


def catalog_recipes(client: Any, model: str) -> List[Optional[str]]:
    """Recipes of the catalog entries *model* names on *client*'s server.

    An exact id wins. Otherwise every entry equal under Lemonade's lenient
    matching (case, ``user.`` prefix) counts. A miss re-reads the catalog once.
    """
    from gaia.llm.lemonade_client import _model_ids_match

    def lookup(entries: Dict[str, Dict[str, Any]]) -> List[Optional[str]]:
        exact = entries.get(model)
        if exact is not None:
            return [exact.get("recipe")]
        return [
            e.get("recipe")
            for mid, e in entries.items()
            if _model_ids_match(mid, model)
        ]

    found = lookup(client._model_metadata)
    if found:
        return found
    catalog = client.list_models(show_all=True)
    return lookup({e["id"]: e for e in catalog.get("data", []) if e.get("id")})


def assert_provider_permitted(provider: str) -> None:
    """Refuse a non-Lemonade cloud provider (e.g. Claude) under Local only."""
    if cloud_blocked():
        raise CloudDisabledError(
            f"Local only is on, so GAIA won't send requests to {provider}. "
            f"{SETTINGS_HINT}"
        )


def local_only_key_message(provider: str) -> str:
    """Why a cloud key wasn't handed to Lemonade, and how to change that."""
    return (
        f"Local only is on, so GAIA won't give Lemonade a {provider} key and "
        f"{provider} models stay off. {SETTINGS_HINT}"
    )


def guard_inference(default_model: Optional[Callable[[Any], Optional[str]]] = None):
    """Decorate a ``LemonadeClient`` inference method with the Local only check.

    The model is read from the method's ``model`` argument, positional or
    keyword. *default_model(self)* supplies the id the method falls back to
    when the caller passes none, so the check sees the model actually used.
    """

    def decorator(method):
        signature = inspect.signature(method)

        @functools.wraps(method)
        def wrapper(self, *args, **kwargs):
            if cloud_blocked():
                bound = signature.bind(self, *args, **kwargs)
                bound.apply_defaults()
                model = bound.arguments.get("model")
                if not model and default_model is not None:
                    model = default_model(self)
                # Re-point at a restarted GAIA Lemonade first, or the catalog
                # read below hits a dead address. Any key replay it attempts is
                # refused under Local only (cloud_keys.ensure_authenticated).
                follow = getattr(self, "_follow_gaia_lemonade", None)
                if follow is not None:
                    follow()
                assert_model_permitted(model, lambda m: catalog_recipes(self, m))
            return method(self, *args, **kwargs)

        wrapper.__local_only_guarded__ = True
        return wrapper

    return decorator
