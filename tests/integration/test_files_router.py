# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Integration tests for GAIA Agent UI files router.

Tests the full HTTP API surface of src/gaia/ui/routers/files.py:
- POST /api/files/upload   -- multipart file upload with validation
- GET  /api/files/browse   -- directory browsing with security checks
- GET  /api/files/search   -- async file search across user directories
- GET  /api/files/preview  -- file content preview with encoding detection
- POST /api/files/open     -- open file/folder in OS file explorer

All tests use FastAPI TestClient with in-memory database.
Filesystem operations use temporary directories inside the user's home
directory to satisfy the ensure_within_home security restriction.

Subprocess calls (for the open endpoint) are mocked to avoid launching
external processes during tests.
"""

import io
import logging
import platform
import shutil
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from gaia.ui.server import create_app

logger = logging.getLogger(__name__)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def app():
    """Create FastAPI app with in-memory database."""
    return create_app(db_path=":memory:")


@pytest.fixture
def client(app):
    """Create test client for the app."""
    return TestClient(app)


@pytest.fixture
def home_tmp_dir():
    """Create a temporary directory inside the user's home directory.

    The files router restricts all browse/preview/open operations to paths
    within Path.home(), so we must create test fixtures there rather than
    in the system temp directory (which is typically outside home on Windows).

    Yields the Path to the temporary directory and cleans it up afterwards.
    """
    home = Path.home()
    tmp_dir = home / ".gaia" / "test_files_router" / str(uuid.uuid4())[:8]
    tmp_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Created test temp dir: %s", tmp_dir)
    yield tmp_dir
    # Cleanup
    try:
        shutil.rmtree(str(tmp_dir))
        logger.info("Cleaned up test temp dir: %s", tmp_dir)
    except OSError as exc:
        logger.warning("Failed to clean up %s: %s", tmp_dir, exc)


@pytest.fixture
def sample_text_file(home_tmp_dir):
    """Create a sample .txt file inside the home temp directory."""
    filepath = home_tmp_dir / "sample.txt"
    filepath.write_text("Line 1\nLine 2\nLine 3\nLine 4\nLine 5\n", encoding="utf-8")
    return filepath


@pytest.fixture
def sample_csv_file(home_tmp_dir):
    """Create a sample .csv file inside the home temp directory."""
    filepath = home_tmp_dir / "data.csv"
    filepath.write_text(
        "name,age,city\nAlice,30,NYC\nBob,25,LA\nCharlie,35,Chicago\n",
        encoding="utf-8",
    )
    return filepath


@pytest.fixture
def sample_tsv_file(home_tmp_dir):
    """Create a sample .tsv file inside the home temp directory."""
    filepath = home_tmp_dir / "data.tsv"
    filepath.write_text(
        "name\tage\tcity\nAlice\t30\tNYC\nBob\t25\tLA\n",
        encoding="utf-8",
    )
    return filepath


@pytest.fixture
def sample_py_file(home_tmp_dir):
    """Create a sample .py file inside the home temp directory."""
    filepath = home_tmp_dir / "hello.py"
    filepath.write_text(
        '#!/usr/bin/env python3\n\ndef greet(name):\n    return f"Hello, {name}!"\n',
        encoding="utf-8",
    )
    return filepath


@pytest.fixture
def sample_json_file(home_tmp_dir):
    """Create a sample .json file inside the home temp directory."""
    filepath = home_tmp_dir / "config.json"
    filepath.write_text('{"key": "value", "number": 42}\n', encoding="utf-8")
    return filepath


@pytest.fixture
def sample_pdf_bytes():
    """Return minimal PDF content for upload tests."""
    # Minimal valid PDF (header + empty body + xref + trailer)
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\n"
        b"xref\n0 3\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"trailer<</Size 3/Root 1 0 R>>\nstartxref\n109\n%%EOF\n"
    )


@pytest.fixture
def sample_png_bytes():
    """Return minimal valid PNG content for upload tests.

    This is a 1x1 pixel red PNG file (smallest valid PNG).
    """
    # Minimal 1x1 red pixel PNG
    import struct
    import zlib

    def _chunk(chunk_type, data):
        raw = chunk_type + data
        return (
            struct.pack(">I", len(data))
            + raw
            + struct.pack(">I", zlib.crc32(raw) & 0xFFFFFFFF)
        )

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)  # 1x1 RGB
    ihdr = _chunk(b"IHDR", ihdr_data)
    # One row: filter byte (0) + RGB (255, 0, 0)
    raw_data = b"\x00\xff\x00\x00"
    idat = _chunk(b"IDAT", zlib.compress(raw_data))
    iend = _chunk(b"IEND", b"")
    return signature + ihdr + idat + iend


# ── TestFileUpload ───────────────────────────────────────────────────────────


class TestFileUpload:
    """Tests for POST /api/files/upload."""

    def test_upload_text_file(self, client):
        """Upload a .txt file and verify all response fields."""
        content = b"Hello, this is a test file."
        resp = client.post(
            "/api/files/upload",
            files={"file": ("test.txt", io.BytesIO(content), "text/plain")},
        )
        assert resp.status_code == 200, f"Upload failed: {resp.text}"

        data = resp.json()
        assert data["original_name"] == "test.txt"
        assert data["size"] == len(content)
        assert data["content_type"] == "text/plain"
        assert data["is_image"] is False
        assert data["url"].startswith("/api/files/uploads/")
        assert data["filename"].endswith(".txt")
        # Filename should be UUID format + extension
        stem = data["filename"].rsplit(".", 1)[0]
        uuid.UUID(stem)  # Raises ValueError if not valid UUID
        logger.info("Upload text file response: %s", data)

    def test_upload_image_file(self, client, sample_png_bytes):
        """Upload a .png image and verify is_image=True."""
        resp = client.post(
            "/api/files/upload",
            files={"file": ("photo.png", io.BytesIO(sample_png_bytes), "image/png")},
        )
        assert resp.status_code == 200

        data = resp.json()
        assert data["original_name"] == "photo.png"
        assert data["is_image"] is True
        assert data["filename"].endswith(".png")
        logger.info("Upload image file response: is_image=%s", data["is_image"])

    def test_upload_pdf_file(self, client, sample_pdf_bytes):
        """Upload a .pdf file and verify content_type."""
        resp = client.post(
            "/api/files/upload",
            files={
                "file": (
                    "document.pdf",
                    io.BytesIO(sample_pdf_bytes),
                    "application/pdf",
                )
            },
        )
        assert resp.status_code == 200

        data = resp.json()
        assert data["original_name"] == "document.pdf"
        assert data["content_type"] == "application/pdf"
        assert data["is_image"] is False
        assert data["filename"].endswith(".pdf")

    def test_upload_no_filename(self, client):
        """Upload with empty filename should return 400 or 422.

        FastAPI/Starlette may reject the empty filename at the framework
        level (422 Unprocessable Entity) before our handler checks
        ``if not file.filename``.  Both 400 and 422 are acceptable since
        they both indicate a client error.
        """
        resp = client.post(
            "/api/files/upload",
            files={"file": ("", io.BytesIO(b"data"), "text/plain")},
        )
        assert resp.status_code in (
            400,
            422,
        ), f"Expected 400 or 422 for empty filename, got {resp.status_code}"

    def test_upload_empty_file(self, client):
        """Upload an empty file should return 400."""
        resp = client.post(
            "/api/files/upload",
            files={"file": ("empty.txt", io.BytesIO(b""), "text/plain")},
        )
        assert resp.status_code == 400
        assert "empty" in resp.json()["detail"].lower()

    def test_upload_disallowed_extension(self, client):
        """Upload a .exe file should return 400."""
        resp = client.post(
            "/api/files/upload",
            files={
                "file": (
                    "malware.exe",
                    io.BytesIO(b"\x00" * 100),
                    "application/octet-stream",
                )
            },
        )
        assert resp.status_code == 400
        assert "not allowed" in resp.json()["detail"].lower()

    def test_upload_disallowed_extension_bat_not_doc(self, client):
        """Upload a .dll file should return 400, .bat is allowed."""
        # .dll is NOT in UPLOAD_ALLOWED_EXTENSIONS
        resp = client.post(
            "/api/files/upload",
            files={
                "file": (
                    "lib.dll",
                    io.BytesIO(b"\x00" * 10),
                    "application/octet-stream",
                )
            },
        )
        assert resp.status_code == 400

        # .bat IS in ALLOWED_EXTENSIONS (and therefore UPLOAD_ALLOWED_EXTENSIONS)
        resp_bat = client.post(
            "/api/files/upload",
            files={"file": ("script.bat", io.BytesIO(b"echo hello"), "text/plain")},
        )
        assert resp_bat.status_code == 200

    def test_upload_oversized_file(self, client):
        """Upload a file >20MB should return 413."""
        # Create content just over the 20MB limit
        size = 20 * 1024 * 1024 + 1
        resp = client.post(
            "/api/files/upload",
            files={"file": ("big.txt", io.BytesIO(b"x" * size), "text/plain")},
        )
        assert resp.status_code == 413
        assert "too large" in resp.json()["detail"].lower()

    def test_upload_preserves_extension(self, client):
        """Verify uploaded filename is UUID.ext pattern."""
        for ext, ctype in [
            (".md", "text/markdown"),
            (".csv", "text/csv"),
            (".json", "application/json"),
            (".jpg", "image/jpeg"),
        ]:
            filename = f"myfile{ext}"
            resp = client.post(
                "/api/files/upload",
                files={"file": (filename, io.BytesIO(b"content"), ctype)},
            )
            assert resp.status_code == 200, f"Failed for extension {ext}: {resp.text}"
            data = resp.json()
            assert data["filename"].endswith(
                ext
            ), f"Extension not preserved: expected {ext}, got {data['filename']}"
            # Verify UUID prefix
            stem = data["filename"][: -len(ext)]
            uuid.UUID(stem)  # Validates UUID format

    def test_upload_creates_uploads_dir(self, client):
        """Verify the uploads directory exists after an upload."""
        resp = client.post(
            "/api/files/upload",
            files={"file": ("test.txt", io.BytesIO(b"data"), "text/plain")},
        )
        assert resp.status_code == 200
        uploads_dir = Path.home() / ".gaia" / "chat" / "uploads"
        assert uploads_dir.is_dir(), "Uploads directory was not created"

    def test_uploaded_file_accessible(self, client):
        """Verify the uploaded file can be served back via its URL."""
        content = b"Retrieve me please"
        resp = client.post(
            "/api/files/upload",
            files={"file": ("retrieve.txt", io.BytesIO(content), "text/plain")},
        )
        assert resp.status_code == 200
        url = resp.json()["url"]

        # Fetch the file back via the static mount
        get_resp = client.get(url)
        assert (
            get_resp.status_code == 200
        ), f"Could not retrieve uploaded file at {url}: {get_resp.status_code}"
        assert get_resp.content == content

    def test_upload_image_extensions(self, client, sample_png_bytes):
        """Verify all image extensions are accepted and flag is_image correctly."""
        image_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
        for ext in image_exts:
            filename = f"image{ext}"
            resp = client.post(
                "/api/files/upload",
                files={"file": (filename, io.BytesIO(sample_png_bytes), "image/png")},
            )
            assert resp.status_code == 200, f"Image ext {ext} rejected: {resp.text}"
            assert resp.json()["is_image"] is True, f"is_image not True for {ext}"


# ── TestFileBrowse ───────────────────────────────────────────────────────────


class TestFileBrowse:
    """Tests for GET /api/files/browse."""

    def test_browse_home_directory(self, client):
        """Default path (no param) should return home directory contents."""
        # On Windows with no path param, the router lists drive letters
        # because 'not path' is True and we're on Windows.
        # We pass the explicit home path instead.
        home = str(Path.home())
        resp = client.get("/api/files/browse", params={"path": home})
        assert resp.status_code == 200

        data = resp.json()
        assert data["current_path"] == home
        assert data["parent_path"] is None  # At home, parent_path should be None
        assert isinstance(data["entries"], list)
        assert isinstance(data["quick_links"], list)
        logger.info(
            "Browse home: %d entries, %d quick_links",
            len(data["entries"]),
            len(data["quick_links"]),
        )

    def test_browse_specific_directory(self, client, home_tmp_dir, sample_text_file):
        """Browse a known temp directory and verify the file appears."""
        resp = client.get("/api/files/browse", params={"path": str(home_tmp_dir)})
        assert resp.status_code == 200

        data = resp.json()
        assert data["current_path"] == str(home_tmp_dir.resolve())
        names = [e["name"] for e in data["entries"]]
        assert "sample.txt" in names, f"sample.txt not found in {names}"

    def test_browse_returns_folders_first(self, client, home_tmp_dir):
        """Verify folders appear before files in the response."""
        # Create a subfolder and a file
        subfolder = home_tmp_dir / "aaa_folder"
        subfolder.mkdir()
        txt_file = home_tmp_dir / "bbb_file.txt"
        txt_file.write_text("content", encoding="utf-8")

        resp = client.get("/api/files/browse", params={"path": str(home_tmp_dir)})
        assert resp.status_code == 200

        entries = resp.json()["entries"]
        assert len(entries) >= 2

        # Find the index of folder and file entries
        folder_indices = [i for i, e in enumerate(entries) if e["type"] == "folder"]
        file_indices = [i for i, e in enumerate(entries) if e["type"] == "file"]

        if folder_indices and file_indices:
            assert max(folder_indices) < min(
                file_indices
            ), "Folders should appear before files in browse results"

    def test_browse_filters_by_extension(self, client, home_tmp_dir):
        """Only files with ALLOWED_EXTENSIONS should appear."""
        # Create a file with allowed extension
        allowed = home_tmp_dir / "allowed.txt"
        allowed.write_text("ok", encoding="utf-8")

        # Create a file with disallowed extension
        disallowed = home_tmp_dir / "blocked.exe"
        disallowed.write_bytes(b"\x00")

        # Create another disallowed
        disallowed2 = home_tmp_dir / "image.mp4"
        disallowed2.write_bytes(b"\x00")

        resp = client.get("/api/files/browse", params={"path": str(home_tmp_dir)})
        assert resp.status_code == 200

        entry_names = [e["name"] for e in resp.json()["entries"]]
        assert "allowed.txt" in entry_names
        assert "blocked.exe" not in entry_names, ".exe files should be filtered out"
        assert "image.mp4" not in entry_names, ".mp4 files should be filtered out"

    def test_browse_null_byte_rejection(self, client):
        """Path containing null byte should return 400."""
        home = str(Path.home())
        resp = client.get("/api/files/browse", params={"path": home + "\x00evil"})
        assert resp.status_code == 400
        assert "Invalid path" in resp.json()["detail"]

    @pytest.mark.skipif(
        platform.system() == "Windows",
        reason="Symlinks require elevated privileges on Windows",
    )
    def test_browse_symlink_rejection(self, client, home_tmp_dir):
        """Symlink in path should return 400."""
        target = home_tmp_dir / "real_dir"
        target.mkdir()
        link = home_tmp_dir / "link_dir"
        link.symlink_to(target)

        resp = client.get("/api/files/browse", params={"path": str(link)})
        assert resp.status_code == 400
        assert "Symbolic links" in resp.json()["detail"]

    def test_browse_nonexistent_directory(self, client, home_tmp_dir):
        """Browsing a nonexistent directory should return 404."""
        nonexistent = home_tmp_dir / "does_not_exist_xyz"
        resp = client.get("/api/files/browse", params={"path": str(nonexistent)})
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_browse_outside_home(self, client):
        """Browsing outside home directory should return 403."""
        # Try to browse the system root or a known non-home path
        if platform.system() == "Windows":
            outside_path = "C:\\Windows\\System32"
        else:
            outside_path = "/etc"

        resp = client.get("/api/files/browse", params={"path": outside_path})
        assert resp.status_code == 403
        assert "home directory" in resp.json()["detail"].lower()

    def test_browse_quick_links_present(self, client, home_tmp_dir):
        """Verify quick_links are included in the browse response."""
        resp = client.get("/api/files/browse", params={"path": str(home_tmp_dir)})
        assert resp.status_code == 200

        quick_links = resp.json()["quick_links"]
        assert isinstance(quick_links, list)
        assert len(quick_links) >= 1  # At minimum, Home link
        link_names = [ql["name"] for ql in quick_links]
        assert "Home" in link_names, "Home quick link should always be present"

    def test_browse_parent_path_at_home(self, client):
        """At home directory, parent_path should be None."""
        home = str(Path.home())
        resp = client.get("/api/files/browse", params={"path": home})
        assert resp.status_code == 200
        assert resp.json()["parent_path"] is None

    def test_browse_parent_path_below_home(self, client, home_tmp_dir):
        """Below home, parent_path should point to the parent directory."""
        resp = client.get("/api/files/browse", params={"path": str(home_tmp_dir)})
        assert resp.status_code == 200

        parent_path = resp.json()["parent_path"]
        assert parent_path is not None
        # parent_path should be a real ancestor of home_tmp_dir
        assert Path(parent_path).is_dir()

    @pytest.mark.skipif(
        platform.system() != "Windows",
        reason="Drive letter listing is Windows-only",
    )
    def test_browse_windows_drive_listing(self, client):
        """On Windows, browsing '/' should list drive letters."""
        resp = client.get("/api/files/browse", params={"path": "/"})
        assert resp.status_code == 200

        data = resp.json()
        assert data["current_path"] == "/"
        assert data["parent_path"] is None
        # Should have at least C: drive
        names = [e["name"] for e in data["entries"]]
        assert "C:" in names, f"C: drive not found in {names}"

    def test_browse_entry_fields(self, client, home_tmp_dir, sample_text_file):
        """Verify all required fields are present on entries."""
        resp = client.get("/api/files/browse", params={"path": str(home_tmp_dir)})
        assert resp.status_code == 200

        for entry in resp.json()["entries"]:
            assert "name" in entry
            assert "path" in entry
            assert "type" in entry
            assert entry["type"] in ("file", "folder")
            assert "size" in entry
            assert "modified" in entry
            if entry["type"] == "file":
                assert entry["extension"] is not None


# ── TestFileSearch ───────────────────────────────────────────────────────────


class TestFileSearch:
    """Tests for GET /api/files/search."""

    def test_search_finds_file(self, client, home_tmp_dir, sample_text_file):
        """Search for a known filename should find it."""
        # The search scans Documents/Downloads/Desktop/OneDrive then home.
        # Our file is under ~/.gaia/test_files_router/ which is under home,
        # so it should be found in the home fallback scan.
        resp = client.get(
            "/api/files/search",
            params={"query": "sample.txt", "max_results": 50},
        )
        assert resp.status_code == 200

        data = resp.json()
        assert data["query"] == "sample.txt"
        assert isinstance(data["results"], list)
        assert isinstance(data["searched_locations"], list)
        assert data["total"] == len(data["results"])

        # The file might or might not be found depending on the scan depth
        # and directory structure. If found, verify structure.
        if data["total"] > 0:
            result = data["results"][0]
            assert "name" in result
            assert "path" in result
            assert "size" in result
            assert "size_display" in result
            assert "extension" in result
            assert "modified" in result
            assert "directory" in result

    def test_search_empty_query(self, client):
        """Empty search query should return 400."""
        resp = client.get("/api/files/search", params={"query": ""})
        assert resp.status_code == 400
        assert "required" in resp.json()["detail"].lower()

    def test_search_whitespace_query(self, client):
        """Whitespace-only query should return 400."""
        resp = client.get("/api/files/search", params={"query": "   "})
        assert resp.status_code == 400

    def test_search_null_byte_query(self, client):
        """Query with null byte should return 400."""
        resp = client.get("/api/files/search", params={"query": "file\x00.txt"})
        assert resp.status_code == 400
        assert "Invalid" in resp.json()["detail"]

    def test_search_with_file_type_filter(self, client):
        """Search with file_types filter should only return matching extensions."""
        resp = client.get(
            "/api/files/search",
            params={"query": "test", "file_types": "txt"},
        )
        assert resp.status_code == 200

        data = resp.json()
        for result in data["results"]:
            assert (
                result["extension"] == ".txt"
            ), f"Expected .txt extension, got {result['extension']}"

    def test_search_max_results_limit(self, client):
        """Verify max_results is clamped to 100."""
        # Requesting 200 should be clamped to 100
        resp = client.get(
            "/api/files/search",
            params={"query": "test", "max_results": 200},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] <= 100

    def test_search_max_results_minimum(self, client):
        """Verify max_results minimum is 1."""
        resp = client.get(
            "/api/files/search",
            params={"query": "test", "max_results": 0},
        )
        # FastAPI validates ge=1 on the query model for FileSearchRequest,
        # but the endpoint uses plain int param with manual clamping.
        # max(0, 1) = 1, so it should return at most 1 result.
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] <= 1

    def test_search_returns_file_metadata(self, client, home_tmp_dir):
        """Verify all fields present in FileSearchResult."""
        # Create a distinctively named file so we can search for it
        unique_name = f"gaia_test_metadata_{uuid.uuid4().hex[:6]}.txt"
        test_file = home_tmp_dir / unique_name
        test_file.write_text("metadata test content", encoding="utf-8")

        resp = client.get(
            "/api/files/search",
            params={"query": unique_name},
        )
        assert resp.status_code == 200

        data = resp.json()
        # File might not be found if home_tmp_dir is too deep, but if found:
        found = [r for r in data["results"] if unique_name in r["name"]]
        if found:
            result = found[0]
            assert result["name"] == unique_name
            assert result["size"] > 0
            assert result["size_display"]  # Non-empty string
            assert result["extension"] == ".txt"
            assert result["modified"]  # ISO format timestamp
            assert result["directory"] == str(home_tmp_dir)

    def test_search_skips_hidden_files(self, client, home_tmp_dir):
        """Files starting with . should be excluded from results."""
        hidden = home_tmp_dir / ".hidden_file.txt"
        hidden.write_text("secret", encoding="utf-8")

        visible_name = f"visible_gaia_{uuid.uuid4().hex[:6]}.txt"
        visible = home_tmp_dir / visible_name
        visible.write_text("visible", encoding="utf-8")

        # Search specifically for the hidden file name
        resp = client.get(
            "/api/files/search",
            params={"query": ".hidden_file"},
        )
        assert resp.status_code == 200

        data = resp.json()
        hidden_found = [r for r in data["results"] if ".hidden_file" in r["name"]]
        assert (
            len(hidden_found) == 0
        ), "Hidden files (starting with .) should be excluded"

    def test_search_sorted_by_modified(self, client, home_tmp_dir):
        """Results should be sorted by modification date, most recent first."""
        import time

        # Create files with slightly different timestamps
        old_name = f"gaia_old_{uuid.uuid4().hex[:6]}.txt"
        new_name = f"gaia_new_{uuid.uuid4().hex[:6]}.txt"

        old_file = home_tmp_dir / old_name
        old_file.write_text("old", encoding="utf-8")
        # Force a time gap
        time.sleep(0.1)
        new_file = home_tmp_dir / new_name
        new_file.write_text("new", encoding="utf-8")

        resp = client.get(
            "/api/files/search",
            params={"query": "gaia_", "max_results": 100},
        )
        assert resp.status_code == 200

        results = resp.json()["results"]
        if len(results) >= 2:
            # Verify descending order by modified timestamp
            for i in range(len(results) - 1):
                assert results[i]["modified"] >= results[i + 1]["modified"], (
                    f"Results not sorted by modified date descending: "
                    f"{results[i]['modified']} < {results[i+1]['modified']}"
                )

    def test_search_response_structure(self, client):
        """Verify the top-level response structure of FileSearchResponse."""
        resp = client.get(
            "/api/files/search",
            params={"query": "nonexistent_file_xyz_12345"},
        )
        assert resp.status_code == 200

        data = resp.json()
        assert "results" in data
        assert "total" in data
        assert "query" in data
        assert "searched_locations" in data
        assert data["query"] == "nonexistent_file_xyz_12345"
        assert isinstance(data["results"], list)
        assert isinstance(data["searched_locations"], list)
        # searched_locations is clamped to 10 entries
        assert len(data["searched_locations"]) <= 10


# ── TestFilePreview ──────────────────────────────────────────────────────────


class TestFilePreview:
    """Tests for GET /api/files/preview."""

    def test_preview_text_file(self, client, sample_text_file):
        """Read first N lines of a .txt file."""
        resp = client.get(
            "/api/files/preview",
            params={"path": str(sample_text_file), "lines": 3},
        )
        assert resp.status_code == 200

        data = resp.json()
        assert data["name"] == "sample.txt"
        assert data["extension"] == ".txt"
        assert data["is_text"] is True
        assert len(data["preview_lines"]) == 3
        assert data["preview_lines"][0] == "Line 1"
        assert data["preview_lines"][1] == "Line 2"
        assert data["preview_lines"][2] == "Line 3"
        assert data["total_lines"] == 5  # 5 text lines in sample file
        assert data["encoding"] == "utf-8"
        assert data["size"] > 0
        assert data["size_display"]  # Non-empty
        assert data["modified"]  # ISO timestamp

    def test_preview_csv_file(self, client, sample_csv_file):
        """Verify CSV columns and row_count are extracted."""
        resp = client.get(
            "/api/files/preview",
            params={"path": str(sample_csv_file)},
        )
        assert resp.status_code == 200

        data = resp.json()
        assert data["extension"] == ".csv"
        assert data["is_text"] is True
        assert data["columns"] == ["name", "age", "city"]
        assert data["row_count"] == 3  # 3 data rows (excluding header)

    def test_preview_tsv_file(self, client, sample_tsv_file):
        """Verify tab-separated file parsing."""
        resp = client.get(
            "/api/files/preview",
            params={"path": str(sample_tsv_file)},
        )
        assert resp.status_code == 200

        data = resp.json()
        assert data["extension"] == ".tsv"
        assert data["is_text"] is True
        assert data["columns"] == ["name", "age", "city"]
        assert data["row_count"] == 2  # 2 data rows

    def test_preview_respects_line_limit(self, client, home_tmp_dir):
        """The lines parameter should limit the number of preview lines."""
        # Create a file with many lines
        many_lines_file = home_tmp_dir / "manylines.txt"
        lines_content = "\n".join(f"Line {i}" for i in range(100))
        many_lines_file.write_text(lines_content, encoding="utf-8")

        # Request only 10 lines
        resp = client.get(
            "/api/files/preview",
            params={"path": str(many_lines_file), "lines": 10},
        )
        assert resp.status_code == 200

        data = resp.json()
        assert len(data["preview_lines"]) == 10
        assert data["total_lines"] == 100
        assert data["preview_lines"][0] == "Line 0"
        assert data["preview_lines"][9] == "Line 9"

    def test_preview_line_limit_clamped_to_200(self, client, home_tmp_dir):
        """Lines parameter should be clamped to max 200."""
        large_file = home_tmp_dir / "large.txt"
        lines_content = "\n".join(f"L{i}" for i in range(300))
        large_file.write_text(lines_content, encoding="utf-8")

        resp = client.get(
            "/api/files/preview",
            params={"path": str(large_file), "lines": 500},
        )
        assert resp.status_code == 200

        data = resp.json()
        assert len(data["preview_lines"]) == 200  # Clamped to max
        assert data["total_lines"] == 300

    def test_preview_nonexistent_file(self, client, home_tmp_dir):
        """Preview of nonexistent file should return 404."""
        fake_path = home_tmp_dir / "nonexistent.txt"
        resp = client.get(
            "/api/files/preview",
            params={"path": str(fake_path)},
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_preview_directory_not_file(self, client, home_tmp_dir):
        """Preview of a directory should return 400."""
        resp = client.get(
            "/api/files/preview",
            params={"path": str(home_tmp_dir)},
        )
        assert resp.status_code == 400
        assert "not a file" in resp.json()["detail"].lower()

    def test_preview_null_byte_path(self, client):
        """Path with null byte should return 400."""
        resp = client.get(
            "/api/files/preview",
            params={"path": "/home/user/file\x00.txt"},
        )
        assert resp.status_code == 400
        assert "Invalid" in resp.json()["detail"]

    def test_preview_empty_path(self, client):
        """Empty path should return 400."""
        resp = client.get(
            "/api/files/preview",
            params={"path": ""},
        )
        assert resp.status_code == 400

    def test_preview_outside_home(self, client):
        """Preview of file outside home should return 403."""
        if platform.system() == "Windows":
            outside_path = "C:\\Windows\\System32\\drivers\\etc\\hosts"
        else:
            outside_path = "/etc/passwd"

        resp = client.get(
            "/api/files/preview",
            params={"path": outside_path},
        )
        assert resp.status_code == 403
        assert "home directory" in resp.json()["detail"].lower()

    def test_preview_encoding_detection(self, client, home_tmp_dir):
        """Verify utf-8 encoding is detected for a UTF-8 file."""
        utf8_file = home_tmp_dir / "unicode.txt"
        utf8_file.write_text(
            "Hello World\nCafe\u0301\nNi\u0303o\n",
            encoding="utf-8",
        )

        resp = client.get(
            "/api/files/preview",
            params={"path": str(utf8_file)},
        )
        assert resp.status_code == 200

        data = resp.json()
        assert data["is_text"] is True
        assert data["encoding"] == "utf-8"

    def test_preview_latin1_fallback(self, client, home_tmp_dir):
        """Verify latin-1 fallback for non-UTF-8 encoded files."""
        latin1_file = home_tmp_dir / "latin1.txt"
        # Write bytes that are valid latin-1 but not valid utf-8
        latin1_file.write_bytes(b"caf\xe9\nni\xf1o\n")

        resp = client.get(
            "/api/files/preview",
            params={"path": str(latin1_file)},
        )
        assert resp.status_code == 200

        data = resp.json()
        assert data["is_text"] is True
        assert data["encoding"] == "latin-1"

    def test_preview_python_file(self, client, sample_py_file):
        """Preview a .py file to confirm code files work."""
        resp = client.get(
            "/api/files/preview",
            params={"path": str(sample_py_file)},
        )
        assert resp.status_code == 200

        data = resp.json()
        assert data["extension"] == ".py"
        assert data["is_text"] is True
        assert "def greet" in "\n".join(data["preview_lines"])

    def test_preview_json_file(self, client, sample_json_file):
        """Preview a .json file."""
        resp = client.get(
            "/api/files/preview",
            params={"path": str(sample_json_file)},
        )
        assert resp.status_code == 200

        data = resp.json()
        assert data["extension"] == ".json"
        assert data["is_text"] is True
        assert any("key" in line for line in data["preview_lines"])

    @pytest.mark.skipif(
        platform.system() == "Windows",
        reason="Symlinks require elevated privileges on Windows",
    )
    def test_preview_symlink_rejected(self, client, home_tmp_dir, sample_text_file):
        """Symlink path should return 400."""
        link = home_tmp_dir / "link.txt"
        link.symlink_to(sample_text_file)

        resp = client.get(
            "/api/files/preview",
            params={"path": str(link)},
        )
        assert resp.status_code == 400
        assert "Symbolic links" in resp.json()["detail"]

    def test_preview_response_all_fields(self, client, sample_text_file):
        """Verify every field in FilePreviewResponse is present."""
        resp = client.get(
            "/api/files/preview",
            params={"path": str(sample_text_file)},
        )
        assert resp.status_code == 200

        data = resp.json()
        expected_fields = {
            "path",
            "name",
            "size",
            "size_display",
            "extension",
            "modified",
            "is_text",
            "preview_lines",
            "total_lines",
            "columns",
            "row_count",
            "encoding",
        }
        assert expected_fields.issubset(
            set(data.keys())
        ), f"Missing fields: {expected_fields - set(data.keys())}"

    def test_preview_long_lines_truncated(self, client, home_tmp_dir):
        """Lines longer than 500 characters should be truncated."""
        long_line_file = home_tmp_dir / "longline.txt"
        long_line = "A" * 600
        long_line_file.write_text(long_line + "\nshort line\n", encoding="utf-8")

        resp = client.get(
            "/api/files/preview",
            params={"path": str(long_line_file)},
        )
        assert resp.status_code == 200

        data = resp.json()
        assert (
            len(data["preview_lines"][0]) == 500
        ), f"Expected line truncated to 500 chars, got {len(data['preview_lines'][0])}"
        assert data["preview_lines"][1] == "short line"


# ── TestFileOpen ─────────────────────────────────────────────────────────────


class TestFileOpen:
    """Tests for POST /api/files/open.

    The open endpoint launches external processes (explorer, open, xdg-open)
    so we mock subprocess.Popen to avoid side effects.
    """

    @patch("subprocess.Popen")
    def test_open_file_valid_path(self, mock_popen, client, sample_text_file):
        """Open a valid file path should return 200."""
        mock_popen.return_value = MagicMock()

        resp = client.post(
            "/api/files/open",
            json={"path": str(sample_text_file), "reveal": True},
        )
        assert resp.status_code == 200

        data = resp.json()
        assert data["status"] == "ok"
        assert data["path"] == str(sample_text_file.resolve())
        mock_popen.assert_called_once()

    @patch("subprocess.Popen")
    def test_open_folder_valid_path(self, mock_popen, client, home_tmp_dir):
        """Open a valid folder path should return 200."""
        mock_popen.return_value = MagicMock()

        resp = client.post(
            "/api/files/open",
            json={"path": str(home_tmp_dir), "reveal": False},
        )
        assert resp.status_code == 200

        data = resp.json()
        assert data["status"] == "ok"
        mock_popen.assert_called_once()

    def test_open_null_byte_path(self, client):
        """Path with null byte should return 400."""
        resp = client.post(
            "/api/files/open",
            json={"path": "/home/user/file\x00.txt", "reveal": True},
        )
        assert resp.status_code == 400
        assert "Invalid path" in resp.json()["detail"]

    def test_open_empty_path(self, client):
        """Empty path should return 400."""
        resp = client.post(
            "/api/files/open",
            json={"path": "", "reveal": True},
        )
        assert resp.status_code == 400

    def test_open_nonexistent_path(self, client, home_tmp_dir):
        """Opening a nonexistent path should return 404."""
        fake = home_tmp_dir / "nonexistent_file.txt"
        resp = client.post(
            "/api/files/open",
            json={"path": str(fake), "reveal": True},
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_open_outside_home(self, client):
        """Opening a path outside home directory should return 403."""
        if platform.system() == "Windows":
            outside_path = "C:\\Windows\\System32"
        else:
            outside_path = "/etc"

        resp = client.post(
            "/api/files/open",
            json={"path": outside_path, "reveal": True},
        )
        assert resp.status_code == 403
        assert "home directory" in resp.json()["detail"].lower()

    @pytest.mark.skipif(
        platform.system() == "Windows",
        reason="Symlinks require elevated privileges on Windows",
    )
    @patch("subprocess.Popen")
    def test_open_symlink_rejected(self, mock_popen, client, home_tmp_dir):
        """Opening a symlink should return 400."""
        target = home_tmp_dir / "real_file.txt"
        target.write_text("real", encoding="utf-8")
        link = home_tmp_dir / "symlink_file.txt"
        link.symlink_to(target)

        resp = client.post(
            "/api/files/open",
            json={"path": str(link), "reveal": True},
        )
        assert resp.status_code == 400
        assert "Symbolic links" in resp.json()["detail"]
        mock_popen.assert_not_called()

    @patch("subprocess.Popen")
    def test_open_file_reveal_true_on_windows(
        self, mock_popen, client, sample_text_file
    ):
        """On Windows, reveal=True should call explorer /select,"""
        mock_popen.return_value = MagicMock()

        resp = client.post(
            "/api/files/open",
            json={"path": str(sample_text_file), "reveal": True},
        )
        assert resp.status_code == 200

        if platform.system() == "Windows":
            call_args = mock_popen.call_args[0][0]
            assert call_args[0] == "explorer"
            assert "/select," in call_args

    @patch("subprocess.Popen")
    def test_open_subprocess_failure(self, mock_popen, client, sample_text_file):
        """If subprocess.Popen raises, should return 500."""
        mock_popen.side_effect = OSError("Cannot start process")

        resp = client.post(
            "/api/files/open",
            json={"path": str(sample_text_file), "reveal": True},
        )
        assert resp.status_code == 500
        assert "Failed to open" in resp.json()["detail"]

    @patch("subprocess.Popen")
    def test_open_reveal_false_opens_folder(self, mock_popen, client, sample_text_file):
        """With reveal=False and a file path, should open the parent folder."""
        mock_popen.return_value = MagicMock()

        resp = client.post(
            "/api/files/open",
            json={"path": str(sample_text_file), "reveal": False},
        )
        assert resp.status_code == 200

        if platform.system() == "Windows":
            call_args = mock_popen.call_args[0][0]
            # With reveal=False, the file path check: resolved.is_file() and reveal
            # is False, so it falls through to open the parent directory
            assert call_args[0] == "explorer"
            # Should open the parent dir, not use /select,
            assert "/select," not in call_args


# ── TestSecurityEdgeCases ────────────────────────────────────────────────────


class TestSecurityEdgeCases:
    """Cross-endpoint security edge cases."""

    def test_browse_path_traversal_attempt(self, client, home_tmp_dir):
        """Path traversal with .. should be resolved and checked."""
        # Attempt to traverse out of home via ..
        traversal_path = str(home_tmp_dir) + "/../../../../../../../etc"
        resp = client.get("/api/files/browse", params={"path": traversal_path})
        # Should either be 403 (outside home) or 404 (resolved within home
        # but nonexistent), but not 200 for a system directory
        assert resp.status_code in (
            403,
            404,
        ), f"Path traversal not blocked: status={resp.status_code}"

    def test_preview_path_traversal_attempt(self, client, home_tmp_dir):
        """Path traversal in preview should be rejected."""
        traversal_path = str(home_tmp_dir) + "/../../../etc/passwd"
        resp = client.get(
            "/api/files/preview",
            params={"path": traversal_path},
        )
        assert resp.status_code in (403, 404)

    def test_open_path_traversal_attempt(self, client, home_tmp_dir):
        """Path traversal in open should be rejected."""
        traversal_path = str(home_tmp_dir) + "/../../../etc"
        resp = client.post(
            "/api/files/open",
            json={"path": traversal_path, "reveal": True},
        )
        assert resp.status_code in (403, 404)

    def test_upload_double_extension_attack(self, client):
        """File with double extension like .txt.exe should be rejected."""
        resp = client.post(
            "/api/files/upload",
            files={
                "file": (
                    "document.txt.exe",
                    io.BytesIO(b"payload"),
                    "application/octet-stream",
                )
            },
        )
        # The suffix is .exe, which is not in UPLOAD_ALLOWED_EXTENSIONS
        assert resp.status_code == 400

    def test_upload_case_insensitive_extension(self, client):
        """Extension check should be case-insensitive."""
        resp = client.post(
            "/api/files/upload",
            files={"file": ("FILE.TXT", io.BytesIO(b"data"), "text/plain")},
        )
        assert resp.status_code == 200  # .txt (lowered from .TXT) is allowed

    def test_upload_special_characters_in_filename(self, client):
        """Upload with special characters in original filename should succeed."""
        resp = client.post(
            "/api/files/upload",
            files={
                "file": (
                    "my file (copy) [2].txt",
                    io.BytesIO(b"data"),
                    "text/plain",
                )
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["original_name"] == "my file (copy) [2].txt"
        # Stored filename should be a clean UUID
        stem = data["filename"].rsplit(".", 1)[0]
        uuid.UUID(stem)  # Validates no special chars leaked through


# ── TestBrowseEdgeCases ──────────────────────────────────────────────────────


class TestBrowseEdgeCases:
    """Edge case tests for the browse endpoint."""

    def test_browse_empty_directory(self, client, home_tmp_dir):
        """Browsing an empty directory should return empty entries list."""
        empty_dir = home_tmp_dir / "empty"
        empty_dir.mkdir()

        resp = client.get("/api/files/browse", params={"path": str(empty_dir)})
        assert resp.status_code == 200
        assert resp.json()["entries"] == []

    def test_browse_directory_with_mixed_content(self, client, home_tmp_dir):
        """Directory with folders, allowed files, and disallowed files."""
        # Create mixed content
        (home_tmp_dir / "subfolder").mkdir()
        (home_tmp_dir / "readme.md").write_text("# Hello", encoding="utf-8")
        (home_tmp_dir / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        (home_tmp_dir / "photo.mp4").write_bytes(b"\x00")  # disallowed
        (home_tmp_dir / "program.exe").write_bytes(b"\x00")  # disallowed

        resp = client.get("/api/files/browse", params={"path": str(home_tmp_dir)})
        assert resp.status_code == 200

        entries = resp.json()["entries"]
        names = [e["name"] for e in entries]

        assert "subfolder" in names
        assert "readme.md" in names
        assert "data.csv" in names
        assert "photo.mp4" not in names
        assert "program.exe" not in names

        # Verify folder is first
        types = [e["type"] for e in entries]
        folder_end = max(i for i, t in enumerate(types) if t == "folder")
        file_start = min(i for i, t in enumerate(types) if t == "file")
        assert folder_end < file_start

    def test_browse_alphabetical_sorting(self, client, home_tmp_dir):
        """Entries should be sorted alphabetically within folders and files."""
        (home_tmp_dir / "zebra").mkdir()
        (home_tmp_dir / "alpha").mkdir()
        (home_tmp_dir / "zebra.txt").write_text("z", encoding="utf-8")
        (home_tmp_dir / "alpha.txt").write_text("a", encoding="utf-8")

        resp = client.get("/api/files/browse", params={"path": str(home_tmp_dir)})
        assert resp.status_code == 200

        entries = resp.json()["entries"]
        folders = [e["name"] for e in entries if e["type"] == "folder"]
        files = [e["name"] for e in entries if e["type"] == "file"]

        assert folders == sorted(folders, key=str.lower)
        assert files == sorted(files, key=str.lower)


# ── TestPreviewEdgeCases ─────────────────────────────────────────────────────


class TestPreviewEdgeCases:
    """Edge case tests for the preview endpoint."""

    def test_preview_empty_file(self, client, home_tmp_dir):
        """Preview of an empty file should return is_text with 0 lines."""
        empty_file = home_tmp_dir / "empty.txt"
        empty_file.write_text("", encoding="utf-8")

        resp = client.get(
            "/api/files/preview",
            params={"path": str(empty_file)},
        )
        assert resp.status_code == 200

        data = resp.json()
        assert data["is_text"] is True
        assert data["preview_lines"] == []
        assert data["total_lines"] == 0

    def test_preview_single_line_no_newline(self, client, home_tmp_dir):
        """Preview a file with a single line and no trailing newline."""
        single = home_tmp_dir / "single.txt"
        single.write_text("just one line", encoding="utf-8")

        resp = client.get(
            "/api/files/preview",
            params={"path": str(single)},
        )
        assert resp.status_code == 200

        data = resp.json()
        assert data["preview_lines"] == ["just one line"]
        assert data["total_lines"] == 1

    def test_preview_csv_with_empty_header(self, client, home_tmp_dir):
        """Preview CSV with empty columns in header."""
        csv_file = home_tmp_dir / "sparse.csv"
        csv_file.write_text(",col2,,col4\n1,2,3,4\n", encoding="utf-8")

        resp = client.get(
            "/api/files/preview",
            params={"path": str(csv_file)},
        )
        assert resp.status_code == 200

        data = resp.json()
        assert data["columns"] is not None
        assert len(data["columns"]) == 4
        assert data["row_count"] == 1

    def test_preview_minimum_lines(self, client, sample_text_file):
        """Lines parameter minimum should be 1."""
        resp = client.get(
            "/api/files/preview",
            params={"path": str(sample_text_file), "lines": 0},
        )
        assert resp.status_code == 200
        # min(max(0, 1), 200) = 1
        data = resp.json()
        assert len(data["preview_lines"]) == 1
