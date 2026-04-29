import importlib.util
import os
import re


def _load_pkce_module():
    path = os.path.join(os.path.dirname(__file__), "..", "..", "src", "gaia", "connections", "pkce.py")
    path = os.path.abspath(path)
    spec = importlib.util.spec_from_file_location("pkce", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_generate_code_verifier_length_and_chars():
    pkce = _load_pkce_module()
    v = pkce.generate_code_verifier(64)
    assert 43 <= len(v) <= 128
    # Allowed chars per RFC 7636 (unreserved characters)
    assert re.fullmatch(r"[A-Za-z0-9\-._~]{43,128}", v)


def test_compute_code_challenge_matches_sha256_base64url():
    pkce = _load_pkce_module()
    v = "test-verifier-1234567890-~._"
    c = pkce.compute_code_challenge(v)
    # Manual compute using hashlib + base64 for cross-check
    import hashlib, base64

    expected = base64.urlsafe_b64encode(hashlib.sha256(v.encode("ascii")).digest()).decode("ascii").rstrip("=")
    assert c == expected
