"""PKCE helpers (RFC 7636).

Implementations are intentionally small and dependency-free to make unit
testing straightforward.
"""
import secrets
import hashlib
import base64
from typing import Optional

# Unreserved characters per RFC 3986 (used by RFC 7636 for code_verifier)
_ALLOWED = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"


def generate_code_verifier(length: int = 64) -> str:
    """Generate a PKCE code_verifier.

    Length must be between 43 and 128 characters (inclusive).
    Characters are from the "unreserved" set: ALPHA / DIGIT / "-" / "." / "_" / "~".
    """
    if length < 43 or length > 128:
        raise ValueError("code_verifier length must be between 43 and 128")
    return "".join(secrets.choice(_ALLOWED) for _ in range(length))


def compute_code_challenge(verifier: str) -> str:
    """Compute S256 code_challenge = BASE64URL-ENCODE(SHA256(verifier))."""
    if not isinstance(verifier, str):
        raise TypeError("verifier must be a str")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    b64 = base64.urlsafe_b64encode(digest).decode("ascii")
    return b64.rstrip("=")


__all__ = ["generate_code_verifier", "compute_code_challenge"]
