# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Run configuration for a benchmark, from CLI flags or the environment.

Nothing here names a machine path: every location has a flag, an environment
variable and a default that works on any host. Required values that are
missing fail at startup, naming the flag and the variable.
"""

from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

HARNESSES = ("gaia", "claude-code")
METERS = ("fireworks",)

ENV_WORK_ROOT = "GAIA_BENCH_WORK_ROOT"
ENV_RESULTS_DIR = "GAIA_BENCH_RESULTS_DIR"
ENV_GATEWAY_URL = "GAIA_BENCH_GATEWAY_URL"
ENV_THEROCK_URL = "GAIA_BENCH_THEROCK_URL"
ENV_RUN_TIMEOUT = "GAIA_BENCH_RUN_TIMEOUT"
ENV_FIREWORKS_ACCOUNT = "FIREWORKS_ACCOUNT_ID"

DEFAULT_THEROCK_URL = "https://github.com/ROCm/TheRock"
#: One wall-clock cap for every harness. TheRock tasks need the headroom; a cap
#: that binds on a slow model measures the cap, not the model.
DEFAULT_RUN_TIMEOUT_S = 1800
#: Fireworks' billing meter trails the calls it counts by about 90 s.
DEFAULT_METER_LAG_S = 150


class BenchConfigError(ValueError):
    """A benchmark setting is missing or invalid; the message names the fix."""


@dataclass(frozen=True)
class BenchConfig:
    """Everything a run needs beyond the suite and the model."""

    harness: str = "gaia"
    run_timeout_s: int = DEFAULT_RUN_TIMEOUT_S
    work_root: Path = Path(tempfile.gettempdir()) / "gaia-bench"
    #: A gateway someone already runs; ``None`` starts one for the run.
    gateway_url: Optional[str] = None
    therock_url: str = DEFAULT_THEROCK_URL
    fence: bool = False
    #: GAIA with no path boundary, the reach Claude Code has with its
    #: permissions skipped. Recorded in the scorecard either way.
    full_access: bool = False
    repeats: int = 1
    meter: Optional[str] = None
    fireworks_account: Optional[str] = None
    meter_lag_s: int = DEFAULT_METER_LAG_S


def _int(value: object, name: str, minimum: int) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise BenchConfigError(f"{name} must be an integer, got {value!r}") from exc
    if number < minimum:
        raise BenchConfigError(f"{name} must be at least {minimum}, got {number}")
    return number


def resolve(
    *,
    harness: str = "gaia",
    run_timeout: Optional[int] = None,
    work_root: Optional[str] = None,
    gateway_url: Optional[str] = None,
    therock_url: Optional[str] = None,
    fence: bool = False,
    full_access: bool = False,
    repeats: int = 1,
    meter: Optional[str] = None,
    fireworks_account: Optional[str] = None,
    meter_lag: Optional[int] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> BenchConfig:
    """Flags win over the environment; the environment wins over defaults."""
    env = os.environ if environ is None else environ
    if harness not in HARNESSES:
        raise BenchConfigError(
            f"Unknown harness {harness!r}. Choose one of: {', '.join(HARNESSES)}."
        )
    if meter is not None and meter not in METERS:
        raise BenchConfigError(
            f"Unknown meter {meter!r}. Choose one of: {', '.join(METERS)}."
        )
    account = fireworks_account or env.get(ENV_FIREWORKS_ACCOUNT) or None
    if meter == "fireworks" and not account:
        raise BenchConfigError(
            "Metering with Fireworks needs the account id. Pass "
            f"--fireworks-account or set {ENV_FIREWORKS_ACCOUNT} (the id in "
            "https://app.fireworks.ai/account/profile)."
        )
    if fence and sys.platform != "darwin":
        raise BenchConfigError(
            "--fence uses macOS sandbox-exec and is not available on "
            f"{sys.platform}. On Linux, run without --fence: TheRock reference "
            "diffs are fetched only by `judge`, after the agent has exited, but "
            "eval/tasks/tasks.json stays readable to an agent that goes looking."
        )
    timeout = run_timeout if run_timeout is not None else env.get(ENV_RUN_TIMEOUT)
    return BenchConfig(
        harness=harness,
        run_timeout_s=_int(
            DEFAULT_RUN_TIMEOUT_S if timeout in (None, "") else timeout,
            f"--run-timeout / {ENV_RUN_TIMEOUT}",
            1,
        ),
        work_root=Path(
            work_root
            or env.get(ENV_WORK_ROOT)
            or Path(tempfile.gettempdir()) / "gaia-bench"
        ),
        gateway_url=(gateway_url or env.get(ENV_GATEWAY_URL) or None),
        therock_url=therock_url or env.get(ENV_THEROCK_URL) or DEFAULT_THEROCK_URL,
        fence=fence,
        full_access=full_access,
        repeats=_int(repeats, "--repeats", 1),
        meter=meter,
        fireworks_account=account,
        meter_lag_s=_int(
            DEFAULT_METER_LAG_S if meter_lag is None else meter_lag, "--meter-lag", 0
        ),
    )


def results_root(environ: Optional[Mapping[str, str]] = None) -> Path:
    """Where a run writes when ``--out`` is not given."""
    env = os.environ if environ is None else environ
    return Path(env.get(ENV_RESULTS_DIR) or "eval/results")
