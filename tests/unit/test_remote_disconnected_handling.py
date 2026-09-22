# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""``http.client.RemoteDisconnected`` must not escape an ``except URLError``.

It is a ``ConnectionError``, not a ``urllib.error.URLError``: ``urllib`` only
wraps errors raised by ``h.request(...)``, so anything ``h.getresponse()``
raises propagates unwrapped (see ``AbstractHTTPHandler.do_open``). Every handler
that catches only ``URLError`` therefore misses a transient connection drop and
reports it as a broken link, a missing asset, or a failed test (#2925, #2927,
#3802).

The AST guard at the bottom is what keeps this from recurring: it fails on any
new ``except URLError`` site that does not also handle the ``ConnectionError``
family in the same ``try``.
"""

import ast
import http.client
import socket
import sys
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

from gaia.installer.lemonade_installer import LemonadeAssetError, LemonadeInstaller
from gaia.mcp.client.transports.http import HTTPTransport

REPO_ROOT = Path(__file__).resolve().parents[2]
SCANNED_DIRS = ("src", "util", "tests", "hub")

# Names that count as handling a raw transport drop. ``Exception`` is
# deliberately absent - a blanket catch is not an audit (CLAUDE.md, fail loudly).
TRANSPORT_NAMES = {"ConnectionError", "OSError", "ConnectionResetError"}


def _disconnect():
    return http.client.RemoteDisconnected(
        "Remote end closed connection without response"
    )


def test_remote_disconnected_is_not_a_urlerror():
    """The premise: catching URLError alone cannot see a dropped connection."""
    assert not issubclass(http.client.RemoteDisconnected, urllib.error.URLError)
    assert issubclass(http.client.RemoteDisconnected, ConnectionError)


def test_mcp_http_transport_reports_a_drop_as_runtimeerror():
    transport = HTTPTransport("http://localhost:8765")
    with patch("urllib.request.urlopen", side_effect=_disconnect()):
        with pytest.raises(RuntimeError, match="dropped mid-flight"):
            transport.send_request("tools/list")


def test_installer_treats_a_drop_as_non_definitive():
    """A blip must not look like a renamed upstream asset."""
    installer = LemonadeInstaller()
    with patch("urllib.request.urlopen", side_effect=_disconnect()):
        with pytest.raises(LemonadeAssetError) as excinfo:
            installer.verify_download_url("https://example.invalid/asset.exe")
    assert excinfo.value.definitive is False


def test_eval_preflight_reports_a_drop_instead_of_crashing():
    from gaia.eval.runner import preflight_check

    with patch("urllib.request.urlopen", side_effect=_disconnect()):
        errors = preflight_check("http://localhost:4200")
    assert any("Agent UI not reachable" in e for e in errors)


def _load_util_script(name):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        f"_{name}", REPO_ROOT / "util" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_doc_link_checker_warns_on_a_drop_instead_of_calling_it_broken():
    module = _load_util_script("check_doc_links")

    with patch("urllib.request.urlopen", side_effect=_disconnect()):
        status, detail = module.check_external_link("https://example.invalid/page")
    assert status == "warning", detail


def test_pypi_fetch_retries_a_drop_but_fails_dns_immediately():
    """The core-API audit must survive a blip, but still name a DNS failure."""
    module = _load_util_script("check_component_core_api")
    module._FETCH_BACKOFF_SECONDS = 0

    attempts = []

    def drop(*_args, **_kwargs):
        attempts.append(1)
        raise _disconnect()

    with patch("urllib.request.urlopen", side_effect=drop):
        with pytest.raises(module.CheckError, match="could not reach PyPI"):
            module._fetch(module.PYPI_JSON_URL, timeout=5)
    assert len(attempts) == module._FETCH_ATTEMPTS

    attempts.clear()

    def dns_failure(*_args, **_kwargs):
        attempts.append(1)
        raise urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))

    with patch("urllib.request.urlopen", side_effect=dns_failure):
        with pytest.raises(module.CheckError, match="could not resolve pypi.org"):
            module._fetch(module.PYPI_JSON_URL, timeout=5)
    assert len(attempts) == 1, "a DNS failure is a real signal - do not retry it"


def _catches_urlerror(handler: ast.ExceptHandler) -> bool:
    return any(_name_of(node) == "URLError" for node in _handler_types(handler))


def _catches_transport(handler: ast.ExceptHandler) -> bool:
    return any(_name_of(node) in TRANSPORT_NAMES for node in _handler_types(handler))


def _handler_types(handler: ast.ExceptHandler):
    if handler.type is None:
        return []
    if isinstance(handler.type, ast.Tuple):
        return handler.type.elts
    return [handler.type]


def _name_of(node: ast.AST) -> str:
    """Last segment of the caught name: ``urllib.error.URLError`` -> ``URLError``."""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _unguarded_sites():
    sites = []
    for directory in SCANNED_DIRS:
        for path in sorted((REPO_ROOT / directory).rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Try):
                    continue
                if not any(_catches_urlerror(h) for h in node.handlers):
                    continue
                if any(_catches_transport(h) for h in node.handlers):
                    continue
                sites.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    return sites


def test_every_urlerror_handler_also_handles_a_dropped_connection():
    """No new ``except URLError`` may ship blind to RemoteDisconnected."""
    unguarded = _unguarded_sites()
    assert not unguarded, (
        "These try blocks catch urllib.error.URLError but not the ConnectionError "
        "family, so http.client.RemoteDisconnected escapes them and a transient "
        "network drop surfaces as a hard failure. Add ConnectionError (or OSError) "
        "to the handler:\n  " + "\n  ".join(unguarded)
    )
