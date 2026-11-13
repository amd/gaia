# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The root conftest stops a test from reaching a local Lemonade on 13305 when
LEMONADE_BASE_URL points the run at another server."""

import os
import socket
import urllib.parse

import pytest


def _redirected() -> bool:
    base_url = os.environ.get("LEMONADE_BASE_URL", "").strip()
    if not base_url:
        return False
    parsed = urllib.parse.urlsplit(base_url)
    local = parsed.hostname in ("localhost", "::1") or (
        parsed.hostname or ""
    ).startswith("127.")
    return not (local and parsed.port == 13305)


pytestmark = [
    pytest.mark.allow_network,
    pytest.mark.skipif(
        not _redirected(),
        reason="guard is active only when LEMONADE_BASE_URL points away from 13305",
    ),
]


@pytest.mark.parametrize("method", ["connect", "connect_ex"])
def test_connecting_to_default_port_fails_the_test(method):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(pytest.fail.Exception, match="LEMONADE_BASE_URL"):
            getattr(sock, method)(("127.0.0.1", 13305))


def test_other_ports_are_untouched():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.create_connection(server.getsockname(), timeout=5):
            pass
