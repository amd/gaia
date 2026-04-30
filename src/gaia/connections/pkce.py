# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
PKCE primitives (RFC 7636) for OAuth flows in ``gaia.connections``.

PKCE is mandatory for desktop apps per RFC 8252; it replaces the client
secret that web apps use. Two values flow through the OAuth handshake:

- The **code verifier**: a high-entropy random string generated locally and
  held in memory for the duration of the flow.
- The **code challenge**: ``base64url(sha256(verifier))`` (no padding) sent
  to the authorization endpoint as ``code_challenge`` with
  ``code_challenge_method=S256``.

The token endpoint receives the verifier in clear during the
authorization-code → token exchange and rejects the exchange unless the
sha256 of the verifier matches the previously-sent challenge.
"""

from __future__ import annotations

import base64
import hashlib
import secrets


def generate_code_verifier() -> str:
    """
    Return a high-entropy verifier string suitable for PKCE.

    ``secrets.token_urlsafe(64)`` produces 86 base64url characters from 64
    random bytes — well within the RFC 7636 [43, 128] character window. No
    trimming needed; the test in ``test_pkce.py`` confirms length and
    charset across 1000 random samples.
    """
    return secrets.token_urlsafe(64)


def compute_code_challenge(verifier: str) -> str:
    """
    Compute the S256 PKCE challenge for ``verifier``.

    Returns ``base64url(sha256(verifier))`` with the trailing ``=`` padding
    stripped, per RFC 7636 §4.2.
    """
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
