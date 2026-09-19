# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for the frozen request snapshot that ``context`` and ``savings`` share.

Freezing keeps published figures reproducible.  The bug these cover is the
other half of that trade: once ``scan`` had seen new sessions, both reports
kept narrating the original snapshot with no signal, so ``tables.md`` and
``context.md`` from the same run described different corpora (#3933).
"""

import json
import os

import pytest

from gaia.factory.harvest import context, savings
from gaia.factory.harvest.context import (
    SNAPSHOT,
    require_fresh_snapshot,
    snapshot_age,
    snapshot_stamp,
)


def seed(cache, *, snapshot_older):
    """A cache dir holding a snapshot and a traces file, with set mtimes."""
    cache.mkdir(parents=True, exist_ok=True)
    (cache / SNAPSHOT).write_text(json.dumps({"sessions": [], "requests": []}))
    (cache / "traces.jsonl").write_text("{}\n")
    snap_t, traces_t = (1000, 2000) if snapshot_older else (2000, 1000)
    os.utime(cache / SNAPSHOT, (snap_t, snap_t))
    os.utime(cache / "traces.jsonl", (traces_t, traces_t))
    return cache


def test_no_snapshot_is_not_stale(tmp_path):
    """A first run has nothing to be stale against."""
    assert snapshot_age(tmp_path) is None
    require_fresh_snapshot(tmp_path, refresh=False, frozen_ok=False)


def test_snapshot_newer_than_corpus_passes(tmp_path):
    seed(tmp_path, snapshot_older=False)
    assert snapshot_age(tmp_path) is False
    require_fresh_snapshot(tmp_path, refresh=False, frozen_ok=False)


def test_stale_snapshot_fails_loudly(tmp_path):
    """The regression: a rescan must not be narrated from the old snapshot."""
    seed(tmp_path, snapshot_older=True)
    assert snapshot_age(tmp_path) is True
    with pytest.raises(SystemExit) as err:
        require_fresh_snapshot(tmp_path, refresh=False, frozen_ok=False)
    msg = str(err.value)
    # An actionable error names both ways out, not just the problem.
    assert "--refresh" in msg and "--frozen" in msg


@pytest.mark.parametrize("refresh,frozen_ok", [(True, False), (False, True)])
def test_stale_snapshot_passes_when_caller_chose(tmp_path, refresh, frozen_ok):
    seed(tmp_path, snapshot_older=True)
    require_fresh_snapshot(tmp_path, refresh=refresh, frozen_ok=frozen_ok)


def test_stamp_names_the_snapshot(tmp_path):
    """Output has to say which snapshot it came from, or the gate is pointless."""
    seed(tmp_path, snapshot_older=True)
    stamp = snapshot_stamp(tmp_path)
    assert SNAPSHOT in stamp and "--refresh" in stamp


def test_stamp_is_empty_without_a_snapshot(tmp_path):
    assert snapshot_stamp(tmp_path) == ""


@pytest.mark.parametrize("module", [context, savings])
def test_refresh_and_frozen_are_mutually_exclusive(
    module, tmp_path, monkeypatch, capsys
):
    """Contradictory intents are rejected, not silently resolved to --refresh."""
    monkeypatch.setattr(
        "sys.argv",
        [module.__name__, "--cache", str(tmp_path), "--refresh", "--frozen"],
    )
    with pytest.raises(SystemExit) as err:
        module.main()
    assert err.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err
