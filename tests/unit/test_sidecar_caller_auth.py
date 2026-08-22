# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The shared sidecar caller-auth layer (`gaia.sidecar.caller_auth`).

The per-sidecar wiring is covered where it lives (the flagship's
`hub/agents/gaia/python/tests/test_caller_auth.py` drives real HTTP through the
real app). These tests pin the mechanism underneath both sidecars, where a
subtle bug is easiest to miss and worst to have: token comparison, the
loopback-host parsing that decides a DNS-rebinding verdict, and the fail-loud
contract on a misconfigured secret file.
"""

from __future__ import annotations

import pytest

from gaia.sidecar import caller_auth
from gaia.sidecar.caller_auth import CallerAuthConfig

_TOKEN = "s3cret-session-token"

_FILE_VAR = "GAIA_TEST_SIDECAR_TOKEN_FILE"
_ENV_VAR = "GAIA_TEST_SIDECAR_TOKEN"


@pytest.fixture(autouse=True)
def _isolate():
    caller_auth.reset()
    yield
    caller_auth.reset()


def _from_env(**kw):
    return caller_auth.config_from_env(
        token_file_env_var=_FILE_VAR,
        token_env_var=_ENV_VAR,
        surface="test sidecar",
        **kw,
    )


# -- token comparison --------------------------------------------------------


@pytest.mark.parametrize(
    "header,expected",
    [
        (f"Bearer {_TOKEN}", True),
        (f"bearer {_TOKEN}", True),  # scheme is case-insensitive per RFC 7235
        (f"BEARER {_TOKEN}", True),
        (f"Bearer  {_TOKEN} ", True),  # surrounding whitespace tolerated
        (f"Basic {_TOKEN}", False),  # wrong scheme must not pass
        (_TOKEN, False),  # bare token is not a Bearer header
        ("Bearer wrong-token", False),
        ("Bearer ", False),
        ("Bearer", False),
        ("", False),
        (f"Bearer {_TOKEN}x", False),  # prefix must not satisfy the compare
        (f"Bearer {_TOKEN[:-1]}", False),
    ],
)
def test_token_ok_accepts_only_a_correct_bearer_header(header, expected):
    config = CallerAuthConfig(token=_TOKEN)
    assert caller_auth.token_ok(config, header) is expected


def test_token_check_is_disabled_when_no_token_is_configured():
    """Dev mode: the token check goes off, deliberately and loudly."""
    assert caller_auth.token_ok(CallerAuthConfig(token=None), "") is True


# -- Host header parsing (the DNS-rebinding verdict) -------------------------


@pytest.mark.parametrize(
    "header,expected",
    [
        ("127.0.0.1:8141", "127.0.0.1"),
        ("127.0.0.1", "127.0.0.1"),
        ("localhost:8141", "localhost"),
        ("LOCALHOST", "localhost"),  # case-folded before the allowlist check
        ("[::1]:8141", "::1"),  # IPv6 literal with port
        ("[::1]", "::1"),
        ("evil.com:8141", "evil.com"),
        ("  127.0.0.1:8141  ", "127.0.0.1"),
        ("", ""),
    ],
)
def test_host_only_strips_port_and_folds_case(header, expected):
    assert caller_auth._host_only(header) == expected


def test_every_loopback_spelling_is_allowed():
    """A wrong verdict here either breaks the product or opens the port."""
    for host in ("127.0.0.1", "localhost", "::1"):
        assert host in caller_auth.LOOPBACK_HOSTS
    assert "evil.com" not in caller_auth.LOOPBACK_HOSTS


# -- config_from_env: fail loudly, never auth-off by accident ----------------


def test_token_comes_from_the_plain_env_var(monkeypatch):
    monkeypatch.delenv(_FILE_VAR, raising=False)
    monkeypatch.setenv(_ENV_VAR, _TOKEN)
    assert _from_env().token == _TOKEN


def test_no_env_vars_means_no_token(monkeypatch):
    monkeypatch.delenv(_FILE_VAR, raising=False)
    monkeypatch.delenv(_ENV_VAR, raising=False)
    assert _from_env().token is None


def test_the_secret_file_is_preferred_over_the_bare_env_var(monkeypatch, tmp_path):
    """#2149: the file leg exists so the secret never sits in the environment."""
    secret = tmp_path / "token"
    secret.write_text("from-the-file\n", encoding="utf-8")
    monkeypatch.setenv(_FILE_VAR, str(secret))
    monkeypatch.setenv(_ENV_VAR, "from-the-env")
    assert _from_env().token == "from-the-file"


def test_a_missing_secret_file_is_a_loud_startup_error(monkeypatch, tmp_path):
    """Never a silent auth-off: a set-but-broken path must refuse to start."""
    monkeypatch.setenv(_FILE_VAR, str(tmp_path / "does-not-exist"))
    monkeypatch.delenv(_ENV_VAR, raising=False)
    with pytest.raises(RuntimeError, match="cannot be read"):
        _from_env()


def test_an_empty_secret_file_is_a_loud_startup_error(monkeypatch, tmp_path):
    secret = tmp_path / "token"
    secret.write_text("   \n", encoding="utf-8")
    monkeypatch.setenv(_FILE_VAR, str(secret))
    monkeypatch.delenv(_ENV_VAR, raising=False)
    with pytest.raises(RuntimeError, match="empty"):
        _from_env()


# -- exempt paths ------------------------------------------------------------


def test_exempt_paths_are_scoped_to_the_active_config():
    caller_auth.configure(
        CallerAuthConfig(token=_TOKEN, exempt_paths=frozenset({"/health"}))
    )
    assert caller_auth.is_exempt_path("/health") is True
    assert caller_auth.is_exempt_path("/v1/gaia/query") is False


def test_nothing_is_exempt_when_auth_was_never_configured():
    """A process that never configured auth has no policy to exempt against."""
    assert caller_auth.get_config() is None
    assert caller_auth.is_exempt_path("/health") is False


# -- token minting -----------------------------------------------------------


def test_generated_tokens_are_unguessable_and_unique():
    tokens = {caller_auth.generate_session_token() for _ in range(100)}
    assert len(tokens) == 100
    assert all(len(t) >= 40 for t in tokens)
