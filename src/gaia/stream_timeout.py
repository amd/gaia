# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The no-progress turn limit, ``GAIA_STREAM_TIMEOUT``.

One definition for every surface that stops a turn which has gone quiet: the
Agent UI and the messaging bridges. Stdlib only, so any of them can import it
without pulling in the others.
"""

import os

STREAM_TIMEOUT_ENV = "GAIA_STREAM_TIMEOUT"
# Longer than one model call may stay silent (an hour of slow local prefill),
# so a quiet model is always ended by the LLM layer, not by this limit.
DEFAULT_STREAM_TIMEOUT_SECONDS = 4500
MAX_STREAM_TIMEOUT_SECONDS = 86400


def stream_timeout_from_env() -> int:
    """Resolve the turn's no-progress limit in seconds from ``GAIA_STREAM_TIMEOUT``.

    A turn is stopped only after this long without progress; a turn that keeps
    making progress has no time limit. Unset or empty returns the 4500 s
    default. Any other value must be a whole number of seconds from 1 to 86400.

    Raises:
        ValueError: the variable is set to a non-integer or out-of-range value.
    """
    raw = os.environ.get(STREAM_TIMEOUT_ENV, "").strip()
    if not raw:
        return DEFAULT_STREAM_TIMEOUT_SECONDS
    remedy = (
        f"Set it to a whole number of seconds from 1 to "
        f"{MAX_STREAM_TIMEOUT_SECONDS} (e.g. {STREAM_TIMEOUT_ENV}=1800), or "
        f"unset it for the {DEFAULT_STREAM_TIMEOUT_SECONDS}s default. See "
        "https://amd-gaia.ai/docs/guides/agent-ui#long-responses"
    )
    try:
        value = int(raw)
    except ValueError as e:
        raise ValueError(
            f"{STREAM_TIMEOUT_ENV}={raw!r} is not a whole number of seconds. "
            f"{remedy}"
        ) from e
    if not 1 <= value <= MAX_STREAM_TIMEOUT_SECONDS:
        raise ValueError(f"{STREAM_TIMEOUT_ENV}={value} is out of range. {remedy}")
    return value
