# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A page whose image inventory cannot be read is not a page with no images.

`count_images_in_page` swallowed a resolution failure and returned `(False, 0)`
— identical to a text-only page. That boolean is the only gate on the vision
path, so a damaged page was never extracted, never reported, and contributed an
empty chunk indistinguishable from a genuinely blank one. The user got an answer
quietly missing whatever the page held (#3551).

No Lemonade server and no real PDF required — the pypdf page object is a
mapping, so a stand-in that raises on resource access reproduces the condition
exactly.
"""

from __future__ import annotations

import pytest

from gaia.rag.pdf_utils import PdfPageInspectionError, count_images_in_page


class _Page(dict):
    """A pypdf-like page. `resources` may be a value or an exception to raise."""

    def __init__(self, resources):
        super().__init__()
        self._resources = resources

    def get(self, key, default=None):
        if key == "/Resources":
            if isinstance(self._resources, Exception):
                raise self._resources
            return self._resources
        return super().get(key, default)

    def __getitem__(self, key):
        if key == "/Resources":
            if isinstance(self._resources, Exception):
                raise self._resources
            return self._resources
        return super().__getitem__(key)


class _Xobject(dict):
    def get_object(self):
        return self


def _page_with_images(count):
    xobj = _Xobject({f"/Im{i}": {"/Subtype": "/Image"} for i in range(count)})
    return _Page({"/XObject": xobj})


class TestInspectionIsThreeStateNotTwo:
    def test_a_page_with_images_is_counted(self):
        assert count_images_in_page(_page_with_images(3)) == (True, 3)

    def test_a_text_only_page_reports_no_images(self):
        assert count_images_in_page(_Page({})) == (False, 0)

    def test_a_non_image_xobject_is_not_counted(self):
        xobj = _Xobject({"/Fm0": {"/Subtype": "/Form"}})
        assert count_images_in_page(_Page({"/XObject": xobj})) == (False, 0)

    def test_an_unreadable_page_raises_rather_than_reporting_none(self):
        """The whole bug: this used to be indistinguishable from (False, 0)."""
        damaged = _Page(ValueError("broken xref"))

        with pytest.raises(PdfPageInspectionError):
            count_images_in_page(damaged, page_num=4)

    def test_the_error_names_the_page_and_the_cause(self):
        damaged = _Page(ValueError("broken xref"))

        with pytest.raises(PdfPageInspectionError) as excinfo:
            count_images_in_page(damaged, page_num=4)

        assert excinfo.value.page_num == 4
        assert "page 4" in str(excinfo.value)
        assert "broken xref" in str(excinfo.value)
        assert isinstance(excinfo.value.__cause__, ValueError)


class TestAnUnreadablePageStillGetsATry:
    """The inventory comes from pypdf; the extraction comes from PyMuPDF.

    A page pypdf cannot inspect may still extract fine, so "unknown" has to
    mean "attempt it", not "skip it" — which is what `(False, 0)` meant.
    """

    def test_extraction_does_not_reuse_the_page_object_that_failed(self):
        """Guards the reasoning above: if these ever unify, revisit the fix.

        The extractor takes a *path* and reopens the document itself, so it
        never touches the pypdf page whose resources could not be resolved.
        Asserted on the signature rather than the body, so editing a comment in
        that function does not break this.
        """
        import inspect

        from gaia.rag import pdf_utils

        signature = inspect.signature(pdf_utils.extract_images_from_page_pymupdf)
        assert list(signature.parameters) == ["pdf_path", "page_num"]


# ============================================================================
# The indexer: a degraded page is recorded, not quietly blank
# ============================================================================


class TestTheIndexerRecordsWhatItCouldNotRead:
    """`_extract_text_from_pdf` is driven with a stubbed reader and VLM.

    The point is the bookkeeping, not the PDF parsing: a page that could not be
    inspected has to reach the caller as *named* and possibly incomplete.
    """

    @staticmethod
    def _sdk():
        from gaia.rag.sdk import RAGSDK, RAGConfig

        sdk = RAGSDK.__new__(RAGSDK)
        sdk.config = RAGConfig(show_stats=False)
        sdk.log = __import__("logging").getLogger("test-rag")
        return sdk

    @pytest.fixture(autouse=True)
    def _stub_pdf_on_disk(self, tmp_path):
        """The reader is stubbed, but the path still has to exist."""
        self._pdf = tmp_path / "doc.pdf"
        self._pdf.write_bytes(b"%PDF-1.4\n")

    def _run(self, mocker, pages, inspect_results):
        """Extract over *pages*, with count_images_in_page scripted."""
        sdk = self._sdk()

        reader = mocker.MagicMock()
        reader.pages = pages
        reader.is_encrypted = False
        mocker.patch("gaia.rag.sdk.PdfReader", return_value=reader)

        vlm = mocker.MagicMock()
        vlm.check_availability.return_value = True
        vlm.extract_from_page_images.return_value = []
        mocker.patch("gaia.llm.VLMClient", return_value=vlm)
        mocker.patch(
            "gaia.rag.pdf_utils.count_images_in_page", side_effect=inspect_results
        )
        mocker.patch(
            "gaia.rag.pdf_utils.extract_images_from_page_pymupdf", return_value=[]
        )
        return sdk._extract_text_from_pdf(str(self._pdf))

    @staticmethod
    def _text_page(mocker, text):
        page = mocker.MagicMock()
        page.extract_text.return_value = text
        return page

    def test_a_damaged_page_is_named_in_the_metadata(self, mocker):
        pages = [
            self._text_page(mocker, "page one text"),
            self._text_page(mocker, "page two text"),
        ]
        _, total, metadata = self._run(
            mocker,
            pages,
            [(False, 0), PdfPageInspectionError("broken xref", 2)],
        )

        assert total == 2
        assert metadata["degraded_pages"] == [2]
        assert metadata["pdf_status"] == "degraded"

    def test_a_clean_document_is_not_marked_degraded(self, mocker):
        pages = [self._text_page(mocker, "all fine")]
        _, _, metadata = self._run(mocker, pages, [(False, 0)])

        assert metadata["pdf_status"] == "readable"
        assert "degraded_pages" not in metadata

    def test_a_damaged_page_still_has_its_text_indexed(self, mocker):
        """The text pypdf *could* read is not thrown away with the inventory."""
        pages = [self._text_page(mocker, "text that did extract")]
        full_text, _, _ = self._run(
            mocker, pages, [PdfPageInspectionError("broken xref", 1)]
        )

        assert "text that did extract" in full_text


# ============================================================================
# The report has to reach a caller, not just the log
# ============================================================================


class TestTheDegradedReportReachesTheCaller:
    """The first version assembled this and dropped it two layers down.

    `_extract_text_from_file` copied only `vlm_pages`/`total_images`, and the
    index step then hardcoded `pdf_status = "readable"`. The extractor's return
    value looked right, so tests that inspected it passed while nothing a
    caller sees ever changed. These go through the layers that were broken.
    """

    @staticmethod
    def _sdk():
        from gaia.rag.sdk import RAGSDK, RAGConfig

        sdk = RAGSDK.__new__(RAGSDK)
        sdk.config = RAGConfig(show_stats=False)
        sdk.log = __import__("logging").getLogger("test-rag")
        return sdk

    def test_extract_text_from_file_carries_the_status_up(self, mocker, tmp_path):
        sdk = self._sdk()
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        mocker.patch.object(
            sdk,
            "_extract_text_from_pdf",
            return_value=(
                "some text",
                3,
                {
                    "vlm_pages": 0,
                    "total_images": 0,
                    "pdf_status": "degraded",
                    "degraded_pages": [2],
                    "page_warnings": {2: "could not read the image inventory"},
                },
            ),
        )

        _, metadata = sdk._extract_text_from_file(str(pdf))

        assert metadata["pdf_status"] == "degraded"
        assert metadata["degraded_pages"] == [2]
        assert metadata["page_warnings"] == {2: "could not read the image inventory"}

    def test_a_clean_pdf_still_reports_readable(self, mocker, tmp_path):
        sdk = self._sdk()
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        mocker.patch.object(
            sdk,
            "_extract_text_from_pdf",
            return_value=("text", 1, {"vlm_pages": 0, "total_images": 0}),
        )

        _, metadata = sdk._extract_text_from_file(str(pdf))

        assert metadata["pdf_status"] == "readable"
        assert "degraded_pages" not in metadata

    def test_the_index_step_does_not_overwrite_degraded_with_readable(self):
        """It hardcoded "readable" for every successful .pdf index."""
        import inspect

        from gaia.rag.sdk import RAGSDK

        source = inspect.getsource(RAGSDK.index_document)
        assert 'stats["pdf_status"] = "readable"' not in source
        assert 'file_metadata.get("pdf_status"' in source

    def test_the_page_warning_is_not_written_somewhere_nothing_reads(self):
        """`pages_data` is local — a key there would be a record nobody sees."""
        import inspect

        from gaia.rag.sdk import RAGSDK

        source = inspect.getsource(RAGSDK._extract_text_from_pdf)
        assert '"extraction_warning"' not in source
        assert "page_warnings[i]" in source
