# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""PyInstaller runtime hook: find the host's CA bundle on any Linux distro.

The Linux binary carries the OpenSSL of the system it was built on, and that
OpenSSL looks for CA certificates where THAT distro keeps them. Built on a
RHEL-family image (the glibc floor, #5526), it looks under ``/etc/pki`` and
finds nothing on Ubuntu or Debian, so every HTTPS call from a ``run_python``
script would fail certificate verification. The agent itself routes through
``truststore`` (gaia.tls), but scripts run before that is injected.

Runs before any other code in every launch of the binary, including the
``run_python`` re-launch. It changes nothing when the user set
``SSL_CERT_FILE``/``SSL_CERT_DIR``, when a built-in CA file exists, or when a
built-in CA directory contains hashed CA links. It falls back to a bundled CA
bundle only when that directory is empty or unreadable. Names are
private because hooks share the entry script's ``__main__`` namespace.
"""

import os
import ssl
import sys

#: Where each Linux family keeps its PEM bundle (the list Go's crypto/x509 uses).
_CA_BUNDLES = (
    "/etc/ssl/certs/ca-certificates.crt",  # Debian, Ubuntu, Arch, Gentoo
    "/etc/pki/tls/certs/ca-bundle.crt",  # Fedora, RHEL 6+
    "/etc/ssl/ca-bundle.pem",  # openSUSE
    "/etc/pki/tls/cacert.pem",  # OpenELEC
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",  # CentOS, RHEL 7
    "/etc/ssl/cert.pem",  # Alpine
)


def _has_ca_links(capath):
    try:
        return os.path.isdir(capath) and any(
            os.path.isfile(os.path.join(capath, name))
            for name in os.listdir(capath)
            if name.endswith(".0")
        )
    except OSError:
        return False


def _os_ca_bundle(environ, default_paths, exists=os.path.isfile, has_ca=_has_ca_links):
    """The bundle to point ``SSL_CERT_FILE`` at, or ``None`` to leave it alone."""
    if environ.get("SSL_CERT_FILE") or environ.get("SSL_CERT_DIR"):
        return None
    if default_paths.cafile:
        return None
    if default_paths.capath and has_ca(default_paths.capath):
        return None
    return next((path for path in _CA_BUNDLES if exists(path)), None)


if sys.platform.startswith("linux"):
    _bundle = _os_ca_bundle(os.environ, ssl.get_default_verify_paths())
    if _bundle:
        os.environ["SSL_CERT_FILE"] = _bundle
