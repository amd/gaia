# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Whether a chat turn is running, so background work can step aside for it."""

import functools
import threading
from contextlib import contextmanager
from typing import Callable, Iterator, TypeVar

_count = [0]
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


def is_chat_turn(fn: F) -> F:
    """Mark a chat turn as running while *fn* runs."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with chat_turn():
            return fn(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


def turn_running() -> bool:
    return _count[0] > 0
