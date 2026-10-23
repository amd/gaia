# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Legacy .xls workbooks get an actionable "save as .xlsx" error, not an openpyxl crash."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gaia.rag.sdk import RAGSDK, RAGConfig


@pytest.fixture
def rag(tmp_path: Path) -> RAGSDK:
    config = RAGConfig(
        cache_dir=str(tmp_path / ".gaia"),
        show_stats=False,
        use_local_llm=False,
    )
    fake_vlm = MagicMock(name="VLMClient")
    fake_vlm.check_availability.return_value = False
    with (
        patch.object(RAGSDK, "_check_dependencies", return_value=None),
        patch("gaia.rag.sdk.AgentSDK", autospec=True) as mock_agent_sdk,
        patch("gaia.llm.VLMClient", return_value=fake_vlm),
    ):
        mock_agent_sdk.return_value = MagicMock(name="AgentSDK")
        yield RAGSDK(config=config)


def test_xls_raises_save_as_xlsx_error(rag, tmp_path):
    xls_path = tmp_path / "legacy.xls"
    # OLE2 compound-file signature, the container legacy .xls uses.
    xls_path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)

    with patch.object(rag, "_extract_text_from_xlsx") as xlsx_reader:
        with pytest.raises(ValueError, match=r"save it as \.xlsx") as exc:
            rag._extract_text_from_file(str(xls_path))

    assert "legacy.xls" in str(exc.value)
    xlsx_reader.assert_not_called()


def test_xlsx_still_routes_to_openpyxl_reader(rag, tmp_path):
    xlsx_path = tmp_path / "modern.xlsx"
    xlsx_path.write_bytes(b"PK")

    with patch.object(
        rag, "_extract_text_from_xlsx", return_value="Sheet: S1"
    ) as xlsx_reader:
        text, _ = rag._extract_text_from_file(str(xlsx_path))

    assert text == "Sheet: S1"
    xlsx_reader.assert_called_once_with(str(xlsx_path))
