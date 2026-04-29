# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""conftest for unit tests.

The CI ``test_unit.yml`` workflow sets ``GAIA_MEMORY_DISABLED=1`` so that
agents that import ``MemoryMixin`` can be instantiated without a running
Lemonade embedding server.  However the memory unit tests (test_memory_*.py)
mock out the embedder and need ``init_memory()`` to run its real flow.

This conftest auto-clears ``GAIA_MEMORY_DISABLED`` when collecting tests
from any ``test_memory_*.py`` file so the per-test mocks take effect.
"""

import os

import pytest


@pytest.fixture(autouse=True)
def _enable_memory_for_memory_tests(request):
    """Auto-fixture: clear GAIA_MEMORY_DISABLED for memory test modules.

    Only applies to tests in modules whose filename starts with ``test_memory_``.
    Other tests continue to honour the env var (so non-memory agents init
    cleanly without Lemonade).
    """
    module_path = getattr(request.module, "__file__", "") or ""
    is_memory_test = "test_memory_" in os.path.basename(module_path)

    if not is_memory_test:
        yield
        return

    prior = os.environ.pop("GAIA_MEMORY_DISABLED", None)
    try:
        yield
    finally:
        if prior is not None:
            os.environ["GAIA_MEMORY_DISABLED"] = prior
