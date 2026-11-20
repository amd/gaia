# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Make GAIA's outbound HTTPS trust the operating system's certificate store.

Python verifies TLS against certifi's bundled CA list by default, which knows
nothing about the root a corporate TLS-inspecting proxy (Zscaler and the like)
installs into the OS store — so every hub, model, and web request fails with
``CERTIFICATE_VERIFY_FAILED`` on those networks while the browser, pip and uv
work. ``truststore`` swaps ``ssl.SSLContext`` for one that verifies through the
OS (Windows CryptoAPI, macOS Security framework, the system OpenSSL store on
Linux), the same mechanism pip uses. CA files a library loads on top (certifi
via requests/httpx) are still honoured, so nothing that verified before stops
verifying.

Call :func:`use_os_trust_store` once at the top of each process entry point,
never at import time: a library user importing ``gaia`` must not have their
process's ``ssl`` module patched behind their back.
"""

from __future__ import annotations

import ssl
import sys

_injected = False
# Captured before use_os_trust_store() can swap ssl.SSLContext.
_STOCK_SSL_CONTEXT = ssl.SSLContext


def stock_ssl_context(protocol: int, **settings) -> ssl.SSLContext:
    """An ``ssl.SSLContext`` the OS trust store injection doesn't reach.

    For TLS servers: truststore's context checks the *peer's* certificate after
    every handshake, so a server built on it drops each client that sends none,
    right after the handshake completes. *settings* (``minimum_version=...``)
    are set in order through the C base class, since ``ssl.SSLContext``'s own
    setters look up the patched class and recurse.

    Raises:
        RuntimeError: ``ssl.SSLContext`` was already patched when this module
            was imported, so no stock class is at hand.
    """
    if _STOCK_SSL_CONTEXT.__module__ != "ssl":
        raise RuntimeError(
            f"ssl.SSLContext was replaced by {_STOCK_SSL_CONTEXT.__module__} "
            "before gaia.tls was imported, so GAIA can't build a TLS server "
            "context. Import gaia.tls before anything that patches ssl."
        )
    context = _STOCK_SSL_CONTEXT(protocol)
    base = _STOCK_SSL_CONTEXT.__mro__[1]
    for name, value in settings.items():
        getattr(base, name).__set__(context, value)
    return context


def use_os_trust_store() -> bool:
    """Route this process's ``ssl`` verification through the OS trust store.

    Idempotent. Returns ``True`` once injected. If ``truststore`` cannot be
    loaded the process keeps Python's default (certifi) verification and a
    warning names the consequence and the fix — default behaviour is unchanged,
    so this never breaks a network that worked before.
    """
    global _injected
    if _injected:
        return True
    try:
        import truststore

        truststore.inject_into_ssl()
    except (ImportError, OSError) as exc:
        # Printed to stderr, never stdout: stdout is the event/MCP channel
        # for stdio transports.
        print(
            "WARNING: GAIA could not switch HTTPS verification to the system "
            f"certificate store ({type(exc).__name__}: {exc}). It is using "
            "Python's bundled CA list, so hub, model and web requests fail with "
            "CERTIFICATE_VERIFY_FAILED behind a TLS-inspecting proxy. Fix: "
            "pip install --upgrade 'truststore>=0.10.0'",
            file=sys.stderr,
            flush=True,
        )
        return False
    _injected = True
    return True
