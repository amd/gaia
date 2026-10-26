# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Excel (.xlsx) extraction and the errors a user sees when a workbook can't be read.

The fixtures in ``tests/fixtures/xlsx/`` were saved by LibreOffice Calc, so
formulas carry cached values the way an Excel-saved file does:

- ``simple_labs.xlsx`` — one sheet, a header row and two readings
- ``lab_results.xlsx`` — a merged title row above the header, formulas,
  a merged note on a second sheet and an empty third sheet
- ``legacy_lab_results.xls`` — the same workbook in the old .xls format
"""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

openpyxl = pytest.importorskip("openpyxl")
pytest.importorskip("faiss", reason="RAGSDK requires faiss-cpu")

from gaia.rag.sdk import RAGSDK, RAGConfig, SpreadsheetReadError  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "xlsx"


@pytest.fixture
def rag(tmp_path: Path) -> RAGSDK:
    with patch("gaia.rag.sdk.AgentSDK"):
        return RAGSDK(
            RAGConfig(
                cache_dir=str(tmp_path / "cache"),
                allowed_paths=[str(tmp_path), str(FIXTURES)],
                show_stats=False,
                use_local_llm=False,
            )
        )


def _fake_encode(texts, show_progress=False, wait_callback=None):
    return np.ones((len(texts), 8), dtype="float32")


class TestExtraction:
    def test_simple_sheet_pairs_each_value_with_its_column(self, rag):
        text = rag._extract_text_from_xlsx(str(FIXTURES / "simple_labs.xlsx"))

        assert "Columns: Date, Test, Value, Unit" in text
        assert "Test: LDL | Value: 131 | Unit: mg/dL" in text

    def test_a_merged_title_row_is_not_taken_for_the_header(self, rag):
        text = rag._extract_text_from_xlsx(str(FIXTURES / "lab_results.xlsx"))

        assert "Lipid panel 2026\nColumns: Date, LDL, HDL, Ratio" in text
        assert "LDL: 131 | HDL: 48" in text
        assert "Lipid panel 2026: " not in text
        assert " | : " not in text

    def test_formulas_read_as_their_cached_values(self, rag):
        text = rag._extract_text_from_xlsx(str(FIXTURES / "lab_results.xlsx"))

        assert "Ratio: 2.729" in text
        assert "Date: Average LDL | LDL: 117.666" in text
        assert "=B3/C3" not in text

    def test_every_sheet_is_read(self, rag):
        text = rag._extract_text_from_xlsx(str(FIXTURES / "lab_results.xlsx"))

        assert "Sheet: Cholesterol" in text
        assert "Sheet: Notes" in text
        assert "Doctor note | Target LDL under 100" in text

    def test_a_blank_header_cell_gets_a_column_number(self, rag, tmp_path):
        wb = openpyxl.Workbook()
        wb.active.append(["Test", None, "Unit"])
        wb.active.append(["LDL", 131, "mg/dL"])
        path = tmp_path / "gap.xlsx"
        wb.save(path)

        text = rag._extract_text_from_xlsx(str(path))

        assert "Test: LDL | Col2: 131 | Unit: mg/dL" in text

    def test_a_single_column_list_keeps_its_header(self, rag, tmp_path):
        wb = openpyxl.Workbook()
        for value in ["Medication", "Atorvastatin", "Ezetimibe"]:
            wb.active.append([value])
        path = tmp_path / "list.xlsx"
        wb.save(path)

        text = rag._extract_text_from_xlsx(str(path))

        assert "Columns: Medication" in text
        assert "Medication: Ezetimibe" in text

    def test_a_one_cell_header_above_numeric_rows_stays_the_header(self, rag, tmp_path):
        wb = openpyxl.Workbook()
        for row in [
            ["Patient", None, None],
            ["Ann", 131, "mg/dL"],
            ["Bob", 120, "mg/dL"],
        ]:
            wb.active.append(row)
        path = tmp_path / "patients.xlsx"
        wb.save(path)

        text = rag._extract_text_from_xlsx(str(path))

        assert "Patient: Ann | Col2: 131 | Col3: mg/dL" in text
        assert "Patient: Bob | Col2: 120" in text
        assert "Columns: Ann" not in text


class TestUnreadableWorkbooks:
    def test_a_file_that_is_not_a_workbook_is_corrupted(self, rag, tmp_path):
        path = tmp_path / "labs.xlsx"
        path.write_bytes(b"this is not a zip archive")

        with pytest.raises(SpreadsheetReadError) as exc_info:
            rag._extract_text_from_xlsx(str(path))

        assert exc_info.value.status == "corrupted"
        assert "labs.xlsx" in str(exc_info.value)
        assert "save it again as .xlsx" in str(exc_info.value)

    def test_a_zip_that_is_not_a_workbook_is_corrupted(self, rag, tmp_path):
        path = tmp_path / "labs.xlsx"
        shutil.make_archive(str(tmp_path / "labs"), "zip", str(FIXTURES))
        (tmp_path / "labs.zip").rename(path)

        with pytest.raises(SpreadsheetReadError) as exc_info:
            rag._extract_text_from_xlsx(str(path))

        assert exc_info.value.status == "corrupted"

    def test_a_legacy_xls_renamed_to_xlsx_says_to_resave_it(self, rag, tmp_path):
        path = tmp_path / "labs.xlsx"
        shutil.copy(FIXTURES / "legacy_lab_results.xls", path)

        with pytest.raises(SpreadsheetReadError) as exc_info:
            rag._extract_text_from_xlsx(str(path))

        assert exc_info.value.status == "unsupported_workbook"
        assert "save a copy as .xlsx" in str(exc_info.value)

    @pytest.mark.parametrize(
        "content",
        [
            (FIXTURES / "simple_labs.xlsx").read_bytes(),
            b"<html><body><table><tr><td>LDL</td></tr></table></body></html>",
        ],
        ids=["xlsx-named-xls", "html-export"],
    )
    def test_any_xls_file_says_to_resave_it(self, rag, tmp_path, content):
        path = tmp_path / "labs.xls"
        path.write_bytes(content)

        with pytest.raises(SpreadsheetReadError) as exc_info:
            rag._extract_text_from_xlsx(str(path))

        assert exc_info.value.status == "unsupported_workbook"
        assert "openpyxl" not in str(exc_info.value)

    def test_the_library_error_stays_out_of_the_message(self, rag, tmp_path):
        path = tmp_path / "labs.xlsx"
        path.write_bytes(b"this is not a zip archive")

        with pytest.raises(SpreadsheetReadError) as exc_info:
            rag._extract_text_from_xlsx(str(path))

        assert "zip" not in str(exc_info.value).lower()

    def test_a_workbook_with_no_values_is_empty(self, rag, tmp_path):
        wb = openpyxl.Workbook()
        wb.create_sheet("Second")
        path = tmp_path / "blank.xlsx"
        wb.save(path)

        with pytest.raises(SpreadsheetReadError) as exc_info:
            rag._extract_text_from_xlsx(str(path))

        assert exc_info.value.status == "empty_file"
        assert "blank.xlsx has no cell values" in str(exc_info.value)

    def test_a_missing_openpyxl_names_the_install(self, rag, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def _no_openpyxl(name, *args, **kwargs):
            if name == "openpyxl":
                raise ImportError("No module named 'openpyxl'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _no_openpyxl)

        with pytest.raises(ImportError) as exc_info:
            rag._extract_text_from_xlsx(str(FIXTURES / "simple_labs.xlsx"))

        assert "openpyxl is required" in str(exc_info.value)
        assert "openpyxl" in str(exc_info.value).split("required", 1)[1]


class TestIndexDocument:
    def test_a_real_workbook_indexes(self, rag):
        with (
            patch.object(rag, "_load_embedder", return_value=None),
            patch.object(rag, "_encode_texts", side_effect=_fake_encode),
        ):
            stats = rag.index_document(str(FIXTURES / "lab_results.xlsx"))

        assert stats["success"] is True, stats.get("error")
        assert stats["num_chunks"] > 0

    @pytest.mark.parametrize(
        "content, kind",
        [
            (b"this is not a zip archive", "corrupted"),
            (
                (FIXTURES / "legacy_lab_results.xls").read_bytes(),
                "unsupported_workbook",
            ),
        ],
    )
    def test_an_unreadable_workbook_reports_its_kind(
        self, rag, tmp_path, content, kind
    ):
        path = tmp_path / "labs.xlsx"
        path.write_bytes(content)

        stats = rag.index_document(str(path))

        assert stats["success"] is False
        assert stats["xlsx_status"] == kind
        assert "labs.xlsx" in stats["error"]
