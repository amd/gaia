# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The determinism threshold in the NPU embedder hardware test (#5787)."""

import math

import pytest

from tests.test_npu_flm_embedder_hw import DETERMINISM_MIN_COSINE, cosine


def _unit(vec):
    n = math.sqrt(sum(x * x for x in vec))
    return [x / n for x in vec]


def test_identical_vectors_pass():
    v = _unit([0.1 * i - 3.0 for i in range(768)])
    assert cosine(v, v) == pytest.approx(1.0)
    assert cosine(v, v) >= DETERMINISM_MIN_COSINE


def test_last_bit_drift_passes():
    v = _unit([math.sin(i) for i in range(768)])
    drifted = [x * (1 + 1e-4 * ((-1) ** i)) for i, x in enumerate(v)]
    assert drifted != v
    assert cosine(v, drifted) >= DETERMINISM_MIN_COSINE


def test_unrelated_vectors_fail():
    # The #5787 failure: two well-formed unit vectors pointing different ways.
    a = _unit([math.sin(i) for i in range(768)])
    b = _unit([math.cos(3 * i + 1) for i in range(768)])
    assert cosine(a, b) < DETERMINISM_MIN_COSINE


def test_opposite_vectors_fail():
    v = _unit([float(i % 7) - 3 for i in range(768)])
    assert cosine(v, [-x for x in v]) == pytest.approx(-1.0)


def test_scaled_vector_is_not_a_divergence():
    v = [float(i % 5) + 1 for i in range(768)]
    assert cosine(v, [2.5 * x for x in v]) == pytest.approx(1.0)


def test_length_mismatch_raises():
    with pytest.raises(ValueError, match="lengths differ"):
        cosine([1.0, 0.0], [1.0, 0.0, 0.0])


def test_zero_vector_raises():
    with pytest.raises(ValueError, match="zero-norm"):
        cosine([0.0] * 4, [1.0, 0.0, 0.0, 0.0])
