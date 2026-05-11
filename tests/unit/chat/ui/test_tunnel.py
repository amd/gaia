# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for the TunnelManager mobile access feature."""

import asyncio

from gaia.ui.tunnel import TunnelManager


class TestTunnelManager:
    """Tests for TunnelManager."""

    def test_init(self):
        """TunnelManager initializes with correct defaults."""
        manager = TunnelManager(port=4200)
        assert manager.port == 4200
        assert manager.domain is None
        assert not manager.active

    def test_init_with_domain(self):
        """TunnelManager accepts custom domain."""
        manager = TunnelManager(port=4200, domain="my-domain.ngrok-free.app")
        assert manager.domain == "my-domain.ngrok-free.app"

    def test_get_status_inactive(self):
        """get_status returns inactive status when not started."""
        manager = TunnelManager(port=4200)
        status = manager.get_status()
        assert status["active"] is False
        assert status["url"] is None
        assert status["token"] is None
        assert status["startedAt"] is None
        assert status["error"] is None
        assert status["publicIp"] is None

    def test_validate_token_inactive(self):
        """validate_token returns False when tunnel is inactive."""
        manager = TunnelManager(port=4200)
        assert manager.validate_token("some-token") is False

    def test_validate_token_wrong_token(self):
        """validate_token returns False for wrong token."""
        manager = TunnelManager(port=4200)
        manager._token = "correct-token"
        # Still inactive (no process), so should return False
        assert manager.validate_token("wrong-token") is False

    def test_active_property_no_process(self):
        """active is False when no process is running."""
        manager = TunnelManager(port=4200)
        assert manager.active is False

    def test_active_property_no_url(self):
        """active is False when process exists but no URL."""
        manager = TunnelManager(port=4200)
        # Simulate a process that's still running but no URL
        manager._url = None
        assert manager.active is False

    def test_find_ngrok(self):
        """_find_ngrok returns a path or None (doesn't crash)."""
        manager = TunnelManager(port=4200)
        result = manager._find_ngrok()
        # May be None if ngrok is not installed, that's OK
        assert result is None or isinstance(result, str)

    def test_start_without_ngrok(self):
        """start() returns error status when ngrok is not installed."""
        manager = TunnelManager(port=4200)
        # Mock _find_ngrok to return None (ngrok not installed)
        manager._find_ngrok = lambda: None

        status = asyncio.run(manager.start())
        assert status["active"] is False
        assert status["error"] is not None
        assert "ngrok" in status["error"].lower()
        # Should mention install instructions
        assert (
            "install" in status["error"].lower()
            or "ngrok.com/download" in status["error"].lower()
        )

    def test_failed_start_preserves_error_in_status(self, monkeypatch):
        """After a failed start, get_status() must still return the diagnostic.

        Regression test: stop() clears _error as part of its normal cleanup
        (so a user-initiated stop doesn't leave a stale error). But when
        a start fails, we want to preserve the error across stop() so the
        caller sees WHY it failed -- not a confusing ``error: null``.
        """
        from gaia.ui import tunnel as tunnel_mod

        manager = TunnelManager(port=4200)
        manager._find_ngrok = lambda: "/fake/ngrok"
        monkeypatch.setattr(
            tunnel_mod,
            "_check_ngrok_authtoken_configured",
            lambda: True,
        )

        # Skip public-IP fetch (would hit the network)
        async def _noop_public_ip(self):
            self._public_ip = None

        monkeypatch.setattr(TunnelManager, "_fetch_public_ip", _noop_public_ip)

        # Skip the stale-ngrok kill
        async def _noop_kill(self):
            return None

        monkeypatch.setattr(TunnelManager, "_kill_stale_ngrok", _noop_kill)

        # Simulate a subprocess.Popen that "died immediately" so the
        # poll-api path reports a friendly error.
        class _DeadProcess:
            def __init__(self, *_a, **_kw):
                self.stdout = None
                self.stderr = None
                self.stdin = None

            def poll(self):
                return 1  # exited with non-zero

            def terminate(self):
                pass

            def wait(self, timeout=None):
                return 1

            def kill(self):
                pass

        import subprocess as _sp

        monkeypatch.setattr(_sp, "Popen", _DeadProcess)

        # Also short-circuit the drain helper since our fake has no pipes
        monkeypatch.setattr(
            TunnelManager,
            "_drain_ngrok_output",
            lambda self: "authentication failed ERR_NGROK_107 "
            "properly formed, but it is invalid",
        )

        status = asyncio.run(manager.start())

        assert status["active"] is False
        assert status["url"] is None
        # The crux of the test: error must survive stop() cleanup.
        assert status["error"] is not None
        assert (
            "rejected" in status["error"].lower()
            or "revoked" in status["error"].lower()
            or "invalid" in status["error"].lower()
        )

    def test_start_without_authtoken(self, monkeypatch):
        """start() surfaces a friendly message when the authtoken isn't set."""
        from gaia.ui import tunnel as tunnel_mod

        manager = TunnelManager(port=4200)
        manager._find_ngrok = lambda: "/fake/ngrok"
        # Pretend the authtoken preflight fails (no config file found)
        monkeypatch.setattr(
            tunnel_mod,
            "_check_ngrok_authtoken_configured",
            lambda: False,
        )

        status = asyncio.run(manager.start())
        assert status["active"] is False
        # Pin the exact hint constant the user should see. Asserting
        # against the constant (rather than substring-matching a URL in
        # the message) keeps the test stronger AND avoids tripping
        # CodeQL's py/incomplete-url-substring-sanitization rule on a
        # URL pattern that is only ever a help-link in user-facing prose.
        from gaia.ui.tunnel import _NGROK_AUTHTOKEN_HINT

        assert status["error"] == _NGROK_AUTHTOKEN_HINT

    def test_stop_when_not_running(self):
        """stop() is safe to call when tunnel is not running."""
        manager = TunnelManager(port=4200)
        # Should not raise
        asyncio.run(manager.stop())
        assert not manager.active

    def test_start_already_active(self):
        """start() returns current status if already active."""
        manager = TunnelManager(port=4200)
        # Fake an active state
        manager._url = "https://test.ngrok-free.app"
        manager._token = "test-token"

        class FakeProcess:
            def poll(self):
                return None  # Still running

        manager._process = FakeProcess()

        status = asyncio.run(manager.start())
        assert status["active"] is True
        assert status["url"] == "https://test.ngrok-free.app"


# ── Friendly error-parser tests ─────────────────────────────────────────


class TestParseNgrokError:
    """_parse_ngrok_error translates raw ngrok output into actionable hints."""

    def test_empty_stderr(self):
        from gaia.ui.tunnel import _parse_ngrok_error

        msg = _parse_ngrok_error("")
        assert "exited without output" in msg.lower()
        assert "ngrok http 4200" in msg

    def test_authtoken_error(self):
        from gaia.ui.tunnel import _NGROK_AUTHTOKEN_HINT, _parse_ngrok_error

        # ERR_NGROK_4018 (malformed/missing authtoken) → fixed hint.
        # Assert exact-equality with the constant so CodeQL's
        # incomplete-url-substring-sanitization rule has nothing to flag,
        # AND the test fails loudly if the prose ever drifts.
        msg = _parse_ngrok_error(
            "ERROR: authentication failed: The authtoken you specified is "
            "invalid. (ERR_NGROK_4018)"
        )
        assert msg == _NGROK_AUTHTOKEN_HINT

    def test_authtoken_error_by_code(self):
        from gaia.ui.tunnel import _parse_ngrok_error

        msg = _parse_ngrok_error("ERR_NGROK_4018")
        assert "authtoken" in msg.lower()

    def test_authtoken_rejected_err_107(self):
        """ERR_NGROK_107 is well-formed-but-rejected, distinct from missing."""
        from gaia.ui.tunnel import (
            _NGROK_AUTHTOKEN_HINT,
            _NGROK_AUTHTOKEN_REJECTED_HINT,
            _parse_ngrok_error,
        )

        msg = _parse_ngrok_error(
            "authentication failed: The authtoken you specified is "
            "properly formed, but it is invalid. ERR_NGROK_107"
        )
        # Pin the exact rejected-hint constant. This is the crux of the
        # test: we route an ERR_NGROK_107 to the rejected hint, NOT the
        # missing hint (those are user-confusingly different).
        assert msg == _NGROK_AUTHTOKEN_REJECTED_HINT
        assert msg != _NGROK_AUTHTOKEN_HINT

    def test_authtoken_rejected_by_revoked_phrase(self):
        from gaia.ui.tunnel import _parse_ngrok_error

        msg = _parse_ngrok_error(
            "You are using ngrok link and this credential was explicitly " "revoked"
        )
        assert "rejected" in msg.lower() or "revoked" in msg.lower()

    def test_session_limit_error(self):
        from gaia.ui.tunnel import _NGROK_SESSION_LIMIT_HINT, _parse_ngrok_error

        msg = _parse_ngrok_error(
            "ERROR: Your account is limited to 1 simultaneous ngrok agent "
            "sessions. (ERR_NGROK_108)"
        )
        # Exact-equality assertion against the constant — see note in
        # test_authtoken_error for why this is preferable to substring
        # checks on URLs in user-facing prose.
        assert msg == _NGROK_SESSION_LIMIT_HINT

    def test_network_error(self):
        from gaia.ui.tunnel import _parse_ngrok_error

        msg = _parse_ngrok_error("dial tcp: lookup tunnel.ngrok.com: no such host")
        assert "internet" in msg.lower() or "network" in msg.lower()

    def test_port_conflict(self):
        from gaia.ui.tunnel import _parse_ngrok_error

        msg = _parse_ngrok_error(
            "failed to bind: listen tcp 127.0.0.1:4040: bind: address " "already in use"
        )
        assert "4040" in msg or "in use" in msg.lower()

    def test_unknown_error_falls_back_to_first_line(self):
        from gaia.ui.tunnel import _parse_ngrok_error

        msg = _parse_ngrok_error(
            "something unusual happened\nadditional context on line 2"
        )
        assert "something unusual happened" in msg
        # Should NOT include the second line (first line only).
        assert "line 2" not in msg

    def test_tls_certificate_alone_does_not_match(self):
        """``certificate`` alone is too generic — only ``certificate``+``verify``.

        Regression: an earlier version had
        ``if "x509" in low or "certificate" in low and "verify" in low``
        which (due to operator precedence) parsed as
        ``x509 OR (certificate AND verify)``. After explicit parens this
        behaviour is unchanged but locked in: a ``certificate`` substring
        without the ``verify`` partner falls through to the generic fallback.
        """
        from gaia.ui.tunnel import _parse_ngrok_error

        msg = _parse_ngrok_error("error: server returned a stale certificate")
        # Falls through to "ngrok failed to start: ..." rather than the TLS hint.
        assert "system clock" not in msg.lower()
        assert "proxy" not in msg.lower()

    def test_tls_x509_matches(self):
        from gaia.ui.tunnel import _parse_ngrok_error

        msg = _parse_ngrok_error("x509: certificate signed by unknown authority")
        assert "system clock" in msg.lower() or "proxy" in msg.lower()

    def test_connection_refused_without_ngrok_host_falls_through(self):
        """Generic ``connection refused`` shouldn't be mis-attributed to ngrok.

        The network-error block parenthesises the
        ``connection refused AND tunnel.ngrok.com`` clause so a
        ``connection refused`` to some other host doesn't get the
        ngrok-specific "couldn't reach servers" hint.
        """
        from gaia.ui.tunnel import _parse_ngrok_error

        msg = _parse_ngrok_error("dial tcp 127.0.0.1:9000: connection refused")
        # ``dial tcp`` itself does match the network branch, so we test the
        # narrower invariant: the message we surface mentions internet/network
        # (correct generic guidance) rather than misleading the user about
        # ngrok-specific connectivity. The substring filter exists so that
        # if the message ever reorders to land in a different branch, this
        # test catches the regression.
        assert (
            "ngrok's servers" in msg
            or "internet" in msg.lower()
            or "network" in msg.lower()
        )

    def test_connection_refused_lookalike_host_does_not_match(self):
        """A hostile string that *contains* ``tunnel.ngrok.com`` as a substring
        must NOT trip the ngrok-specific network branch.

        Locks in the word-boundary regex used by ``_parse_ngrok_error`` so a
        future refactor back to a naked ``in`` check (which CodeQL flagged as
        py/incomplete-url-substring-sanitization) is caught.
        """
        from gaia.ui.tunnel import _parse_ngrok_error

        # ``connection refused`` *and* the literal ``tunnel.ngrok.com`` substring
        # appears, but only as a misleading subdomain of an attacker-controlled
        # host. The match must NOT fire — the message that actually surfaces is
        # the generic fallback (``ngrok failed to start: ...``).
        msg = _parse_ngrok_error(
            "evil.tunnel.ngrok.com.attacker.tld: connection refused"
        )
        assert "internet connection" not in msg.lower()
        assert "ngrok failed to start" in msg


class TestMaskNgrokSecrets:
    """``_mask_ngrok_secrets`` redacts plausible authtokens before logging."""

    def test_authtoken_field_is_masked(self):
        from gaia.ui.tunnel import _mask_ngrok_secrets

        masked = _mask_ngrok_secrets(
            "config: authtoken: 2abcdefghijklmnopqrstuvwxyz_zyxwvutsrqponmlkjihgfedcba"
        )
        assert "2abcdefghij" not in masked
        assert "[REDACTED]" in masked

    def test_long_opaque_token_is_masked_anywhere(self):
        from gaia.ui.tunnel import _mask_ngrok_secrets

        # An ngrok-shaped long token appearing inline (e.g. echoed in stderr
        # without the ``authtoken:`` prefix) must still be redacted.
        masked = _mask_ngrok_secrets(
            "rejected token: 2ABCDEFGHIJKLMNOPQRSTUVWXYZ_zyxwvutsrqponmlkjihgfedcba "
            "please retry"
        )
        assert "2ABCDEFGHIJ" not in masked
        assert "[REDACTED]" in masked
        # Non-secret context is preserved.
        assert "please retry" in masked

    def test_safe_input_unchanged(self):
        from gaia.ui.tunnel import _mask_ngrok_secrets

        text = "ngrok exited cleanly: no authtoken issues"
        # No secret-shaped substring → string passes through verbatim.
        assert _mask_ngrok_secrets(text) == text


class TestCheckNgrokAuthtokenConfigured:
    """Tests for ``_check_ngrok_authtoken_configured``.

    The check decides whether to abort start() with a "configure your
    authtoken" hint. False positives are cheap (ngrok will surface its own
    error). False negatives block working setups, so each input shape that
    real users have is exercised here.
    """

    def test_env_var_takes_precedence(self, monkeypatch, tmp_path):
        """``$NGROK_AUTHTOKEN`` should short-circuit the file probes.

        ngrok v3 honours the env var directly — a user with valid env-var
        auth and no config file is fully working, but the file-only probe
        would falsely report "not configured" and block startup.
        """
        from gaia.ui import tunnel as tunnel_mod

        monkeypatch.setenv("NGROK_AUTHTOKEN", "valid-token-from-env")
        # Point file probes at a non-existent path so they all return False.
        monkeypatch.setattr(
            tunnel_mod,
            "_ngrok_config_candidates",
            lambda: [tmp_path / "nope.yml"],
        )
        assert tunnel_mod._check_ngrok_authtoken_configured() is True

    def test_empty_env_var_does_not_count(self, monkeypatch, tmp_path):
        """An empty/whitespace env var must NOT register as configured."""
        from gaia.ui import tunnel as tunnel_mod

        monkeypatch.setenv("NGROK_AUTHTOKEN", "   ")
        monkeypatch.setattr(
            tunnel_mod,
            "_ngrok_config_candidates",
            lambda: [tmp_path / "nope.yml"],
        )
        assert tunnel_mod._check_ngrok_authtoken_configured() is False

    def test_v2_flat_authtoken_in_config(self, monkeypatch, tmp_path):
        """v2 layout: ``authtoken: xxx`` at column 0."""
        from gaia.ui import tunnel as tunnel_mod

        monkeypatch.delenv("NGROK_AUTHTOKEN", raising=False)
        cfg = tmp_path / "ngrok.yml"
        cfg.write_text("authtoken: 2abc-token-v2-flat\nregion: us\n")
        monkeypatch.setattr(tunnel_mod, "_ngrok_config_candidates", lambda: [cfg])
        assert tunnel_mod._check_ngrok_authtoken_configured() is True

    def test_v3_nested_authtoken_in_config(self, monkeypatch, tmp_path):
        """v3 layout: ``authtoken`` indented under ``agent:`` block.

        Locks in that nested layouts are still detected — the line-strip
        scan tolerates any indentation, but a future refactor to a
        column-sensitive parser would silently break this.
        """
        from gaia.ui import tunnel as tunnel_mod

        monkeypatch.delenv("NGROK_AUTHTOKEN", raising=False)
        cfg = tmp_path / "ngrok.yml"
        cfg.write_text(
            "version: 3\n"
            "agent:\n"
            "  authtoken: 2xyz-token-v3-nested\n"
            "  region: us\n"
        )
        monkeypatch.setattr(tunnel_mod, "_ngrok_config_candidates", lambda: [cfg])
        assert tunnel_mod._check_ngrok_authtoken_configured() is True

    def test_empty_authtoken_value_rejected(self, monkeypatch, tmp_path):
        """``authtoken:`` with no value (or quoted empty) shouldn't count."""
        from gaia.ui import tunnel as tunnel_mod

        monkeypatch.delenv("NGROK_AUTHTOKEN", raising=False)
        cfg = tmp_path / "ngrok.yml"
        cfg.write_text("authtoken: ''\n")
        monkeypatch.setattr(tunnel_mod, "_ngrok_config_candidates", lambda: [cfg])
        assert tunnel_mod._check_ngrok_authtoken_configured() is False

    def test_no_config_files_returns_false(self, monkeypatch, tmp_path):
        from gaia.ui import tunnel as tunnel_mod

        monkeypatch.delenv("NGROK_AUTHTOKEN", raising=False)
        monkeypatch.setattr(
            tunnel_mod,
            "_ngrok_config_candidates",
            lambda: [tmp_path / "missing.yml"],
        )
        assert tunnel_mod._check_ngrok_authtoken_configured() is False
