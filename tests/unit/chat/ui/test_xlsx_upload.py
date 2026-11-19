# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What the Agent UI's upload route returns for real Excel workbooks.

Extraction itself is covered by ``tests/unit/rag/test_xlsx_extraction.py``;
this file needs the UI extras, so it runs in the UI lane.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

openpyxl = pytest.importorskip("openpyxl")
pytest.importorskip("faiss", reason="RAGSDK requires faiss-cpu")

from gaia.rag.sdk import RAGSDK  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "xlsx"


def _fake_encode(texts, show_progress=False, wait_callback=None):
    return np.ones((len(texts), 8), dtype="float32")


class TestIndexDocumentKind:
    @pytest.mark.asyncio
    async def test_the_ui_treats_it_as_a_permanent_failure(self):
        from gaia.ui._chat_helpers import UnreadableDocumentError, _index_document

        fake = MagicMock()
        fake.index_document.return_value = {
            "success": False,
            "error": "Could not read labs.xlsx: it is not a valid .xlsx workbook",
            "xlsx_status": "corrupted",
        }
        with (
            patch("gaia.rag.sdk.RAGSDK", return_value=fake),
            patch("gaia.rag.sdk.RAGConfig"),
        ):
            with pytest.raises(UnreadableDocumentError) as exc_info:
                await _index_document(Path("labs.xlsx"))

        assert exc_info.value.kind == "corrupted"


class TestUploadRoute:
    """The drag-and-drop upload the Agent UI calls, with the real RAG pipeline."""

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        from starlette.testclient import TestClient

        from gaia.ui.server import create_app

        monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
        return TestClient(create_app(db_path=":memory:"))

    @staticmethod
    def _upload(client, name, content):
        return client.post(
            "/api/documents/upload",
            files={"file": (name, content)},
            headers={"x-gaia-ui": "1", "x-gaia-client": "agent-ui"},
        )

    def test_a_real_workbook_uploads(self, client):
        with (
            patch.object(RAGSDK, "_load_embedder", return_value=None),
            patch.object(RAGSDK, "_encode_texts", side_effect=_fake_encode),
        ):
            resp = self._upload(
                client, "lab_results.xlsx", (FIXTURES / "lab_results.xlsx").read_bytes()
            )

        assert resp.status_code == 200, resp.text
        assert resp.json()["chunk_count"] > 0

    def test_a_corrupt_workbook_is_a_422_with_a_remedy(self, client):
        resp = self._upload(client, "labs.xlsx", b"this is not a zip archive")

        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["status"] == "corrupted"
        assert "labs.xlsx" in detail["message"]
        assert "save it again as .xlsx" in detail["message"]

    def test_an_empty_workbook_is_a_422(self, client, tmp_path):
        path = tmp_path / "blank.xlsx"
        openpyxl.Workbook().save(path)

        resp = self._upload(client, "blank.xlsx", path.read_bytes())

        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["status"] == "empty_file"
