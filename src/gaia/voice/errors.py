# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Typed voice errors with stable codes the apps translate."""

from __future__ import annotations

#: Every code a voice session can report. Apps key their translated message
#: and recovery action on these, so they never change meaning once shipped.
ERROR_CODES = frozenset(
    {
        "lemonade_unreachable",
        "model_missing",
        "mic_denied",
        "mic_missing",
        "mic_busy",
        "remote_lemonade",
        "cloud_model",
        "tts_failed",
        "asr_failed",
        "session_lost",
        "bad_request",
    }
)


class VoiceError(RuntimeError):
    """A voice failure with a stable ``code`` and an actionable message."""

    def __init__(self, code: str, message: str, *, detail: str = ""):
        if code not in ERROR_CODES:
            raise ValueError(f"unknown voice error code {code!r}")
        super().__init__(message)
        self.code = code
        self.detail = detail

    def to_event(self) -> dict:
        event = {"type": "error", "code": self.code, "message": str(self)}
        if self.detail:
            event["detail"] = self.detail
        return event
