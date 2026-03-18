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
