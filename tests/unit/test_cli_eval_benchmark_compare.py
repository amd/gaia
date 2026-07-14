# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Pin the `gaia eval benchmark --compare` ctx-mismatch contract (#1892).

ASSUMED SEAM: this test assumes the ctx-comparison logic will be factored into
a standalone ``_compare_benchmark_ctx(current_ctx, baseline, baseline_path)``
function in ``src/gaia/cli.py`` — if the implementer chooses a different seam
(e.g. inlining the check directly in the `--compare` block), this is the
flagged assumption to reconcile, not a hard requirement on the exact function
name. The exact name ``_compare_benchmark_ctx`` is what THIS test currently
imports, so red-first it fails with ImportError until that seam exists.

Today's ``--compare`` block (src/gaia/cli.py, ~lines 4286-4299) only compares
``avg_tokens_per_second`` between the current run and a loaded baseline
scorecard.json; it has no ctx_size awareness at all.
"""

import pytest


def test_benchmark_compare_ctx_mismatch_hard_error():
    from gaia.cli import _compare_benchmark_ctx  # noqa: PLC0415

    with pytest.raises((RuntimeError, SystemExit)):
        _compare_benchmark_ctx(
            current_ctx=16384,
            baseline={"ctx_size": 4096},
            baseline_path="baseline.json",
        )


def test_benchmark_compare_ctx_baseline_missing_warns_and_proceeds():
    from gaia.cli import _compare_benchmark_ctx  # noqa: PLC0415

    # No ctx_size key in the baseline at all -> a warning is printed (not
    # asserted precisely here), but no exception propagates.
    _compare_benchmark_ctx(
        current_ctx=16384,
        baseline={},
        baseline_path="baseline.json",
    )


def test_benchmark_compare_ctx_match_passes():
    from gaia.cli import _compare_benchmark_ctx  # noqa: PLC0415

    _compare_benchmark_ctx(
        current_ctx=16384,
        baseline={"ctx_size": 16384},
        baseline_path="baseline.json",
    )


def test_ctx_size_zero_or_negative_rejected():
    """``gaia eval benchmark --ctx-size 0`` (and negative) must be rejected
    with a non-zero exit BEFORE any benchmark work runs (src/gaia/cli.py,
    the ``--ctx-size`` guard just before ``run_benchmark`` is invoked)."""
    import sys

    from gaia import cli

    old_argv = sys.argv
    for bad_ctx in ("0", "-5"):
        sys.argv = ["gaia", "eval", "benchmark", "--ctx-size", bad_ctx]
        try:
            with pytest.raises(SystemExit) as exc:
                cli.main()
            assert exc.value.code != 0
        finally:
            sys.argv = old_argv


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
