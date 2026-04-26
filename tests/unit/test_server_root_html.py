# Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Unit tests for the GAIA Agent UI server's ``/`` fallback behaviour when the
frontend ``dist/`` directory is not present.

Part of the issue #782 AppImage launch-failure fix (T4): visiting
http://localhost:4200/ in a browser previously returned raw JSON. It must now
return a helpful HTML landing page pointing the user at the desktop app and
the API docs.

These tests cover **AC5** in the #782 plan:

    "Graceful fallback when the embedded UI fails; visiting localhost:4200/
     shows a helpful page, not raw JSON."
"""

import json
import tempfile
import unittest
from pathlib import Path


class TestServerRootHtmlFallback(unittest.TestCase):
    """The root ``/`` route must serve HTML (not JSON) when dist/ is absent."""

    def _build_client(self):
        """Create a FastAPI TestClient with no dist/ directory configured.

        We point ``webui_dist`` at a freshly-created, empty temp directory so
        ``create_app`` takes the "no frontend build found" branch. Using
        ``db_path=":memory:"`` avoids touching the real database file.
        """
        from fastapi.testclient import TestClient

        from gaia.ui.server import create_app

        # Non-existent dir: create_app's `_webui_dist.is_dir()` check fails
        # and installs the HTML fallback route at "/". An empty-but-existing
        # dir would still hit the StaticFiles mount for `<dist>/assets` and
        # blow up before the fallback branch is reached.
        self._tmpdir = tempfile.TemporaryDirectory()
        missing_dist = Path(self._tmpdir.name) / "missing-dist"
        # NOTE: intentionally NOT calling mkdir() — the path must not exist.

        app = create_app(webui_dist=str(missing_dist), db_path=":memory:")
        return TestClient(app, raise_server_exceptions=False)

    def tearDown(self):
        tmp = getattr(self, "_tmpdir", None)
        if tmp is not None:
            tmp.cleanup()

    # ------------------------------------------------------------------
    # AC5: root returns HTML, not JSON, when dist/ is missing
    # ------------------------------------------------------------------

    def test_root_returns_200(self):
        """GET / must succeed (200) even without a frontend build."""
        client = self._build_client()
        response = client.get("/")
        self.assertEqual(response.status_code, 200)

    def test_root_content_type_is_html(self):
        """Content-Type must start with text/html (not application/json)."""
        client = self._build_client()
        response = client.get("/")
        ctype = response.headers.get("content-type", "")
        self.assertTrue(
            ctype.lower().startswith("text/html"),
            f"Expected text/html Content-Type, got: {ctype!r}",
        )

    def test_root_body_mentions_desktop_app(self):
        """Body must mention the desktop app (case-insensitive)."""
        client = self._build_client()
        response = client.get("/")
        self.assertIn("desktop app", response.text.lower())

    def test_root_body_is_not_json(self):
        """Body must NOT be valid JSON — the old broken behaviour."""
        client = self._build_client()
        response = client.get("/")
        with self.assertRaises(
            (json.JSONDecodeError, ValueError),
            msg="Root page should not be parseable as JSON (that was the bug).",
        ):
            json.loads(response.text)


if __name__ == "__main__":
    unittest.main()
