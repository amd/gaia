# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The harness table: one list of the programs that can answer as GAIA."""

import dataclasses
import json
from pathlib import Path

import pytest

from gaia.config import GaiaConfig
from gaia.harnesses import (
    DEFAULT_HARNESS,
    HARNESSES,
    HarnessUnavailableError,
    harness,
    require_runnable,
)

_TUI_TABLE = (
    Path(__file__).resolve().parents[2]
    / "tui"
    / "internal"
    / "catalog"
    / "harnesses.json"
)


def test_the_tui_reads_the_same_harnesses():
    rows = json.loads(_TUI_TABLE.read_text(encoding="utf-8"))["harnesses"]
    assert rows == [dataclasses.asdict(h) for h in HARNESSES]


def test_the_python_agent_is_the_recommended_default():
    assert DEFAULT_HARNESS == "gaiapy"
    assert harness("gaiapy").status == "recommended"
    assert [h.name for h in HARNESSES if h.status == "recommended"] == ["gaiapy"]


def test_gaiacode_is_known_but_not_yet_runnable():
    code = harness("gaiacode")
    assert code is not None and code.native and code.status == "beta"
    assert not code.runnable


def test_config_set_offers_only_runnable_harnesses():
    assert GaiaConfig.FLAGSHIP_AGENT_CHOICES == tuple(
        h.name for h in HARNESSES if h.runnable
    )


@pytest.mark.parametrize("name", ["gaiapy", "gaiacpp"])
def test_a_runnable_harness_passes(name):
    assert require_runnable(name) == name


@pytest.mark.parametrize("name", ["gaiacode", "rust"])
def test_a_harness_that_cannot_run_names_the_fix(name):
    with pytest.raises(HarnessUnavailableError) as e:
        require_runnable(name)
    assert name in str(e.value) and "Settings" in str(e.value)


def test_the_eval_override_runs_only_the_harness_it_names(monkeypatch):
    monkeypatch.setenv("GAIA_UNPROVEN_HARNESS", "gaiacode")
    assert require_runnable("gaiacode") == "gaiacode"
    # It never makes an unknown name runnable.
    with pytest.raises(HarnessUnavailableError):
        require_runnable("rust")


def test_without_the_override_a_saved_gaiacode_still_refuses(monkeypatch):
    monkeypatch.delenv("GAIA_UNPROVEN_HARNESS", raising=False)
    with pytest.raises(HarnessUnavailableError):
        require_runnable("gaiacode")
