# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Where voice reaches Lemonade, and the check that it stays on this machine."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from gaia.voice.errors import VoiceError

#: Streaming recognizer for English, and Whisper for everything else.
ENGLISH_ASR_MODEL = "Moonshine-Tiny-Streaming"
MULTILINGUAL_ASR_MODEL = "Whisper-Base"
TTS_MODEL = "kokoro-v1"


@dataclass(frozen=True)
class LemonadeTarget:
    base_url: str  # e.g. http://127.0.0.1:13305/api/v1
    api_key: Optional[str]

    @property
    def headers(self) -> Dict[str, str]:
        from gaia.llm.lemonade_client import lemonade_auth_headers

        return lemonade_auth_headers(self.api_key)

    @property
    def host(self) -> str:
        return urlparse(self.base_url).hostname or ""

    @property
    def origin(self) -> str:
        parsed = urlparse(self.base_url)
        return f"{parsed.scheme}://{parsed.netloc}"


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def resolve_target(
    base_url: Optional[str] = None, api_key: Optional[str] = None
) -> LemonadeTarget:
    """Resolve Lemonade the way every GAIA client does, refusing a remote one."""
    from gaia.llm.lemonade_client import (
        resolve_lemonade_api_key,
        resolve_lemonade_base_url,
    )

    url = resolve_lemonade_base_url(base_url)
    target = LemonadeTarget(url, resolve_lemonade_api_key(api_key, base_url=url))
    if not _is_loopback(target.host):
        raise VoiceError(
            "remote_lemonade",
            f"Voice only runs on a Lemonade Server on this machine, and "
            f"LEMONADE_BASE_URL points at {target.host}. Unset it, or point it "
            "at a local server, to use voice.",
        )
    return target


def pick_asr_model(language: str, override: str = "auto") -> str:
    if override and override != "auto":
        return override
    return ENGLISH_ASR_MODEL if language in ("en", "") else MULTILINGUAL_ASR_MODEL


def model_entry(catalog: List[Dict[str, Any]], model: str) -> Optional[Dict[str, Any]]:
    return next((m for m in catalog if m.get("id") == model), None)


def require_downloaded(catalog: List[Dict[str, Any]], model: str, label: str) -> None:
    """Raise ``model_missing`` unless *model* is in the catalog and downloaded."""
    entry = model_entry(catalog, model)
    if entry is None:
        raise VoiceError(
            "model_missing",
            f"Lemonade has no {label} model '{model}'. Update Lemonade Server to "
            "get it.",
            detail=model,
        )
    if label not in (entry.get("labels") or []) and not (
        label == "transcription"
        and "realtime-transcription" in (entry.get("labels") or [])
    ):
        raise VoiceError(
            "model_missing",
            f"Lemonade model '{model}' is not a {label} model.",
            detail=model,
        )
    if entry.get("downloaded") is not True:
        size = entry.get("size")
        size_hint = f" ({float(size):.2f} GB)" if isinstance(size, (int, float)) else ""
        raise VoiceError(
            "model_missing",
            f"Voice needs the speech model {model}{size_hint}. Download it to "
            "continue.",
            detail=model,
        )
