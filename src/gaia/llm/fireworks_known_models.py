# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Keep Fireworks chat working while its model list is down.

Lemonade only routes models it knows, and it learns Fireworks' from one list.
When that list fails, chat still works but every Fireworks model is unknown. So
GAIA registers the models it already prices in the embedded server's
``user_models.json`` and restarts it.

The key goes in through ``LEMONADE_FIREWORKS_API_KEY`` at start, because a key
posted to ``cloud/auth`` makes Lemonade re-read the list and drop these entries.
The entries are the only state: each start asks Fireworks again and removes them
once the list answers.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List

from gaia.llm.providers.fireworks import (
    API_KEY_ENV_VARS,
    LEMONADE_PREFIX,
    ModelListState,
    check_model_list,
)
from gaia.logger import get_logger

log = get_logger(__name__)

_USER_MODELS = "user_models.json"
_LEMONADE_KEY_VAR = API_KEY_ENV_VARS[1]


class KnownModelsError(RuntimeError):
    """The model server's model file could not be read or written."""


def offered_models() -> List[str]:
    """Bare Fireworks names GAIA prices and still offers."""
    from gaia.eval.bench.metering import RATES
    from gaia.llm.recommended_models import rank

    # A dated snapshot priced only for old comparisons is no longer offered.
    return [
        name
        for name in RATES
        if rank(f"{LEMONADE_PREFIX}{name}") or not re.search(r"-\d{4}$", name)
    ]


def _entries() -> Dict[str, Dict[str, object]]:
    return {
        f"{LEMONADE_PREFIX}{name}": {
            "checkpoint": f"accounts/fireworks/models/{name}",
            "cloud_provider": "fireworks",
            "labels": ["chat", "custom", "tool-calling"],
            "recipe": "cloud",
            "size": 0.0,
            "suggested": True,
        }
        for name in offered_models()
    }


def _read(config_dir: Path) -> Dict[str, object]:
    path = config_dir / _USER_MODELS
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise KnownModelsError(f"Could not read {path}: {e}") from e
    if not isinstance(loaded, dict):
        raise KnownModelsError(f"{path} is not a JSON object.")
    return loaded


def _write(config_dir: Path, models: Dict[str, object]) -> None:
    path = config_dir / _USER_MODELS
    tmp = path.with_name(path.name + ".tmp")
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(models, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError as e:
        raise KnownModelsError(f"Could not write {path}: {e}") from e


def is_active(config_dir: Path) -> bool:
    """Whether the known models are registered."""
    return any(name.startswith(LEMONADE_PREFIX) for name in _read(config_dir))


def add_entries(config_dir: Path) -> None:
    """Register the known models. Only with the server stopped: it rewrites the file."""
    _write(config_dir, {**_read(config_dir), **_entries()})


def remove_entries(config_dir: Path) -> None:
    """Remove the known models. Only with the server stopped: it rewrites the file."""
    models = _read(config_dir)
    kept = {k: v for k, v in models.items() if not k.startswith(LEMONADE_PREFIX)}
    if len(kept) != len(models):
        _write(config_dir, kept)


def start_env(config_dir: Path) -> Dict[str, str]:
    """Environment for a server start: the key while the list is still down."""
    if not is_active(config_dir):
        return {}
    from gaia.llm.cloud_keys import recall_key
    from gaia.llm.local_only import cloud_blocked

    if cloud_blocked():
        return {}
    key = recall_key("fireworks")
    check = check_model_list(key) if key else None
    if check is None or check.state is not ModelListState.FAILING:
        log.info("Fireworks' model list no longer needs the known models.")
        remove_entries(config_dir)
        return {}
    log.warning(f"Using known Fireworks models: {check.detail}")
    return {_LEMONADE_KEY_VAR: key}


def _restart(embedded, change) -> None:
    status = embedded.status()
    embedded.stop()
    change(embedded.config_dir)
    embedded.start(
        port=status.port,
        install_if_missing=False,
        owner_pid=status.owner_pid,
        owner_started=status.owner_started,
    )


def use_known_models(embedded) -> None:
    """Restart GAIA's server serving the known models."""
    _restart(embedded, add_entries)


def stop_using_known_models(embedded) -> None:
    """Restart GAIA's server without the known models or the key they start with."""
    _restart(embedded, remove_entries)
