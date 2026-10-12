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

import sys

_injected = False


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
        # stderr, not the GAIA logger: its console handler writes to stdout,
        # which is the event/MCP channel for stdio transports.
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
