# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""What the local model server is running, read from Lemonade's own reports.

One reading of ``/health`` and ``/system-info`` answers the questions a user
asks when inference is slow or odd: which Lemonade is this (GAIA's own or the
user's), where is it, and is llama.cpp running on Vulkan, ROCm or the CPU.
Nothing here guesses: a field Lemonade did not report stays ``None``, which
every surface renders as "unknown".
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

#: llama.cpp backends Lemonade's ``/load`` accepts as ``llamacpp_backend``.
LLAMACPP_BACKENDS = ("vulkan", "rocm", "metal", "cpu")

#: ``/system-info`` backend states that a load can use: installed now, or
#: fetched by Lemonade on first use. ``unsupported`` is never offered.
_USABLE_STATES = frozenset({"installed", "installable", "update_required"})

#: Model types that are never the chat model.
_NON_CHAT_TYPES = frozenset(
    {
        "embedding",
        "embed",
        "reranking",
        "rerank",
        "transcription",
        "asr",
        "stt",
        "image",
        "tts",
        "speech",
        "audio",
    }
)


def is_chat_model_entry(entry: Any) -> bool:
    """True for an ``all_models_loaded`` entry that can be the chat model.

    Speech, transcription, embedding, reranking and image models never are,
    whatever loaded last.
    """
    return (
        isinstance(entry, dict)
        and str(entry.get("type") or "").lower() not in _NON_CHAT_TYPES
    )


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def llamacpp_recipe(system_info: Any) -> Dict[str, Any]:
    """``recipes.llamacpp`` from ``/system-info``, or ``{}`` when absent."""
    return _as_dict(_as_dict(_as_dict(system_info).get("recipes")).get("llamacpp"))


def default_llamacpp_backend(system_info: Any) -> Optional[str]:
    """The backend Lemonade picks when a load names none, or None if unreported."""
    backend = llamacpp_recipe(system_info).get("default_backend")
    return backend if isinstance(backend, str) and backend else None


def available_llamacpp_backends(system_info: Any) -> List[Dict[str, str]]:
    """The llama.cpp backends a load may ask for on this machine.

    Only names ``/load`` accepts, and only in a state Lemonade can run: an
    older Lemonade that reports no ``backends`` offers nothing to choose.
    """
    backends = _as_dict(llamacpp_recipe(system_info).get("backends"))
    usable = []
    for name in LLAMACPP_BACKENDS:
        state = _as_dict(backends.get(name)).get("state")
        if state in _USABLE_STATES:
            usable.append({"name": name, "state": state})
    return usable


def _backend_from_launch(launch_command: Any) -> Optional[str]:
    """The backend named by the llama-server path Lemonade launched, if any.

    Lemonade installs each backend in a directory named after it, so a path
    component equal to a backend name identifies it.
    """
    if not isinstance(launch_command, list) or not launch_command:
        return None
    program = str(launch_command[0]).replace("\\", "/").lower()
    parts = set(program.split("/"))
    for name in LLAMACPP_BACKENDS:
        if name in parts:
            return name
    return None


def describe_loaded_model(
    entry: Dict[str, Any], system_info: Any, *, is_cloud: bool = False
) -> Dict[str, Any]:
    """Name, backend and context window of one ``all_models_loaded`` entry.

    ``backend`` is ``remote`` for a cloud model, ``npu`` for an NPU recipe, a
    llama.cpp backend name, or None when Lemonade did not say.
    ``backend_source`` records how it was learned: ``requested`` (the load
    named it), ``launch`` (the llama-server path), or ``lemonade_default``.
    """
    from gaia.llm.lemonade_client import resolve_effective_ctx_size

    name = entry.get("model_name") or entry.get("id") or None
    recipe = entry.get("recipe") if isinstance(entry.get("recipe"), str) else None
    device = entry.get("device") if isinstance(entry.get("device"), str) else None
    options = _as_dict(entry.get("recipe_options"))
    info: Dict[str, Any] = {
        "name": name,
        "recipe": recipe,
        "device": device,
        "backend": None,
        "backend_source": None,
        "ctx_size": None,
    }
    if is_cloud or recipe == "cloud":
        info["backend"] = "remote"
        return info

    ctx = options.get("ctx_size")
    if isinstance(ctx, int) and not isinstance(ctx, bool) and ctx > 0:
        info["ctx_size"] = resolve_effective_ctx_size(
            ctx, entry.get("max_context_window")
        )

    if recipe == "llamacpp":
        requested = options.get("llamacpp_backend")
        launched = _backend_from_launch(entry.get("launch_command"))
        if isinstance(requested, str) and requested:
            info["backend"], info["backend_source"] = requested, "requested"
        elif launched:
            info["backend"], info["backend_source"] = launched, "launch"
        else:
            default = default_llamacpp_backend(system_info)
            if default:
                info["backend"], info["backend_source"] = default, "lemonade_default"
    elif device and "npu" in device.lower():
        info["backend"] = "npu"
    return info


def _chat_entry(health: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The loaded chat model: the root ``model_loaded`` if it is one, else the first."""
    loaded = [
        m for m in health.get("all_models_loaded") or [] if is_chat_model_entry(m)
    ]
    root = str(health.get("model_loaded") or "").lower()
    for m in loaded:
        if root and str(m.get("model_name") or "").lower() == root:
            return m
    return loaded[0] if loaded else None


def _embedder_entry(health: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for m in health.get("all_models_loaded") or []:
        if isinstance(m, dict) and m.get("type") == "embedding":
            return m
    return None


def server_owner(base_url: str, embedded_port: Optional[int]) -> str:
    """``gaia`` for GAIA's own embedded Lemonade, ``user`` for another local one,
    ``remote`` for one on another machine."""
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    if host not in {"localhost", "127.0.0.1", "::1"}:
        return "remote"
    if embedded_port is not None and parsed.port == embedded_port:
        return "gaia"
    return "user"


def describe_model_server(
    *,
    base_url: str,
    owner: str,
    health: Dict[str, Any],
    system_info: Optional[Dict[str, Any]],
    preferred_backend: Optional[str],
    is_cloud_model=lambda _name: False,
) -> Dict[str, Any]:
    """Everything the "Model server" panel and the TUI status line show.

    ``system_info`` is None when ``/system-info`` could not be read: the
    loaded models still show, and nothing is offered to choose.
    """
    parsed = urlparse(base_url)
    chat = _chat_entry(health)
    embed = _embedder_entry(health)
    sysinfo = system_info or {}
    version = health.get("version")
    return {
        "reachable": True,
        "error": None,
        "base_url": base_url,
        "port": parsed.port,
        "owner": owner,
        "version": version if isinstance(version, str) and version else None,
        "chat_model": (
            describe_loaded_model(
                chat, sysinfo, is_cloud=bool(is_cloud_model(chat.get("model_name")))
            )
            if chat
            else None
        ),
        "embedding_model": describe_loaded_model(embed, sysinfo) if embed else None,
        "default_backend": default_llamacpp_backend(sysinfo),
        "available_backends": available_llamacpp_backends(sysinfo),
        "preferred_backend": preferred_backend,
    }
