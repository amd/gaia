# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""SessionHistory keeps a running turn's stand-in for the transcript only."""

import json
import os
import sqlite3
from unittest.mock import patch

import psutil

from gaia.agents.base.history import SessionHistory
from gaia.utils import process_owner


class _Agent:
    device = "gpu"


def _rows(path):
    db = sqlite3.connect(str(path))
    try:
        return [json.loads(r[0]) for r in db.execute("SELECT messages FROM turns")]
    finally:
        db.close()


def _other_start(started):
    """A start that is not *started*: on Linux another tick, else long ago."""
    return started.split("/")[0] + "/1" if "/" in started else "1.0"


def _turn(question, answer):
    return [
        {"role": "user", "content": question},
        {"role": "assistant", "content": answer},
    ]


def test_record_replaces_the_stand_in(tmp_path):
    path = tmp_path / "s.sqlite3"
    history = SessionHistory(path)
    try:
        history.begin(_turn("q", "interrupted"))
        assert _rows(path) == [_turn("q", "interrupted")]
        history.record(_turn("q", "a"))
        history.record(_turn("q2", "a2"))
        assert _rows(path) == [_turn("q", "a"), _turn("q2", "a2")]
    finally:
        history.close()


def test_discard_drops_only_the_stand_in(tmp_path):
    path = tmp_path / "s.sqlite3"
    history = SessionHistory(path)
    try:
        history.record(_turn("q1", "a1"))
        history.begin(_turn("q2", "interrupted"))
        history.discard_pending()
        history.discard_pending()  # nothing pending: a no-op
        assert _rows(path) == [_turn("q1", "a1")]
    finally:
        history.close()


def test_the_model_never_sees_the_stand_in_but_a_resumed_session_does(tmp_path):
    path = tmp_path / "s.sqlite3"
    history = SessionHistory(path)
    try:
        history.record(_turn("q1", "a1"))
        history.begin(_turn("q2", "interrupted"))
        history.prepare(_Agent(), "q2")
        assert [m["content"] for m in history] == ["q1", "a1"]
    finally:
        history.close()

    # The process that wrote it was killed; a new one resumes the file.
    resumed = SessionHistory(path)
    try:
        with patch.object(process_owner, "_TOKEN", "the-resumed-session"):
            resumed.prepare(_Agent(), "q3")
        assert [m["content"] for m in resumed] == ["q1", "a1", "q2", "interrupted"]
    finally:
        resumed.close()


def test_clear_forgets_the_stand_in(tmp_path):
    path = tmp_path / "s.sqlite3"
    history = SessionHistory(path)
    try:
        history.begin(_turn("q", "interrupted"))
        history.clear()
        history.record(_turn("q2", "a2"))
        assert _rows(path) == [_turn("q2", "a2")]
    finally:
        history.close()


def test_a_kept_stand_in_is_an_ordinary_turn_from_then_on(tmp_path):
    path = tmp_path / "s.sqlite3"
    history = SessionHistory(path)
    try:
        history.begin(_turn("q1", "interrupted"))
        history.keep_pending()
        history.record(_turn("q2", "a2"))
        history.prepare(_Agent(), "q3")
        assert [m["content"] for m in history] == ["q1", "interrupted", "q2", "a2"]
    finally:
        history.close()


def test_a_turn_finishing_after_another_process_cleared_it_is_kept(tmp_path):
    """The stand-in was erased by a clear from another process; the finished
    turn is still saved, as it was before stand-ins existed."""
    path = tmp_path / "s.sqlite3"
    history = SessionHistory(path)
    other = SessionHistory(path)
    try:
        history.begin(_turn("q", "interrupted"))
        other.clear()
        history.record(_turn("q", "a"))
        assert _rows(path) == [_turn("q", "a")]
    finally:
        history.close()
        other.close()


def test_another_live_process_s_running_turn_is_not_shown_as_interrupted(tmp_path):
    """A process sharing the file sees the other's running turn only once that
    process has gone."""
    path = tmp_path / "s.sqlite3"
    running = SessionHistory(path)
    reader = SessionHistory(path)
    try:
        running.record(_turn("q1", "a1"))
        running.begin(_turn("q2", "interrupted"))
        parent = psutil.Process(os.getppid())
        started = process_owner._started(parent.pid)
        # The stand-in belongs to another live process (this test's parent).
        running._db.execute(
            "UPDATE turns SET owner = ? WHERE owner IS NOT NULL",
            (f"{parent.pid}:{started}:other",),
        )
        running._db.commit()
        reader.prepare(_Agent(), "q3")
        assert [m["content"] for m in reader] == ["q1", "a1"]

        # That process has gone (its pid now names a newer process).
        running._db.execute(
            "UPDATE turns SET owner = ? WHERE owner IS NOT NULL",
            (f"{parent.pid}:{_other_start(started)}:other",),
        )
        running._db.commit()
        reader.prepare(_Agent(), "q3")
        assert [m["content"] for m in reader] == ["q1", "a1", "q2", "interrupted"]
    finally:
        running.close()
        reader.close()
