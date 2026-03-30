# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Unit tests verifying that the GAIA Agent UI server uses the webui_dist
parameter of create_app() to locate the pre-built frontend.

When installed via npm (`gaia-ui`), the launcher passes --ui-dist so the
Python server can find the dist/ folder inside the npm package rather than
looking in the (absent) PyPI package tree.
"""

import tempfile
import unittest
from pathlib import Path


class TestServerWebuiDist(unittest.TestCase):
    """Tests for webui_dist parameter handling in gaia.ui.server.create_app()."""

    # ------------------------------------------------------------------
    # Test 1: server uses webui_dist parameter and serves index.html from it
    # ------------------------------------------------------------------

    def test_server_uses_provided_webui_dist(self):
        """
        When webui_dist is passed to create_app(), the server serves index.html
        from that directory instead of returning the JSON API banner.
        """
        from fastapi.testclient import TestClient

        from gaia.ui.server import create_app

        with tempfile.TemporaryDirectory() as tmpdir:
            dist_dir = Path(tmpdir)
            (dist_dir / "assets").mkdir()
            (dist_dir / "index.html").write_text(
                "<html><body>GAIA Agent UI</body></html>"
            )

            app = create_app(webui_dist=str(dist_dir), db_path=":memory:")
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/")
            self.assertEqual(response.status_code, 200)
            self.assertIn("GAIA Agent UI", response.text)


if __name__ == "__main__":
    unittest.main()
