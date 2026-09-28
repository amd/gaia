# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The ``_block_network`` guard must block real connects and still let asyncio start.

Windows has no native ``socketpair()`` — CPython emulates it with a loopback TCP
connect, and asyncio builds every event loop's self pipe that way. A guard that
patches ``socket.socket.connect`` with no exemption stops async tests from
starting at all; exempting loopback wholesale would instead let a test reach a
local Lemonade server. These tests pin both halves of that trade-off.
"""

import asyncio
import socket

import pytest


def _connect(address):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.connect(address)


def test_loopback_connect_is_blocked():
    """A local Lemonade server stays unreachable from a unit test."""
    with pytest.raises(ConnectionError):
        _connect(("127.0.0.1", 8000))


def test_external_connect_is_blocked():
    with pytest.raises(ConnectionError):
        _connect(("example.com", 80))


def test_socketpair_is_allowed_and_leaves_the_guard_armed():
    left, right = socket.socketpair()
    try:
        left.sendall(b"ping")
        assert right.recv(4) == b"ping"
    finally:
        left.close()
        right.close()

    with pytest.raises(ConnectionError):
        _connect(("127.0.0.1", 8000))


@pytest.mark.asyncio
async def test_async_test_can_start():
    await asyncio.sleep(0)
