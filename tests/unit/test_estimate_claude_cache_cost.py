# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``util/estimate_claude_cache_cost.py`` prices recorded runs correctly."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).parents[2] / "util" / "estimate_claude_cache_cost.py"
_spec = importlib.util.spec_from_file_location("estimate_claude_cache_cost", _PATH)
est = importlib.util.module_from_spec(_spec)
# Dataclasses resolve their annotations through sys.modules.
sys.modules[_spec.name] = est
_spec.loader.exec_module(est)

# Round prices so every expected figure is exact.
PRICES = est.Prices(uncached=4, cache_read=1, cache_write=10, output=100)


def _stats(step, read, write, out, uncached=0):
    return {
        "role": "system",
        "content": {
            "type": "stats",
            "step": step,
            "performance_stats": {
                "cache_read_input_tokens": read,
                "cache_creation_input_tokens": write,
                "uncached_input_tokens": uncached,
                "completion_tokens": out,
            },
        },
    }


def _record(*messages):
    return {"type": "result", "conversation": list(messages)}


def _write(tmp_path, record, name="transcript.json"):
    path = tmp_path / name
    path.write_text("log line\n" + json.dumps(record) + "\n", encoding="utf-8")
    return path


def test_a_stable_prefix_has_no_miss_rewrites():
    calls = [
        est.Call(uncached=0, cache_read=0, cache_write=1000, output=10),
        est.Call(uncached=0, cache_read=1000, cache_write=200, output=10),
        est.Call(uncached=0, cache_read=1200, cache_write=300, output=10),
    ]

    parts = est.decompose(calls, PRICES)

    assert parts["first"] == pytest.approx(1000 * 10 / 1e6)
    assert parts["prefix_reads"] == pytest.approx(2000 / 1e6)
    assert parts["history_reads"] == pytest.approx(200 / 1e6)
    assert parts["growth_writes"] == pytest.approx(500 * 10 / 1e6)
    assert parts["miss_rewrites"] == 0
    assert parts["miss_tokens"] == 0
    assert parts["output"] == pytest.approx(30 * 100 / 1e6)


def test_a_changed_prefix_counts_the_rewrite():
    calls = [
        est.Call(uncached=0, cache_read=0, cache_write=1000, output=0),
        est.Call(uncached=0, cache_read=1000, cache_write=500, output=0),
        # 1500 -> 1600: only 100 tokens are new, yet 600 were written.
        est.Call(uncached=0, cache_read=1000, cache_write=600, output=0),
    ]

    parts = est.decompose(calls, PRICES)

    assert parts["miss_tokens"] == 500
    assert parts["miss_rewrites"] == pytest.approx(500 * 10 / 1e6)
    assert parts["growth_writes"] == pytest.approx(600 * 10 / 1e6)


def test_a_shrinking_prompt_is_all_rewrite():
    calls = [
        est.Call(uncached=0, cache_read=0, cache_write=1000, output=0),
        est.Call(uncached=0, cache_read=600, cache_write=300, output=0),
    ]

    parts = est.decompose(calls, PRICES)

    assert parts["miss_tokens"] == 300
    assert parts["growth_writes"] == 0


def test_summary_prices_the_stable_prefix_upper_bound(tmp_path):
    record = _record(
        _stats(1, 0, 1000, 5),
        _stats(2, 1000, 500, 5),
        _stats(3, 1000, 600, 5),
    )

    report = est.summarize([_write(tmp_path, record)], PRICES)

    assert report["runs"] == 1 and report["calls"] == 3
    assert report["stable_prefix_savings"] == pytest.approx(500 * (10 - 1) / 1e6)
    assert report["total"] == pytest.approx(sum(report["components"].values()))


def test_readable_results_save_tokens_on_every_later_read():
    escaped = json.dumps({"status": "success", "content": 'x = "a\\b"\n' * 50})
    record = _record(
        {"role": "assistant", "content": {"tool_calls": []}},
        {"role": "tool", "name": "read_file", "content": json.loads(escaped)},
        _stats(1, 0, 1000, 5),
        _stats(2, 1000, 400, 5),
        _stats(3, 1400, 50, 5),
        _stats(4, 1450, 50, 5),
    )

    saved = est.readable_savings(record, PRICES)

    assert saved["tokens"] > 0
    # Written once by call 2, then read by calls 3 and 4.
    assert saved["dollars"] == pytest.approx(saved["tokens"] * (10 + 2 * 1) / 1e6)


def test_verbatim_and_unsent_results_save_nothing():
    page = {"content": "line\n" * 40}
    record = _record(
        {"role": "tool", "name": "read_tool_output", "content": page},
        _stats(1, 0, 1000, 5),
        _stats(2, 1000, 400, 5),
        # Produced by the last step: no later call ever sends it.
        {"role": "tool", "name": "read_file", "content": page},
        _stats(3, 1400, 50, 5),
    )

    assert est.readable_savings(record, PRICES) == {"tokens": 0.0, "dollars": 0.0}


@pytest.mark.parametrize("prices", ["4,0.2,5", "a,b,c,d", "4,0.2,-5,20", ""])
def test_bad_prices_fail_loudly(prices):
    with pytest.raises(ValueError, match="--prices"):
        est.Prices.parse(prices)


def test_prices_are_required(tmp_path, capsys):
    path = _write(tmp_path, _record(_stats(1, 0, 10, 1)))

    with pytest.raises(SystemExit):
        est.main([str(path)])

    assert "--prices" in capsys.readouterr().err


def test_a_record_without_usage_is_an_error(tmp_path, capsys):
    path = _write(tmp_path, _record({"role": "user", "content": "hi"}))

    assert est.main([str(path), "--prices", "1,1,1,1"]) == 2
    assert "per-step usage" in capsys.readouterr().err


def test_a_file_without_a_record_is_an_error(tmp_path, capsys):
    path = tmp_path / "transcript.json"
    path.write_text("not json\n", encoding="utf-8")

    assert est.main([str(path), "--prices", "1,1,1,1"]) == 2
    assert "no JSON record" in capsys.readouterr().err


def test_a_directory_is_searched_for_transcripts(tmp_path, capsys):
    run = tmp_path / "task-a"
    run.mkdir()
    _write(run, _record(_stats(1, 0, 100, 1), _stats(2, 100, 10, 1)))

    assert est.main([str(tmp_path), "--prices", "1,1,1,1", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["runs"] == 1 and report["calls"] == 2


def test_a_later_object_without_a_conversation_is_skipped(tmp_path):
    record = _record(_stats(1, 0, 100, 1))
    path = tmp_path / "transcript.json"
    path.write_text(
        json.dumps(record) + "\n" + json.dumps({"event": "done"}) + "\n",
        encoding="utf-8",
    )

    assert est.load_record(path) == record


def test_only_objects_without_a_conversation_is_an_error(tmp_path):
    path = tmp_path / "transcript.json"
    path.write_text(json.dumps({"type": "result"}) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="--verbose"):
        est.load_record(path)
