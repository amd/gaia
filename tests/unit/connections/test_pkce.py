# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
T-3a (AC1, AC2): PKCE primitives — code_verifier and code_challenge.

Acceptance:
- ``generate_code_verifier()`` length is in [43, 128] inclusive (RFC 7636 §4.1)
  for 1000 random samples.
- Verifier alphabet is exactly the RFC 7636 unreserved-character set
  (``A-Za-z0-9-._~``).
- ``compute_code_challenge`` is base64url(sha256(verifier)) with the
  ``=`` padding stripped (RFC 7636 §4.2). Verified against the published
  vector in RFC 7636 Appendix B.
"""

from __future__ import annotations

import re

from gaia.connections.pkce import compute_code_challenge, generate_code_verifier

_RFC7636_VERIFIER_CHARSET = re.compile(r"^[A-Za-z0-9._~\-]+$")


class TestGenerateCodeVerifier:
    def test_length_and_charset_over_1000_samples(self):
        # 1000 iterations stresses the entropy source and verifies length
        # invariance — token_urlsafe(64) is deterministically 86 chars.
        for _ in range(1000):
            v = generate_code_verifier()
            assert 43 <= len(v) <= 128, f"verifier length out of range: {len(v)}"
            assert _RFC7636_VERIFIER_CHARSET.fullmatch(
                v
            ), f"verifier contains illegal character: {v!r}"

    def test_no_padding_in_verifier(self):
        # urlsafe_b64encode produces ``=`` padding; the verifier must not
        # carry it (RFC 7636 §4.1 forbids ``=``).
        for _ in range(50):
            assert "=" not in generate_code_verifier()

    def test_uniqueness(self):
        # Cryptographic randomness — collisions in 1000 samples would
        # indicate a fundamentally broken RNG.
        samples = {generate_code_verifier() for _ in range(1000)}
        assert len(samples) == 1000


class TestComputeCodeChallenge:
    def test_rfc7636_appendix_b_vector(self):
        # RFC 7636 §B (Example for the S256 code_challenge_method):
        #   verifier  = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        #   challenge = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        expected = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
        assert compute_code_challenge(verifier) == expected

    def test_no_padding_in_challenge(self):
        # base64url(sha256()) raw produces ``...=``; the challenge must
        # be unpadded per RFC 7636.
        challenge = compute_code_challenge(generate_code_verifier())
        assert not challenge.endswith("=")
        assert "=" not in challenge

    def test_challenge_alphabet_is_url_safe_base64(self):
        # base64url alphabet: A-Z a-z 0-9 - _ (no + or /).
        url_safe = re.compile(r"^[A-Za-z0-9_\-]+$")
        for _ in range(50):
            challenge = compute_code_challenge(generate_code_verifier())
            assert url_safe.fullmatch(challenge), challenge

    def test_challenge_is_deterministic(self):
        v = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        # Same input → same output — sha256 is deterministic.
        assert compute_code_challenge(v) == compute_code_challenge(v)
