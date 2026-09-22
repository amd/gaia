# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for the frozen request snapshot that ``context`` and ``savings`` share.

Freezing keeps published figures reproducible.  The bug these cover is the
other half of that trade: once ``scan`` had seen new sessions, both reports
kept narrating the original snapshot with no signal, so ``tables.md`` and
``context.md`` from the same run described different corpora (#3933).

Staleness is keyed on the corpus *content*.  ``scan`` rewrites
``traces.jsonl`` whole on every run, so an mtime comparison also fires after a
rescan that found nothing — a gate that stops a correct report is as wrong as
one that lets a stale report through.
"""

import json
import os

import pytest

from gaia.factory.harvest import context, savings
from gaia.factory.harvest.context import (
    CORPUS_KEY,
    SNAPSHOT,
    corpus_digest,
    require_fresh_snapshot,
    snapshot_is_stale,
    snapshot_stamp,
)

CORPUS = '{"session_id": "a"}\n'


def seed(cache, *, snapshot_older, corpus=CORPUS, fingerprint=True):
    """A cache dir holding a snapshot and a traces file, with set mtimes.

    ``fingerprint`` off reproduces a snapshot frozen before the digest key
    existed, which still has to fall back to the mtime comparison.
    """
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "traces.jsonl").write_text(corpus)
    blob = {"sessions": [], "requests": []}
    if fingerprint:
        blob[CORPUS_KEY] = corpus_digest(cache / "traces.jsonl")
    (cache / SNAPSHOT).write_text(json.dumps(blob))
    snap_t, traces_t = (1000, 2000) if snapshot_older else (2000, 1000)
    os.utime(cache / SNAPSHOT, (snap_t, snap_t))
    os.utime(cache / "traces.jsonl", (traces_t, traces_t))
    return cache


def test_no_snapshot_is_not_stale(tmp_path):
    """A first run has nothing to be stale against."""
    assert snapshot_is_stale(tmp_path) is None
    require_fresh_snapshot(tmp_path, refresh=False, frozen_ok=False)


def test_snapshot_matching_the_corpus_passes(tmp_path):
    seed(tmp_path, snapshot_older=False)
    assert snapshot_is_stale(tmp_path) is False
    require_fresh_snapshot(tmp_path, refresh=False, frozen_ok=False)


def test_noop_rescan_does_not_trip_the_gate(tmp_path):
    """A rescan that found nothing rewrites traces.jsonl but changes nothing.

    mtime alone would call this stale and block a report whose figures are
    exactly right.
    """
    seed(tmp_path, snapshot_older=True)
    assert snapshot_is_stale(tmp_path) is False
    require_fresh_snapshot(tmp_path, refresh=False, frozen_ok=False)


def test_grown_corpus_fails_loudly(tmp_path):
    """The regression: a real rescan must not be narrated from the old snapshot."""
    seed(tmp_path, snapshot_older=True, corpus=CORPUS)
    (tmp_path / "traces.jsonl").write_text(CORPUS + '{"session_id": "b"}\n')
    assert snapshot_is_stale(tmp_path) is True
    with pytest.raises(SystemExit) as err:
        require_fresh_snapshot(tmp_path, refresh=False, frozen_ok=False)
    msg = str(err.value)
    # An actionable error names both ways out, not just the problem.
    assert "--refresh" in msg and "--frozen" in msg
    assert "the corpus has changed under it" in msg


def test_session_growing_in_place_is_caught(tmp_path):
    """One session gaining steps keeps the line count but changes the corpus."""
    seed(tmp_path, snapshot_older=False)
    (tmp_path / "traces.jsonl").write_text('{"session_id": "a", "steps": 2}\n')
    assert snapshot_is_stale(tmp_path) is True


def test_pre_fingerprint_snapshot_falls_back_to_mtime(tmp_path):
    """Snapshots frozen before the digest existed must keep working."""
    seed(tmp_path, snapshot_older=True, fingerprint=False)
    assert snapshot_is_stale(tmp_path) is True
    with pytest.raises(SystemExit) as err:
        require_fresh_snapshot(tmp_path, refresh=False, frozen_ok=False)
    # It cannot see the content, so it must not claim the corpus changed.
    msg = str(err.value)
    assert "predates the corpus fingerprint" in msg
    assert "has changed under it" not in msg

    seed(tmp_path, snapshot_older=False, fingerprint=False)
    assert snapshot_is_stale(tmp_path) is False


def test_corrupt_snapshot_names_the_fix(tmp_path):
    seed(tmp_path, snapshot_older=False)
    (tmp_path / SNAPSHOT).write_text("{not json")
    with pytest.raises(SystemExit) as err:
        snapshot_is_stale(tmp_path)
    assert "--refresh" in str(err.value)


@pytest.mark.parametrize("refresh,frozen_ok", [(True, False), (False, True)])
def test_stale_snapshot_passes_when_caller_chose(tmp_path, refresh, frozen_ok):
    seed(tmp_path, snapshot_older=True, fingerprint=False)
    require_fresh_snapshot(tmp_path, refresh=refresh, frozen_ok=frozen_ok)


def test_collect_records_the_corpus_it_measured(tmp_path):
    """Freezing has to record the fingerprint, or nothing can compare against it."""
    (tmp_path / "traces.jsonl").write_text(
        json.dumps({"session_id": "a", "project": "p"}) + "\n"
    )
    context.collect(tmp_path, tmp_path / "projects")
    blob = json.loads((tmp_path / SNAPSHOT).read_text())
    assert blob[CORPUS_KEY] == corpus_digest(tmp_path / "traces.jsonl")
    assert snapshot_is_stale(tmp_path) is False


def test_stamp_names_the_snapshot_in_utc(tmp_path):
    """Output has to say which snapshot it came from, or the gate is pointless.

    Stamped in UTC so a report read next to UTC logs is not off by an offset.
    """
    seed(tmp_path, snapshot_older=True)
    stamp = snapshot_stamp(tmp_path)
    assert SNAPSHOT in stamp and "--refresh" in stamp
    # mtime 1000 == 1970-01-01 00:16:40 UTC, whatever the host's zone is.
    assert "1970-01-01 00:16:40 UTC" in stamp


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
