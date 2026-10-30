# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA's processes verify HTTPS against the OS certificate store.

Behind a TLS-inspecting proxy the only root that works is the one IT installed
in the OS store; certifi alone fails every hub/model/web request. These tests
pin that every process entry point switches to the OS store before doing any
work, and that a broken ``truststore`` is reported rather than swallowed.
"""

from __future__ import annotations

import sys
import types
from unittest import mock

import pytest

import gaia.tls as tls


class _Stop(Exception):
    """Raised by the patched injector so an entry point stops right there."""


@pytest.fixture
def fresh_tls(monkeypatch):
    monkeypatch.setattr(tls, "_injected", False)
    return tls


def _fake_truststore(inject):
    module = types.ModuleType("truststore")
    module.inject_into_ssl = inject
    return module


def test_injects_truststore_once(fresh_tls, monkeypatch):
    inject = mock.Mock()
    monkeypatch.setitem(sys.modules, "truststore", _fake_truststore(inject))

    assert fresh_tls.use_os_trust_store() is True
    assert fresh_tls.use_os_trust_store() is True
    inject.assert_called_once_with()


def test_missing_truststore_warns_on_stderr_and_keeps_default(
    fresh_tls, monkeypatch, capsys
):
    # A None entry makes `import truststore` raise ImportError.
    monkeypatch.setitem(sys.modules, "truststore", None)

    assert fresh_tls.use_os_trust_store() is False

    captured = capsys.readouterr()
    # stdout is the event channel of the stdio transports.
    assert captured.out == ""
    message = captured.err
    assert "system certificate store" in message
    assert "CERTIFICATE_VERIFY_FAILED" in message
    assert "pip install --upgrade 'truststore>=0.10.0'" in message
    assert fresh_tls._injected is False


def test_real_injection_uses_truststore_context(fresh_tls, monkeypatch):
    """The real package, end to end: ssl.SSLContext becomes truststore's."""
    import ssl

    import requests.adapters

    truststore = pytest.importorskip("truststore")
    original = ssl.SSLContext
    # extract_from_ssl does not undo the requests patch; restore it ourselves.
    monkeypatch.setattr(
        requests.adapters,
        "_preloaded_ssl_context",
        getattr(requests.adapters, "_preloaded_ssl_context", None),
        raising=False,
    )
    try:
        assert fresh_tls.use_os_trust_store() is True
        assert ssl.SSLContext is truststore.SSLContext
        assert isinstance(ssl.create_default_context(), truststore.SSLContext)
    finally:
        truststore.extract_from_ssl()
        assert ssl.SSLContext is original


def test_gaia_cli_injects_before_parsing(monkeypatch):
    import gaia.cli as cli

    monkeypatch.setattr(cli, "use_os_trust_store", mock.Mock(side_effect=_Stop))
    monkeypatch.setattr(cli, "build_parser", mock.Mock(side_effect=AssertionError))
    with pytest.raises(_Stop):
        cli.main()


@pytest.mark.parametrize(
    "module_name, entry",
    [
        ("gaia.ui.server", "main"),
        ("gaia.daemon.server", "run"),
        ("gaia.mcp.mcp_bridge", "main"),
    ],
)
def test_process_entry_points_inject(monkeypatch, module_name, entry):
    module = pytest.importorskip(module_name)
    monkeypatch.setattr(tls, "use_os_trust_store", mock.Mock(side_effect=_Stop))
    with pytest.raises(_Stop):
        getattr(module, entry)()


@pytest.mark.parametrize("module_name", ["gaia_agent.entry", "gaia_agent_email.server"])
def test_hub_agent_sidecars_inject(monkeypatch, module_name):
    module = pytest.importorskip(module_name)
    monkeypatch.setattr(tls, "use_os_trust_store", mock.Mock(side_effect=_Stop))
    with pytest.raises(_Stop):
        module.main(["--help"])
