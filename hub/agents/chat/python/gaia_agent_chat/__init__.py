# Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Deprecated: ``ChatAgent`` now ships in the flagship package, ``gaia_agent``.

This package is a re-export shim kept for one release so existing imports keep
working; it is deleted in #6103. Import from the new homes instead:

* ``gaia_agent_chat.agent``         -> ``gaia_agent.chat_agent``
* ``gaia_agent_chat.app``           -> ``gaia_agent.interactive``
* ``gaia_agent_chat.<module>``      -> ``gaia_agent.<module>`` for ``profiles``,
  ``tool_bundles``, ``session``, ``python_jobs``, ``python_runner`` and
  ``tool_requests``

Each old submodule is the new module object itself, so ``isinstance`` checks
and ``unittest.mock.patch("gaia_agent_chat.agent.X")`` behave as before.

The ``chat``, ``doc`` and ``file`` agent ids are no longer registered here;
the registry resolves them to the flagship ``gaia`` agent so stored sessions
reopen on it.
"""

import warnings

__all__ = ["ChatAgent", "ChatAgentConfig", "ChatAgentLite"]

__version__ = "0.1.0"

warnings.warn(
    "gaia_agent_chat is deprecated and will be removed in the next release: "
    "ChatAgent now ships in the flagship package. Import it with "
    "`from gaia_agent.chat_agent import ChatAgent, ChatAgentConfig` "
    "(install gaia-agent-gaia).",
    DeprecationWarning,
    stacklevel=2,
)

_LAZY = {
    "ChatAgent": "gaia_agent.chat_agent",
    "ChatAgentConfig": "gaia_agent.chat_agent",
    "ChatAgentLite": "gaia_agent_chat.lite_agent",
}


def __getattr__(name):
    if name in _LAZY:
        import importlib

        module = importlib.import_module(_LAZY[name])
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
