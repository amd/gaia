# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Integration tests for per-request SPA static file resolution (issue #1088).

The server must NOT cache the presence or absence of the frontend dist directory
at startup. If dist/ appears after the server is already running (e.g. because
npm run build finished in the background), a fresh browser refresh must serve the
real SPA without requiring a process restart.

Acceptance criteria:
- App started with an EMPTY dist dir serves the "no frontend build found" fallback.
- After index.html + assets/app.js are written to that same dir, a fresh GET /
  returns the REAL SPA content (not the fallback).
- A fresh GET /assets/app.js returns the real asset file content.
- The healthy-at-startup case continues to work (dist present before create_app()).
- Path-traversal requests to /assets/../secret are still rejected.
"""

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from gaia.ui.server import create_app


class TestSpaHotDistResolution:
    """Server resolves the dist dir on every request, not once at startup."""

    def test_fallback_served_when_dist_empty_at_startup(self):
        """GET / on a freshly-created empty dist dir returns the fallback page."""
        with tempfile.TemporaryDirectory() as tmpdir:
            dist_dir = Path(tmpdir)
            # dist_dir exists but has no index.html  ->  should hit fallback
            app = create_app(webui_dist=str(dist_dir), db_path=":memory:")
            client = TestClient(app, raise_server_exceptions=False)

            resp = client.get("/")
            assert resp.status_code == 200
            # Must be the fallback page, not the real SPA
            assert (
                "no frontend build" in resp.text.lower()
                or "desktop app" in resp.text.lower()
            )
            assert "REAL_SPA_CONTENT" not in resp.text

    def test_real_spa_served_after_build_appears(self):
        """After dist/index.html is written post-startup, GET / returns the real SPA."""
        with tempfile.TemporaryDirectory() as tmpdir:
            dist_dir = Path(tmpdir)
            # Start with empty dir (no index.html)
            app = create_app(webui_dist=str(dist_dir), db_path=":memory:")
            client = TestClient(app, raise_server_exceptions=False)

            # Confirm fallback is served first
            resp_before = client.get("/")
            assert "REAL_SPA_CONTENT" not in resp_before.text

            # Simulate npm run build completing: write index.html + assets/
            assets_dir = dist_dir / "assets"
            assets_dir.mkdir(parents=True, exist_ok=True)
            (dist_dir / "index.html").write_text(
                "<html><body>REAL_SPA_CONTENT</body></html>", encoding="utf-8"
            )
            (assets_dir / "app.js").write_text(
                "/* REAL_ASSET_CONTENT */", encoding="utf-8"
            )

            # NO app recreation -- same client, same process
            resp_after = client.get("/")
            assert resp_after.status_code == 200
            assert "REAL_SPA_CONTENT" in resp_after.text, (
                "Expected real SPA content after dist appeared, got fallback: "
                + resp_after.text[:300]
            )

    def test_asset_served_after_build_appears(self):
        """After dist/assets/app.js appears, GET /assets/app.js returns the file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            dist_dir = Path(tmpdir)
            app = create_app(webui_dist=str(dist_dir), db_path=":memory:")
            client = TestClient(app, raise_server_exceptions=False)

            # Before build: asset should NOT be found (or not return real content)
            resp_before = client.get("/assets/app.js")
            assert (
                resp_before.status_code != 200
                or "REAL_ASSET_CONTENT" not in resp_before.text
            )

            # Write the assets
            assets_dir = dist_dir / "assets"
            assets_dir.mkdir(parents=True, exist_ok=True)
            (dist_dir / "index.html").write_text(
                "<html><body>REAL_SPA_CONTENT</body></html>", encoding="utf-8"
            )
            (assets_dir / "app.js").write_text(
                "/* REAL_ASSET_CONTENT */", encoding="utf-8"
            )

            resp_after = client.get("/assets/app.js")
            assert resp_after.status_code == 200
            assert "REAL_ASSET_CONTENT" in resp_after.text

    def test_healthy_startup_case_unchanged(self):
        """When dist is present at startup, GET / still serves the real SPA."""
        with tempfile.TemporaryDirectory() as tmpdir:
            dist_dir = Path(tmpdir)
            assets_dir = dist_dir / "assets"
            assets_dir.mkdir(parents=True)
            (dist_dir / "index.html").write_text(
                "<html><body>REAL_SPA_CONTENT</body></html>", encoding="utf-8"
            )
            (assets_dir / "app.js").write_text(
                "/* REAL_ASSET_CONTENT */", encoding="utf-8"
            )

            app = create_app(webui_dist=str(dist_dir), db_path=":memory:")
            client = TestClient(app, raise_server_exceptions=False)

            resp = client.get("/")
            assert resp.status_code == 200
            assert "REAL_SPA_CONTENT" in resp.text

    def test_missing_asset_returns_404_not_index_html(self):
        """A missing file under /assets/ must 404, not fall through to index.html.

        Otherwise a stale hashed chunk request gets index.html (text/html) back,
        which the browser tries to execute as JS (``Uncaught SyntaxError``).
        SPA fallback is correct for route paths only (issue #1741 review).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            dist_dir = Path(tmpdir)
            assets_dir = dist_dir / "assets"
            assets_dir.mkdir(parents=True)
            (dist_dir / "index.html").write_text(
                "<html><body>REAL_SPA_CONTENT</body></html>", encoding="utf-8"
            )

            app = create_app(webui_dist=str(dist_dir), db_path=":memory:")
            client = TestClient(app, raise_server_exceptions=False)

            # Missing hashed chunk under /assets/ -> real 404, not index.html
            resp = client.get("/assets/old-chunk-abc123.js")
            assert resp.status_code == 404, (
                "Missing /assets/* file must 404, not serve index.html; got "
                f"{resp.status_code}"
            )
            assert "REAL_SPA_CONTENT" not in resp.text

            # A non-asset route path still falls back to the SPA (unchanged).
            resp_route = client.get("/some/app/route")
            assert resp_route.status_code == 200
            assert "REAL_SPA_CONTENT" in resp_route.text

    def test_path_traversal_outside_dist_rejected(self):
        """Requests that escape the dist directory must not serve files from outside it."""
        with tempfile.TemporaryDirectory() as outer_dir_str:
            outer_dir = Path(outer_dir_str)
            dist_dir = outer_dir / "dist"
            dist_dir.mkdir()
            assets_dir = dist_dir / "assets"
            assets_dir.mkdir()
            (dist_dir / "index.html").write_text(
                "<html><body>REAL_SPA_CONTENT</body></html>", encoding="utf-8"
            )
            # Write a "secret" file OUTSIDE the dist dir (one level up)
            (outer_dir / "secret.txt").write_text(
                "SECRET_OUTSIDE_DIST", encoding="utf-8"
            )

            app = create_app(webui_dist=str(dist_dir), db_path=":memory:")
            client = TestClient(app, raise_server_exceptions=False)

            # Traversal attempt escaping the dist root -- must NOT serve the file
            # HTTP clients normalize ".." so we check sanitize_static_path directly
            from gaia.ui.utils import sanitize_static_path

            result = sanitize_static_path(dist_dir.resolve(), "../secret.txt")
            assert (
                result is None
            ), "_sanitize_static_path should reject traversal outside dist dir"
