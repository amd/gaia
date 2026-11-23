# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Load the OpenAI SDK on first use, not on import.

Every GAIA process imports ``gaia.llm.lemonade_client``, and most of them (the
daemon, a backend that relays chat to a sidecar) never make an SDK call. The SDK
costs each of them about 33 MB of memory just to be imported.

Stdlib only at import time.
"""

import sys
from typing import Any, Tuple


class _LazyOpenAI:
    """Stands in for the ``openai`` module; imports it on first attribute use."""

    def __getattr__(self, name: str) -> Any:
        return getattr(_sdk(), name)


def _sdk() -> Any:
    # A plain import statement, so the freeze scripts still bundle the SDK.
    import openai as sdk  # pylint: disable=import-outside-toplevel,redefined-outer-name

    return sdk


openai = _LazyOpenAI()


def OpenAI(*args: Any, **kwargs: Any) -> Any:  # pylint: disable=invalid-name
    """Build an ``openai.OpenAI`` client."""
    return _sdk().OpenAI(*args, **kwargs)


def loaded_openai_errors(*names: str) -> Tuple[type, ...]:
    """The named SDK exception classes, or ``()`` while the SDK is not loaded.

    For ``except`` clauses that can run in a process which never used the SDK:
    no SDK exception can be in flight there, so there is nothing to match and
    no reason to import it.
    """
    module = sys.modules.get("openai")
    if module is None:
        return ()
    return tuple(getattr(module, name) for name in names)
