# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The programs ("harnesses") that can answer as the GAIA agent.

One table, read by the config loader, the daemon and the Agent UI backend.
``tui/internal/catalog/harnesses.json`` carries the same rows for the terminal
UI; ``tests/unit/test_harnesses.py`` fails if they drift.

``runnable`` is False for a harness this version knows the name of but cannot
start yet. Its name still loads from ``config.json``, so a config written by a
newer GAIA never stops this one from starting; choosing or launching it is an
error that says why.

``GAIA_UNPROVEN_HARNESS`` names one known harness that may run anyway. Only the
eval lanes and tests set it, to measure a harness before it is offered; no
setting writes it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Tuple

DEFAULT_HARNESS = "gaiapy"

UNPROVEN_HARNESS_ENV = "GAIA_UNPROVEN_HARNESS"


@dataclass(frozen=True)
class Harness:
    name: str
    #: A program of its own (C++), started beside GAIA rather than imported.
    native: bool
    #: What the apps call it.
    label: str
    #: "recommended" or "beta".
    status: str
    runnable: bool


HARNESSES: Tuple[Harness, ...] = (
    Harness(
        "gaiapy",
        native=False,
        label="Full-featured",
        status="recommended",
        runnable=True,
    ),
    Harness(
        "gaiacpp", native=True, label="Fast and light", status="beta", runnable=True
    ),
    Harness("gaiacode", native=True, label="Coding", status="beta", runnable=False),
)


def harness(name: Optional[str]) -> Optional[Harness]:
    """The harness called ``name``, or None for a name this version doesn't know."""
    for h in HARNESSES:
        if h.name == name:
            return h
    return None


class HarnessUnavailableError(Exception):
    """The saved harness is one this version of GAIA can't run."""


def unavailable_reason(name: str) -> str:
    """Why ``name`` can't be the GAIA agent here, worded for the user."""
    known = harness(name)
    if known is None:
        return (
            f"The GAIA agent is set to run {name!r}, which this version of GAIA "
            "doesn't know. A newer GAIA may have saved it. Choose another agent "
            "program in Settings, or update GAIA."
        )
    return (
        f"The GAIA agent is set to run {name}, which isn't available in this "
        "version of GAIA yet. Choose another agent program in Settings."
    )


def require_runnable(name: str) -> str:
    """``name`` when this version can run it; else raise naming the fix."""
    known = harness(name)
    if known is not None and not known.runnable:
        if os.environ.get(UNPROVEN_HARNESS_ENV, "").strip() == name:
            return name
    if known is None or not known.runnable:
        raise HarnessUnavailableError(unavailable_reason(name))
    return name
