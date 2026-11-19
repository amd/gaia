# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Why ``sounddevice`` can't be used here, and the fix that works for this install."""

from __future__ import annotations

from typing import Optional

from gaia.agents.install_hints import missing_extra_hint


def sounddevice_missing_message(purpose: str, error: Optional[BaseException]) -> str:
    """The reason *purpose* (e.g. "audio recording") has no sounddevice.

    *error* is what ``import sounddevice`` raised. An ``OSError`` means the
    module is installed but its PortAudio library is not, which no Python
    install fixes.
    """
    if isinstance(error, OSError):
        return (
            f"{purpose} needs the PortAudio system library, which was not found "
            f"({error}). Install your system's PortAudio package (libportaudio2 "
            "on Ubuntu and Debian, portaudio on Fedora), then run the command again."
        )
    return f"sounddevice is required for {purpose}. {missing_extra_hint('talk')}"
