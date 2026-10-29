# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The autonomy routes report a connection being saved as a retryable 503.

Matches the UI router's table: a busy connection is transient, so it must not
surface as a textless 500 (or as a 401 "reconnect").
"""

from __future__ import annotations

from gaia_agent_email.agent_routes import _connectors_error_to_http

from gaia.connectors.errors import ConnectionBusyError


def test_a_busy_connection_maps_to_503_with_retry_after():
    http = _connectors_error_to_http(ConnectionBusyError("google"))

    assert http.status_code == 503
    assert http.headers["Retry-After"] == "1"
    assert "Retry" in http.detail
