# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Image-generation errors must name the failure the user actually hit.

The bug this pins: ``requests`` puts "HTTPConnectionPool" in the text of a
*read timeout* as well as a refused connection, so a substring test for
"connect" reported a healthy server as unreachable. A first-use model download
runs to several GB and routinely outlasts the request window, so the message a
user saw most often was the one telling them to restart a server that was fine.
"""

from __future__ import annotations

import pytest

from gaia.llm.lemonade_client import LemonadeClientError
from gaia.sd.mixin import SDToolsMixin

_TIMEOUT = LemonadeClientError(
    "Request failed: HTTPConnectionPool(host='localhost', port=13305): "
    "Read timed out. (read timeout=600)"
)
_REFUSED = LemonadeClientError(
    "Request failed: HTTPConnectionPool(host='localhost', port=19999): "
    "Max retries exceeded with url: /api/v1/load (Caused by NewConnectionError("
    "\"HTTPConnection(host='localhost', port=19999): Failed to establish a new "
    'connection: [WinError 10061] No connection could be made"))'
)


def test_a_download_timeout_is_not_reported_as_an_unreachable_server():
    """The regression: both errors carry 'HTTPConnectionPool'."""
    message = SDToolsMixin._describe_client_error(_TIMEOUT, model="SDXL-Turbo")

    assert "timed out" in message.lower()
    assert "SDXL-Turbo" in message, "the user cannot pre-fetch an unnamed model"
    assert "pull" in message, "a timeout must point at the fix, not just the symptom"
    # The wrong diagnosis, in the words that would send the user to restart.
    assert "cannot reach" not in message.lower()


def test_a_refused_connection_says_the_server_is_not_running():
    message = SDToolsMixin._describe_client_error(_REFUSED, model="SDXL-Turbo")

    assert "cannot reach" in message.lower()
    assert "lemonade-server serve" in message
    assert "timed out" not in message.lower()


@pytest.mark.parametrize("error", [_TIMEOUT, _REFUSED], ids=["timeout", "refused"])
def test_the_raw_error_survives_for_debugging(error):
    """A friendlier message must not delete the detail a bug report needs."""
    assert "13305" in SDToolsMixin._describe_client_error(
        error, model="SDXL-Turbo"
    ) or "19999" in SDToolsMixin._describe_client_error(error, model="SDXL-Turbo")


def test_an_unrecognized_failure_is_passed_through_named():
    message = SDToolsMixin._describe_client_error(
        LemonadeClientError("out of VRAM"), model="SDXL-Base-1.0"
    )

    assert "out of VRAM" in message
    assert "SDXL-Base-1.0" in message
