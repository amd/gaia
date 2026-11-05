# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Phone errors with stable codes the apps translate."""

from __future__ import annotations

ERROR_CODES = frozenset(
    {
        "bad_number",
        "not_in_phone_book",
        "not_configured",
        "phone_off",
        "bad_credentials",
        "provider_error",
        "provider_unreachable",
        "trial_unverified",
        "verification_failed",
        "verification_expired",
        "rate_limited",
        "limit_reached",
        "last_number",
        "duplicate_number",
        "not_found",
        "bad_request",
    }
)


class PhoneError(RuntimeError):
    def __init__(self, code: str, message: str, *, detail: str = ""):
        if code not in ERROR_CODES:
            raise ValueError(f"unknown phone error code {code!r}")
        super().__init__(message)
        self.code = code
        self.detail = detail

    def to_dict(self) -> dict:
        out = {"code": self.code, "message": str(self)}
        if self.detail:
            out["detail"] = self.detail
        return out
