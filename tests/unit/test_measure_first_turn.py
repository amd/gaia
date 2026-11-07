# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The gates ``util/measure_first_turn.py`` applies to a real-model run.

The measurement needs a GPU, so it runs on the eval pool; a gate that quietly
stopped gating would not show up until a regression shipped. These pin each one
on hand-built legs.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_PATH = Path(__file__).parents[2] / "util" / "measure_first_turn.py"
_spec = importlib.util.spec_from_file_location("measure_first_turn", _PATH)
measure = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(measure)


def _turn(task, reads, cached, ok=True):
    return {
        "task": task,
        "ok": ok,
        "steps": len(reads),
        "reads": reads,
        "cached": cached,
        "wall_s": 1.0,
    }


def _off():
    return [_turn("hello", [21000], [0]), _turn("math", [100], [21000])]


def _on():
    return [_turn("hello", [13500], [0]), _turn("math", [100], [13500])]


def test_a_healthy_run_passes():
    assert measure.judge(_on(), _off()) == []


def test_a_grown_prefix_fails():
    on = [_turn("hello", [20000], [0]), _turn("math", [100], [20000])]
    assert any("first turn" in p for p in measure.judge(on, _off()))


def test_a_broken_cache_fails_even_when_the_first_turn_is_fine():
    on = [_turn("hello", [13500], [0]), _turn("math", [13600], [0])]
    assert any("prompt cache broke" in p for p in measure.judge(on, _off()))


def test_extra_model_calls_fail():
    on = [
        _turn("hello", [13500], [0]),
        _turn("math", [100, 100, 100, 100], [13500, 13600, 13700, 13800]),
    ]
    assert any("more model calls" in p for p in measure.judge(on, _off()))


def test_one_extra_call_is_normal_wobble():
    on = [_turn("hello", [13500], [0]), _turn("math", [100, 100], [13500, 13600])]
    assert measure.judge(on, _off()) == []


def test_missing_timings_fail_instead_of_passing_every_gate():
    on = [_turn("hello", [0], [0]), _turn("math", [0], [0])]
    assert any("no server timings" in p for p in measure.judge(on, _off()))
    assert any("no server timings" in p for p in measure.judge(_on(), on))


def test_a_turn_with_no_model_call_fails():
    on = [_turn("hello", [13500], [0]), _turn("math", [], [])]
    assert any("no server timings" in p for p in measure.judge(on, _off()))


def test_a_wrong_answer_fails():
    on = [_turn("hello", [13500], [0]), _turn("math", [100], [13500], ok=False)]
    assert any("wrong answer" in p for p in measure.judge(on, _off()))
