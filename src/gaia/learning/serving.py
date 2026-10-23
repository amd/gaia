# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Load the active adapter with its base model.

Lemonade passes ``llamacpp_args`` to llama-server, which applies a LoRA with
``--lora <path>``. GAIA's chat load path appends that flag whenever an adapter
is active for the model it loads, so the adapter survives Lemonade's idle
unload and every later reload.
"""

import os
from pathlib import Path
from typing import Optional

from gaia.learning.errors import LearningError
from gaia.learning.registry import AdapterRegistry
from gaia.logger import get_logger

log = get_logger(__name__)


def _arg_path(path: Path) -> str:
    """*path* as one whitespace-free token: Lemonade splits ``llamacpp_args``
    on spaces."""
    text = str(path)
    if not any(c.isspace() for c in text):
        return text
    if os.name == "nt":
        import ctypes

        buf = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.kernel32.GetShortPathNameW(text, buf, len(buf)):
            short = buf.value
            if short and not any(c.isspace() for c in short):
                return short
    raise LearningError(
        f"The adapter path {text!r} contains a space, which llama-server's "
        "argument list can't carry. Set GAIA_HOME to a folder without spaces, "
        "or deactivate the adapter with `gaia learn deactivate`."
    )


def lora_args(model_name: str, registry: Optional[AdapterRegistry] = None) -> str:
    """``--lora <gguf>`` for *model_name*'s active adapter, or ``""``."""
    registry = registry or AdapterRegistry()
    adapter_id = registry.active_id(model_name)
    if not adapter_id:
        return ""
    gguf = registry.gguf_path(adapter_id)
    if not gguf.exists():
        raise LearningError(
            f"The active adapter {adapter_id} for {model_name} is missing its "
            f"file {gguf}. Run `gaia learn deactivate` to serve the base model, "
            "or activate another adapter."
        )
    return f"--lora {_arg_path(gguf)}"


def chat_llamacpp_args(model_name: str, base_args: str) -> str:
    extra = lora_args(model_name)
    return f"{base_args} {extra}" if extra else base_args


def reload_with_adapter(
    client,
    model_name: str,
    *,
    adapter_gguf: Optional[Path] = None,
    base_only: bool = False,
) -> None:
    """Reload *model_name* now so the change in adapter takes effect.

    By default it loads with the active adapter. *adapter_gguf* loads that file
    instead (the eval gate trying a candidate); *base_only* loads no adapter.
    """
    from gaia.llm.lemonade_client import CHAT_LLAMACPP_ARGS, resolve_ctx_size

    if adapter_gguf is not None:
        args = f"{CHAT_LLAMACPP_ARGS} --lora {_arg_path(adapter_gguf)}"
    elif base_only:
        args = CHAT_LLAMACPP_ARGS
    else:
        args = chat_llamacpp_args(model_name, CHAT_LLAMACPP_ARGS)
    client.unload_model(model_name, ignore_if_not_loaded=True)
    client.load_model(
        model_name,
        auto_download=False,
        prompt=False,
        ctx_size=resolve_ctx_size(model=model_name, base_url=client.base_url),
        llamacpp_args=args,
    )
    log.info("Reloaded %s with llamacpp_args=%r", model_name, args)
