# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
The in-package runnable server (``gaia_agent_email.server``) and its ``serve``
CLI — the fast dev-iteration entry point.

Guards:
- **Parity**: the ``packaging/server.py`` freeze shim serves the exact same
  routes as the in-package ``build_app()`` (the shim re-exports it), so the
  frozen binary and a source ``uvicorn gaia_agent_email.server:app`` can't drift.
- **CLI**: ``serve`` is the default subcommand, ``--print-openapi`` emits the
  contract, ``--port 4001`` is rejected (reserved), and ``--reload`` / ``--dev``
  drive uvicorn's reloader with an import-string app (never a pre-built object).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

pytest.importorskip("gaia_agent_email")
pytest.importorskip("fastapi")

from gaia_agent_email import server  # noqa: E402


def _route_paths(app) -> set[str]:
    return {getattr(r, "path", None) for r in app.routes}


def _load_packaging_shim():
    """Load ``packaging/server.py`` by path, exactly as the freeze + test_caller_auth do."""
    path = Path(__file__).resolve().parents[1] / "packaging" / "server.py"
    spec = importlib.util.spec_from_file_location("email_sidecar_shim_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_shim_serves_identical_routes_to_in_package_builder():
    pkg_routes = _route_paths(server.build_app())
    shim = _load_packaging_shim()
    shim_routes = _route_paths(shim.build_app())
    assert shim_routes == pkg_routes
    # And the shim re-exports the very same builder, not a fork.
    assert shim.build_app is server.build_app
    # Sanity: the canonical surfaces are present.
    for path in ("/health", "/version", "/v1/email/triage"):
        assert path in pkg_routes


def test_print_openapi_emits_the_contract(capsys):
    rc = server.main(["serve", "--print-openapi"])
    assert rc == 0
    doc = json.loads(capsys.readouterr().out)
    assert "/v1/email/triage" in doc["paths"]


def test_serve_is_the_default_subcommand(capsys):
    # No subcommand → treated as `serve`, so bare flags still work.
    rc = server.main(["--print-openapi"])
    assert rc == 0
    assert "openapi" in capsys.readouterr().out


def test_port_4001_is_rejected():
    # 4001 is reserved; the parser must refuse it (SystemExit from parser.error).
    with pytest.raises(SystemExit):
        server.main(["serve", "--port", "4001"])


def test_plain_serve_runs_the_prebuilt_app_object(monkeypatch):
    captured = {}

    def fake_run(app_arg, **kwargs):
        captured["app"] = app_arg
        captured["kwargs"] = kwargs

    monkeypatch.setattr("uvicorn.run", fake_run)
    rc = server.main(["serve", "--host", "127.0.0.1", "--port", "8131"])
    assert rc == 0
    # Non-reload path hands uvicorn the pre-built app object, not an import string.
    assert captured["app"] is server.app
    assert captured["kwargs"].get("reload") in (None, False)


def test_reload_uses_import_string_and_watches_the_package(monkeypatch):
    captured = {}

    def fake_run(app_arg, **kwargs):
        captured["app"] = app_arg
        captured["kwargs"] = kwargs

    monkeypatch.setattr("uvicorn.run", fake_run)
    rc = server.main(["serve", "--reload", "--reload-dir", "/tmp/core-src"])
    assert rc == 0
    # Reload REQUIRES an import-string app so the worker can re-import on edit.
    assert captured["app"] == "gaia_agent_email.server:app"
    assert captured["kwargs"]["reload"] is True
    reload_dirs = captured["kwargs"]["reload_dirs"]
    pkg_dir = str(Path(server.__file__).resolve().parent)
    assert pkg_dir in reload_dirs
    assert "/tmp/core-src" in reload_dirs


def test_dev_implies_reload(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "uvicorn.run", lambda app_arg, **kw: captured.update(app=app_arg, kw=kw)
    )
    rc = server.main(["serve", "--dev"])
    assert rc == 0
    assert captured["app"] == "gaia_agent_email.server:app"
    assert captured["kw"]["reload"] is True
