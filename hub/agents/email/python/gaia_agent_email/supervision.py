# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Daemon-supervision detection for the email sidecar (V2-15, #2156).

When the GAIA daemon spawns this sidecar it sets
``GAIA_DAEMON_SUPERVISED=1`` in the environment. In that mode the daemon owns
the clock: it drives the briefing and one-shot jobs from its single reconciled
scheduler, so the sidecar's OWN embedded schedulers (``BriefingScheduler`` #1918,
``EmailJobScheduler`` #1919) must NOT also run — two clocks over one store is the
double-run this reconciliation exists to prevent.

A sidecar started any other way — a bare integrator, a standalone
``gaia-agent-email serve``, an embedded ``CustodyProvider`` deployment — never
sees the var and keeps its embedded clocks live. The check is a supervision
*context* test, deliberately NOT a deletion, so standalone scheduling behavior
(and its test suite) is untouched.

The env-var NAME is owned by core (``gaia.daemon.constants``) so the daemon that
sets it and the sidecar that reads it can never drift. Importing a core constant
from a hub package is allowed; core never imports a hub wheel.
"""

from __future__ import annotations

import os
from typing import Mapping, Optional

from gaia.daemon.constants import (
    DAEMON_SUPERVISION_ENABLED_VALUE,
    DAEMON_SUPERVISION_ENV_VAR,
)


def is_daemon_supervised(environ: Optional[Mapping[str, str]] = None) -> bool:
    """True when the daemon is driving this sidecar's clock.

    Only the exact enabled value counts — any other value (including a stray
    empty string) means "not supervised", so a misconfigured env never
    silently disables the embedded clocks a standalone run depends on.
    """
    env = os.environ if environ is None else environ
    return env.get(DAEMON_SUPERVISION_ENV_VAR) == DAEMON_SUPERVISION_ENABLED_VALUE


__all__ = ["is_daemon_supervised"]
