# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Unit tests verifying that the GAIA Agent UI server reads GAIA_WEBUI_DIST
from the environment to locate the pre-built frontend.

When installed via npm (`gaia-ui`), the launcher sets this env var so the
Python server can find the dist/ folder inside the npm package rather than
looking in the (absent) PyPI package tree.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestServerWebuiDist(unittest.TestCase):
    """Tests for GAIA_WEBUI_DIST env-var handling in gaia.ui.server."""

    # ------------------------------------------------------------------
    # Test 1: server reads GAIA_WEBUI_DIST and serves index.html from it
    # ------------------------------------------------------------------

    def test_server_reads_gaia_webui_dist_env_var(self):
        """
        When GAIA_WEBUI_DIST points to a real dist dir, create_app() should
        register a route for '/' that serves the index.html from that dir.
        """
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as tmpdir:
            dist_dir = Path(tmpdir)
            # Create a minimal fake dist layout
            (dist_dir / "assets").mkdir()
            (dist_dir / "index.html").write_text(
                "<html><body>GAIA Agent UI</body></html>"
            )

            with patch.dict(os.environ, {"GAIA_WEBUI_DIST": str(dist_dir)}):
                # Re-import create_app so the module re-evaluates _webui_dist
                import importlib

                import gaia.ui.server as server_mod

                importlib.reload(server_mod)
                app = server_mod.create_app(db_path=":memory:")

            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/")
            # Should serve the index file (200) rather than the JSON API banner
            self.assertEqual(response.status_code, 200)
            self.assertIn("GAIA Agent UI", response.text)

    # ------------------------------------------------------------------
    # Test 2: server falls back gracefully when env var is unset
    # ------------------------------------------------------------------

    def test_server_falls_back_to_default_dist_when_env_unset(self):
        """
        When GAIA_WEBUI_DIST is not set, create_app() should still succeed.
        If the default dist dir doesn't exist the server returns JSON (not a
        crash), so we just assert the app is created without raising.
        """
        import importlib

        import gaia.ui.server as server_mod

        # Ensure env var is absent
        env_without_var = {
            k: v for k, v in os.environ.items() if k != "GAIA_WEBUI_DIST"
        }
        with patch.dict(os.environ, env_without_var, clear=True):
            importlib.reload(server_mod)
            try:
                app = server_mod.create_app(db_path=":memory:")
            except Exception as exc:
                self.fail(f"create_app() raised unexpectedly when env var unset: {exc}")

        self.assertIsNotNone(app)


if __name__ == "__main__":
    unittest.main()
