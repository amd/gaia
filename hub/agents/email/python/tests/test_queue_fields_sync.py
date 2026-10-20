# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The email translator's ``_QUEUE_FIELDS`` must match the core translator's.

The sidecar translator copies the tuple rather than importing it, to stay free
of the ``gaia.ui`` import chain. This pins the copy so a field added on one side
cannot silently vanish from the other surface's ``model_queue`` status.
"""

from __future__ import annotations

import pytest

pytest.importorskip("gaia_agent_email")
pytest.importorskip("gaia.ui.sse_translation")

from gaia.ui.sse_translation import _QUEUE_FIELDS as _CORE  # noqa: E402
from gaia_agent_email.sse_translation import (  # noqa: E402
    _QUEUE_FIELDS as _SIDECAR,
)


def test_queue_fields_match_the_core_translator():
    assert _SIDECAR == _CORE


def test_queue_fields_carry_every_key_the_turn_stream_reports():
    """Every key a wait listener sees and the UI renders survives translation."""
    for key in ("event", "position", "queue_length", "holder", "waited_s"):
        assert key in _SIDECAR, key
