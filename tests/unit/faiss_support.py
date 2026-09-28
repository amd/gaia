# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Shared gate for tests that run a real FAISS search."""

import pytest


def require_faiss():
    """Skip unless a real ``index.search`` can run in THIS process.

    Two things stop it. faiss-cpu may not be installed — the usual
    ``importorskip`` case. Or a second OpenMP runtime (torch's) is already
    resident, which makes the native search abort the interpreter: pytest dies
    with SIGABRT and takes the rest of the run with it, so the search-side
    guard refuses the call instead. A refusal is correct but it is not a test
    result, so skip with the reason rather than fail.
    """
    faiss = pytest.importorskip("faiss")

    from gaia.agents.base.memory import _loaded_omp_runtimes

    runtimes = _loaded_omp_runtimes()
    if len(runtimes) > 1:
        pytest.skip(
            "a second OpenMP runtime is resident "
            f"({', '.join(runtimes)}); a real faiss search would abort this process"
        )
    return faiss
