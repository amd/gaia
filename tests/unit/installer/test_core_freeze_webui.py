# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The frozen ``gaia`` carries the built Agent UI, so ``gaia chat --ui`` serves it.

Without it the backend serves only its placeholder page, and every installer
journey's setup phase timed out waiting for the setup screen (v0.26.0-rc6/rc7).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
CORE = REPO / "installer" / "core"
BUILD_CORE = REPO / ".github" / "workflows" / "build_core.yml"
JOURNEYS = REPO / ".github" / "workflows" / "test_installers_headless.yml"
SERVER = REPO / "src" / "gaia" / "ui" / "server.py"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"core_{name}", CORE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


freeze = _load("freeze")
smoke = _load("smoke_test")


def _bundled_path(dest: str) -> Path:
    """Where PyInstaller puts *dest*, relative to the frozen ``_internal`` root."""
    return Path(*dest.split("/"))


def test_freeze_bundles_the_webui_build_where_the_server_looks():
    entries = {src: dest for src, dest in freeze.PACKAGE_DATA}
    assert freeze.WEBUI_DIST in entries
    # server.py resolves Path(__file__).parent.parent / "apps/webui/dist" from
    # gaia/ui/server.py, i.e. gaia/apps/webui/dist under the bundle root.
    server_rel = Path("gaia") / "ui" / "server.py"
    expected = server_rel.parent.parent / "apps" / "webui" / "dist"
    assert _bundled_path(entries[freeze.WEBUI_DIST]) == expected
    assert freeze.WEBUI_DIST == REPO / "src" / expected
    assert (
        'Path(__file__).resolve().parent.parent / "apps" / "webui" / "dist"'
        in SERVER.read_text(encoding="utf-8")
    )


def test_freeze_refuses_without_a_webui_build(tmp_path, monkeypatch):
    monkeypatch.setattr(freeze, "WEBUI_DIST", tmp_path / "dist")
    with pytest.raises(SystemExit) as exc:
        freeze._verify_freeze_env()
    message = str(exc.value)
    assert "no Agent UI build" in message
    assert "npm run build" in message


def test_freeze_accepts_a_webui_build(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    monkeypatch.setattr(freeze, "WEBUI_DIST", dist)
    # Skip the module checks: the test env need not hold fastapi/faiss/etc.
    monkeypatch.setattr(freeze, "COLLECT_SUBMODULES", [])
    monkeypatch.setattr(freeze, "COLLECT_ALL", [])
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, _real=importlib.util.find_spec: (
            _real(name) if name == "gaia" else object()
        ),
    )
    freeze._verify_freeze_env()


@pytest.mark.parametrize(
    "html, is_app",
    [
        (
            "<!DOCTYPE html><html><head><title>GAIA Agent UI</title>"
            '<script type="module" crossorigin src="/assets/index-abc.js"></script>'
            '</head><body><div id="root"></div></body></html>',
            True,
        ),
        # The backend's placeholder when no build is bundled.
        (
            "<!DOCTYPE html><html><head><title>GAIA Agent UI &mdash; Backend API"
            "</title><style>:root { color-scheme: light dark; }</style></head>"
            "<body><h1>This is the GAIA backend API</h1></body></html>",
            False,
        ),
        ("", False),
        ("GET / failed: timed out", False),
        ('<div id="root"></div>', False),
    ],
)
def test_smoke_tells_the_app_from_the_placeholder(html, is_app):
    assert smoke.serves_app(html) is is_app


def test_smoke_placeholder_is_the_servers_fallback():
    text = SERVER.read_text(encoding="utf-8")
    start = text.index('_FALLBACK_HTML = """') + len('_FALLBACK_HTML = """')
    fallback = text[start : text.index('"""', start)]
    assert not smoke.serves_app(fallback)


def _steps(workflow: Path, job: str) -> list[dict]:
    return yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"][job]["steps"]


def test_build_core_builds_the_webui_before_freezing():
    names = [s.get("name", "") for s in _steps(BUILD_CORE, "freeze")]
    build = next(
        i
        for i, s in enumerate(_steps(BUILD_CORE, "freeze"))
        if "npm run build" in s.get("run", "")
    )
    assert build < names.index("Freeze")
    assert build > names.index("Stamp the release-candidate version")


@pytest.mark.parametrize("job", ["windows", "posix"])
def test_journeys_upload_playwright_results_on_failure(job):
    uploads = [
        s
        for s in _steps(JOURNEYS, job)
        if str(s.get("uses", "")).startswith("actions/upload-artifact")
        and str(s.get("with", {}).get("name", "")).startswith("playwright-results-")
    ]
    assert len(uploads) == 1
    step = uploads[0]
    assert "failure()" in step["if"]
    assert step["with"]["path"].rstrip("/") == "src/gaia/apps/webui/e2e/test-results"
    config = (REPO / "src/gaia/apps/webui/e2e/playwright.config.ts").read_text(
        encoding="utf-8"
    )
    assert "outputDir: 'test-results/output'" in config
