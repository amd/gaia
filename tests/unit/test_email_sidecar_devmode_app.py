# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Dev mode needs uvicorn's import-string form, which requires a module-level
``app`` in ``packaging/server.py``.

Resolution detail that bites: the email package's ``packaging/`` dir collides by
name with the PyPI ``packaging`` library, so ``packaging.server:app`` resolves to
the wrong ``packaging`` and fails. Dev mode therefore loads the file as the
TOP-LEVEL module ``server`` via uvicorn's ``--app-dir <email>/packaging`` (which
puts the ``packaging/`` dir itself on ``sys.path``) — no ``packaging`` package
import, no collision. This test mirrors that resolution in a fresh subprocess.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

EMAIL_PKG = Path(__file__).resolve().parents[2] / "hub" / "agents" / "email" / "python"
PACKAGING_DIR = EMAIL_PKG / "packaging"


@pytest.mark.skipif(
    importlib.util.find_spec("gaia_agent_email") is None,
    reason="email agent wheel not installed (uv pip install -e hub/agents/email/python)",
)
def test_server_app_dir_import_string_resolves_to_fastapi_app():
    # Mirror `uvicorn server:app --app-dir <email>/packaging`: put the packaging
    # dir on sys.path and import the file as top-level module `server`.
    # Use TestClient HTTP probes instead of app.routes introspection: older
    # Starlette versions expose _IncludedRouter objects without a .path attribute,
    # so reachability (status != 404) is the robust cross-version assertion.
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "import server as s;"
        "from fastapi import FastAPI;"
        "from fastapi.testclient import TestClient;"
        "assert hasattr(s, 'app'), 'module-level app missing for uvicorn --reload';"
        "assert isinstance(s.app, FastAPI), type(s.app);"
        "client = TestClient(s.app, raise_server_exceptions=False);"
        "h = client.get('/health');"
        "assert h.status_code != 404, f'/health returned 404: {h.text}';"
        "t = client.post('/v1/email/triage', json={});"
        "assert t.status_code != 404, f'/v1/email/triage returned 404: {t.text}';"
        "print('OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(PACKAGING_DIR)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "OK" in result.stdout


@pytest.mark.skipif(
    importlib.util.find_spec("gaia_agent_email") is None,
    reason="email agent wheel not installed",
)
def test_server_main_serves_the_single_module_app_openapi():
    # The frozen `main()` must reuse the module-level `app` (no second build).
    # Assert both that --print-openapi works and that its schema matches the
    # module-level app's, proving they are the same app instance's contract.
    code = (
        "import sys, io, json, contextlib;"
        "sys.path.insert(0, sys.argv[1]);"
        "import server;"
        "buf=io.StringIO();"
        "ctx=contextlib.redirect_stdout(buf); ctx.__enter__();"
        "rc=server.main(['--print-openapi']);"
        "ctx.__exit__(None,None,None);"
        "printed=json.loads(buf.getvalue());"
        "assert rc==0, rc;"
        "assert printed==server.app.openapi(), 'main() built a different app';"
        "print('OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(PACKAGING_DIR)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "OK" in result.stdout
