# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Whether GAIA's own Lemonade can verify HTTPS on this Linux PC.

The Linux ``lemond`` GAIA bundles is built on Ubuntu, and its libcurl checks
every HTTPS site against exactly one file, ``/etc/ssl/certs/ca-certificates.crt``.
It reads no environment variable for another one (``SSL_CERT_FILE``,
``CURL_CA_BUNDLE`` and ``SSL_CERT_DIR`` were each tried and ignored), so where
that file is missing every cloud model and every download fails with
``CURL error: Problem with the SSL CA cert``. Fedora 44's original
``ca-certificates`` build ships no such file; its update restores it.

GAIA cannot supply the file without root, so it names the problem instead of
letting it read as a rejected key or a broken download.

stdlib-only: imported by the launcher, the client and the UI routers.
"""

from __future__ import annotations

import os
import sys
from typing import Callable, Optional

#: The only CA file the bundled lemond's libcurl reads.
LEMOND_CA_FILE = "/etc/ssl/certs/ca-certificates.crt"

#: Where Linux distributions keep the system CA bundle, Debian's path first.
SYSTEM_CA_BUNDLES = (
    LEMOND_CA_FILE,
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
    "/etc/ssl/ca-bundle.pem",
    "/etc/ssl/cert.pem",
)

#: What lemond's libcurl says when it cannot read its CA file (CURLE 77).
_CURL_CA_ERROR = "Problem with the SSL CA cert"


def find_system_ca_bundle(
    exists: Callable[[str], bool] = os.path.isfile,
) -> Optional[str]:
    """The first system CA bundle present, or None."""
    return next((path for path in SYSTEM_CA_BUNDLES if exists(path)), None)


def ca_store_problem(
    *,
    platform: Optional[str] = None,
    exists: Callable[[str], bool] = os.path.isfile,
) -> Optional[str]:
    """Why GAIA's Lemonade cannot verify HTTPS here, or None when it can.

    Args:
        platform: ``sys.platform`` to judge; the running one by default.
        exists: File test, replaceable in tests.
    """
    if not (platform or sys.platform).startswith("linux"):
        return None
    if exists(LEMOND_CA_FILE):
        return None
    effect = (
        "Until that file exists, GAIA's model server cannot reach cloud models "
        "or download models."
    )
    bundle = find_system_ca_bundle(exists)
    if bundle:
        return (
            f"This PC's certificate store has no {LEMOND_CA_FILE}, the file "
            "GAIA's model server reads to trust HTTPS sites (the store is at "
            f"{bundle}). {effect} Install your system's latest updates (the "
            "updated ca-certificates package adds that file), then restart GAIA."
        )
    return (
        "This PC has no system certificate store, so GAIA's model server cannot "
        f"trust HTTPS sites (it reads {LEMOND_CA_FILE}). {effect} Install the "
        "ca-certificates package from your system's software manager, then "
        "restart GAIA."
    )


def explain_tls_failure(message: str, **probe) -> str:
    """*message* with the cause and remedy in front when it is a CA-store failure.

    Args:
        message: An error Lemonade returned.
        **probe: Passed to :func:`ca_store_problem`.
    """
    if _CURL_CA_ERROR not in (message or ""):
        return message
    problem = ca_store_problem(**probe)
    if not problem:
        return message
    return f"{problem} (Lemonade said: {message})"
