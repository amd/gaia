# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Which models setup downloads, which the user skipped, and why each matters.

Shared by ``gaia init``, ``gaia init --plan`` (the TUI) and the Agent UI's
setup and Settings screens, so all three describe a model, a skip, a fit and a
disk shortfall in the same words and numbers. Nothing here downloads a model.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from gaia.logger import get_logger

log = get_logger(__name__)

ROLE_CHAT = "chat"
ROLE_VISION = "vision"
ROLE_EMBEDDING = "embedding"

_PURPOSE = {
    ROLE_CHAT: "Answers your messages and runs GAIA's tools",
    ROLE_VISION: "Reads images and screenshots",
    ROLE_EMBEDDING: "Searches your documents and remembers things",
}

#: How long an opted-in "keep trying" waits out a dropped connection.
KEEP_TRYING_S = 120
_RETRY_PAUSE_S = 5.0

#: Lemonade model labels that never make a chat model.
_NON_CHAT_LABELS = frozenset(
    {
        "embeddings",
        "embedding",
        "image",
        "reranker",
        "reranking",
        "audio",
        "tts",
        "stt",
        "transcription",
        "realtime-transcription",
    }
)
_NON_CHAT_RECIPES = frozenset({"whispercpp", "sd-cpp", "kokoro"})

_NETWORK_RE = re.compile(
    r"connection|connect|timed out|timeout|network|reset by peer|"
    r"temporarily unavailable|name resolution|unreachable|remote end closed",
    re.IGNORECASE,
)
_DISK_FULL_RE = re.compile(
    r"no space|disk full|not enough (disk|space)|insufficient disk|enospc",
    re.IGNORECASE,
)


class NeedsAcknowledgement(Exception):
    """A choice that may not work out; the caller confirms or picks again.

    ``detail`` is the JSON body shown to the user: ``code``, ``message``, the
    numbers behind it, and ``acknowledge``, the field that confirms it.
    """

    def __init__(self, detail: Dict[str, Any]):
        super().__init__(detail["message"])
        self.detail = detail


class ModelChoiceError(Exception):
    """A model choice that cannot be made, with what to do instead."""


def ids_match(a: Optional[str], b: Optional[str]) -> bool:
    from gaia.llm.lemonade_client import _model_ids_match

    return _model_ids_match(a, b)


def is_chat_catalog_entry(entry: Dict[str, Any]) -> bool:
    """True for a Lemonade ``/models`` entry that can be the chat model."""
    labels = {str(label).lower() for label in entry.get("labels") or []}
    if str(entry.get("recipe") or "").lower() in _NON_CHAT_RECIPES:
        return False
    if "embed" in str(entry.get("id") or "").lower():
        return False
    # ``chat`` wins: FLM omni models are labeled both ``chat`` and ``audio``.
    return "chat" in labels or not labels & _NON_CHAT_LABELS


def roles_for(model_id: str, chat_model: Optional[str]) -> List[str]:
    """What *model_id* does in this setup: chat, vision, embedding."""
    from gaia.installer.init_command import is_embedding_model_id
    from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

    if is_embedding_model_id(model_id):
        return [ROLE_EMBEDDING]
    roles = []
    if chat_model and ids_match(model_id, chat_model):
        roles.append(ROLE_CHAT)
    # The image tools load Gemma by name, whatever the chat model is.
    if ids_match(model_id, DEFAULT_MODEL_NAME):
        roles.append(ROLE_VISION)
    return roles or [ROLE_CHAT]


def purpose(roles: Iterable[str]) -> str:
    return "; ".join(_PURPOSE[r] for r in roles)


def if_skipped(model_id: str, roles: Iterable[str]) -> Optional[str]:
    """What stops working without *model_id*, in plain words."""
    roles = list(roles)
    lost = []
    if ROLE_CHAT in roles:
        lost.append(
            "GAIA has no local chat model, so pick another one or a cloud provider"
        )
    if ROLE_VISION in roles:
        lost.append("GAIA can't read images or screenshots")
    if ROLE_EMBEDDING in roles:
        lost.append("GAIA can't search your documents or remember things")
    if not lost:
        return None
    return (
        f"Without {model_id}, {' and '.join(lost)}. Download it later in "
        f"Settings or with `gaia init --download-model {model_id}`."
    )


# ── the skip list ──────────────────────────────────────────────────────────


def skipped_models() -> List[str]:
    from gaia.config import GaiaConfig

    return list(GaiaConfig.load().skipped_models)


def is_skipped(model_id: str, skipped: Iterable[str]) -> bool:
    return any(ids_match(model_id, s) for s in skipped)


def split_skipped(model_ids, skipped) -> Tuple[List[str], List[str]]:
    """``(kept, dropped)``: *model_ids* without, and with, the user's skips."""
    kept = [m for m in model_ids if not is_skipped(m, skipped)]
    dropped = [m for m in model_ids if is_skipped(m, skipped)]
    return kept, dropped


def set_skipped(model_id: str, skipped: bool) -> List[str]:
    """Add *model_id* to, or take it off, ``skipped_models``; returns the list."""
    from gaia.config import GaiaConfig

    def mutate(doc: dict) -> None:
        current = [m for m in doc.get("skipped_models") or [] if isinstance(m, str)]
        current = [m for m in current if not ids_match(m, model_id)]
        if skipped:
            current.append(model_id)
        doc["skipped_models"] = current

    return list(GaiaConfig.edit_doc(mutate).skipped_models)


# ── fit and disk ───────────────────────────────────────────────────────────


def to_decimal_gb(model_fit_gb: float) -> float:
    """A model_fit memory figure (Lemonade's GiB) in decimal GB, as the plan reports it.

    A need and the memory it is judged against both come from model_fit in
    the same unit, so scaling both keeps every verdict as it was.
    """
    from gaia.llm.gpu_memory import GB_PER_GIB

    return model_fit_gb * GB_PER_GIB


def memory_reason(need_gb: float, capacity) -> str:
    """Why a model may not fit, in the decimal GB the plan reports."""
    from gaia.llm.model_fit import format_gb

    return (
        f"needs ~{to_decimal_gb(need_gb):.0f} GB of memory; this PC has "
        f"{format_gb(to_decimal_gb(capacity.memory_gb))} GB ({capacity.memory_source})"
    )


def memory_fit(model_id: str, size_gb: Optional[float], capacity) -> Dict[str, Any]:
    """``fits`` / ``need_gb`` / ``capacity_gb`` / ``fit_reason`` for one model.

    Memory only: a model already on disk costs no disk, so disk is judged on
    the whole selection instead (:func:`disk_shortfall`). ``fits`` is None
    when either the size or this PC's memory is unknown. The verdict is
    model_fit's; the numbers are converted to decimal GB.
    """
    from gaia.llm.lemonade_client import find_model_requirement, kv_cache_for
    from gaia.llm.model_fit import MachineCapacity, check_fit, required_memory_gb

    if capacity is None or not size_gb:
        return {
            "fits": None,
            "need_gb": None,
            "capacity_gb": (
                round(to_decimal_gb(capacity.memory_gb), 1) if capacity else None
            ),
            "fit_reason": None,
        }
    mr = find_model_requirement(model_id)
    kv = kv_cache_for(mr, capacity) if mr else 0.0
    memory_only = MachineCapacity(capacity.memory_gb, capacity.memory_source, None)
    verdict = check_fit(float(size_gb), memory_only, kv)
    need = required_memory_gb(float(size_gb), kv)
    return {
        "fits": verdict.fits,
        "need_gb": round(to_decimal_gb(need), 1),
        "capacity_gb": round(to_decimal_gb(capacity.memory_gb), 1),
        "fit_reason": None if verdict.fits else memory_reason(need, capacity),
    }


def _hf_hub_cache() -> Path:
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    if os.environ.get("HF_HOME"):
        return Path(os.environ["HF_HOME"]) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def _free_gb_at(path: Path) -> Optional[float]:
    """Free GB on the drive holding *path*, which need not exist yet."""
    probe = path
    while not probe.exists():
        if probe.parent == probe:
            return None
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free / 1e9
    except OSError as e:
        log.warning("Could not read free space at %s: %s", probe, e)
        return None


def model_store(system_info: Optional[Dict[str, Any]], local: bool) -> Dict[str, Any]:
    """The drive Lemonade writes models to and its free GB.

    Lemonade's ``/system-info`` says where its store is when it runs. GAIA's
    own server, before it runs, writes to this PC's Hugging Face cache, so that
    drive is read here. Anything else (a server on another PC that does not
    say) is unknown: None, never a guess.
    """
    from gaia.llm.model_fit import model_store_free_gb

    if system_info:
        free, path = model_store_free_gb(system_info)
        if free is not None or path:
            return {
                "path": path,
                "free_gb": round(free, 1) if free is not None else None,
                "source": "lemonade",
            }
    if local:
        path = _hf_hub_cache()
        free = _free_gb_at(path)
        return {
            "path": str(path),
            "free_gb": round(free, 1) if free is not None else None,
            "source": "local",
        }
    return {"path": None, "free_gb": None, "source": None}


def disk_shortfall(
    selected: Iterable[Tuple[str, Optional[float]]], disk: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """The low-disk warning for downloading *selected*, or None when it fits.

    *selected* is ``(model_id, gb still to download)``. The warning names how
    much to free and, when skipping the largest pending model alone makes it
    fit, that model.
    """
    pending = [(m, s) for m, s in selected if s]
    need = round(sum(s for _, s in pending), 1)
    free = disk.get("free_gb")
    if free is None or need <= free:
        return None
    short = round(need - free, 1)
    where = disk.get("path") or "the model drive"
    largest = max(pending, key=lambda p: p[1])
    drop = [largest[0]] if largest[1] >= short else []
    advice = (
        f"Free {short:.1f} GB or skip {drop[0]} (saves "
        f"{dict(pending)[drop[0]]:.1f} GB)."
        if drop
        else f"Free {short:.1f} GB, or skip more models."
    )
    return {
        "code": "low_disk",
        "message": f"Needs {need:.1f} GB; only {free:.1f} GB free on {where}. {advice}",
        "need_gb": need,
        "free_gb": free,
        "free_up_gb": short,
        "path": disk.get("path"),
        "drop": drop,
        "acknowledge": "acknowledge_low_disk",
    }


# ── pulls ──────────────────────────────────────────────────────────────────


def error_kind(message: Optional[str]) -> Optional[str]:
    """``disk_full`` / ``network`` / ``lemonade`` for a failed pull's message."""
    if not message:
        return None
    if _DISK_FULL_RE.search(message):
        return "disk_full"
    if _NETWORK_RE.search(message):
        return "network"
    return "lemonade"


def pull_kwargs_for(model_id: str) -> Dict[str, Any]:
    """Registration fields and timeout for one pull (built-ins get none, #1655)."""
    from gaia.llm.lemonade_client import find_model_requirement

    mr = find_model_requirement(model_id)
    kwargs = mr.pull_kwargs() if mr else {}
    # Two hours covers a few GB on any link; a larger model gets ~5 MB/s.
    if mr and mr.size_gb:
        kwargs["timeout"] = max(7200, int(mr.size_gb * 200))
    return kwargs


def pull_model(
    client,
    model_id: str,
    on_progress: Callable[[Dict[str, Any]], None],
    *,
    keep_trying_s: float = 0,
    on_retry: Optional[Callable[[float], None]] = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    **pull_kwargs,
) -> bool:
    """Pull *model_id*; with *keep_trying_s*, wait out a dropped connection.

    The wait is opt-in and bounded: only a network failure is retried, for at
    most *keep_trying_s* after it first fails, and Lemonade resumes from the
    partial file. *on_retry* is told the deadline before each wait.
    """
    last_error: Dict[str, Optional[str]] = {"message": None}

    def track(event: Dict[str, Any]) -> None:
        if event.get("event") == "error":
            last_error["message"] = str(event.get("error") or "")
        on_progress(event)

    deadline = None
    while True:
        last_error["message"] = None
        if client.ensure_model_downloaded(model_id, on_progress=track, **pull_kwargs):
            return True
        if keep_trying_s <= 0 or error_kind(last_error["message"]) != "network":
            return False
        now = clock()
        deadline = deadline if deadline is not None else now + keep_trying_s
        if now >= deadline:
            return False
        if on_retry is not None:
            on_retry(time.time() + (deadline - now))
        sleep(min(_RETRY_PAUSE_S, deadline - now))
