# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Mail and calendar tool tests never reach the real keychain.

The tools enumerate connected accounts through ``gaia.connectors``, which reads
the keyring. A test that forgets to fake one of those calls would read the
developer's real keychain, so every test here runs on the in-memory backend.
"""

import pytest


@pytest.fixture(autouse=True)
def _autouse_in_memory_keyring(in_memory_keyring):
    import keyring

    keyring.set_keyring(in_memory_keyring)
    in_memory_keyring._store.clear()
    yield in_memory_keyring
    in_memory_keyring._store.clear()
