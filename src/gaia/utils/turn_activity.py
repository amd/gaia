# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Whether a chat turn is running, so background work can step aside for it."""

import functools
import threading
import time
from contextlib import contextmanager
from typing import Callable, Iterator, TypeVar

_count = [0]
_last_ended = [float("-inf")]
_lock = threading.Lock()

F = TypeVar("F", bound=Callable)


@contextmanager
def chat_turn() -> Iterator[None]:
    """Mark a chat turn as running for the length of the block."""
    with _lock:
        _count[0] += 1
    try:
        yield
    finally:
        with _lock:
            _count[0] -= 1
            _last_ended[0] = time.monotonic()


def is_chat_turn(fn: F) -> F:
    """Mark a chat turn as running while *fn* runs."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with chat_turn():
            return fn(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


def turn_running(within: float = 0.0) -> bool:
    """Whether a turn is running, or ended less than *within* seconds ago."""
    return _count[0] > 0 or time.monotonic() - _last_ended[0] < within
