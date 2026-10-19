# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""SessionHistory keeps a running turn's stand-in for the transcript only."""

import json
import sqlite3

from gaia.agents.base.history import SessionHistory


class _Agent:
    device = "gpu"


def _rows(path):
    db = sqlite3.connect(str(path))
    try:
        return [json.loads(r[0]) for r in db.execute("SELECT messages FROM turns")]
    finally:
        db.close()


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

    resumed = SessionHistory(path)
    try:
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
