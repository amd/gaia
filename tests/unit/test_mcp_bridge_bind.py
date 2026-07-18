#!/usr/bin/env python
#
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Bind-host resolution for the MCP bridge.

The bridge is unauthenticated, so a request for "localhost" must never
silently widen to a wildcard bind. Binding all interfaces is explicit
opt-in only, and must be loudly logged.
"""

import sys

import pytest

from gaia.mcp.mcp_bridge import resolve_bind_host


class TestResolveBindHost:
    def test_localhost_resolves_to_ipv4_loopback_on_non_windows(self, monkeypatch):
        """localhost must bind the IPv4 loopback, never the wildcard address."""
        monkeypatch.setattr(sys, "platform", "linux")
        assert resolve_bind_host("localhost") == "127.0.0.1"

    def test_localhost_resolves_to_ipv4_loopback_on_macos(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        assert resolve_bind_host("localhost") == "127.0.0.1"

    def test_localhost_kept_on_windows(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        assert resolve_bind_host("localhost") == "localhost"

    def test_localhost_never_resolves_to_wildcard(self, monkeypatch):
        """Regression guard: the old code rebound localhost to 0.0.0.0."""
        for platform in ("linux", "darwin", "win32"):
            monkeypatch.setattr(sys, "platform", platform)
            assert resolve_bind_host("localhost") != "0.0.0.0"

    def test_explicit_loopback_passes_through(self):
        assert resolve_bind_host("127.0.0.1") == "127.0.0.1"

    @pytest.mark.parametrize("wildcard", ["0.0.0.0", "::"])
    def test_explicit_wildcard_is_honored_but_warns(self, wildcard, caplog):
        """Binding all interfaces is allowed only as explicit opt-in, with a loud warning."""
        with caplog.at_level("WARNING", logger="gaia.mcp.mcp_bridge"):
            assert resolve_bind_host(wildcard) == wildcard
        assert any(
            "unauthenticated" in record.getMessage().lower()
            for record in caplog.records
        ), "expected a warning that the unauthenticated bridge is network-exposed"

    def test_specific_host_passes_through_without_warning(self, caplog):
        with caplog.at_level("WARNING", logger="gaia.mcp.mcp_bridge"):
            assert resolve_bind_host("192.168.1.50") == "192.168.1.50"
        assert not caplog.records
