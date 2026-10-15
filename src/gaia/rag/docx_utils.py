# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Word (.docx) text extraction shared by RAG indexing and the file read tools."""

import zipfile
from pathlib import Path

#: Uncompressed size above which a .docx is refused as a likely zip bomb.
MAX_UNCOMPRESSED_BYTES = 500 * 1024 * 1024

_CORRUPT_HINT = (
    "The file appears to be corrupted or not a valid .docx file.\n"
    "Suggestions:\n"
    "  1. Re-download or re-export the document\n"
    "  2. Try opening the file in Word to confirm it is readable\n"
    "  3. Save as PDF and index the PDF instead"
)


def extract_docx_text(docx_path: str) -> str:
    """Extract the text of a Word (.docx) document in reading order.

    Walks the document body in order so paragraphs and tables stay
    interleaved. Paragraph text is collected by walking the inline tree,
    so runs nested in hyperlinks, **content controls** (``w:sdt`` — the
    fields used by form/template documents), and textboxes are captured —
    not just the direct runs that ``Paragraph.text`` exposes. Tabs and
    line/page breaks become whitespace so adjacent words don't glue
    together, and the ``mc:Fallback`` (VML) twin of a DrawingML textbox is
    skipped so shape text isn't emitted twice. Table cells (including
    tables nested in a cell, and rows/cells wrapped in repeating-section
    content controls) and block-level content controls are recursed into.

    Known omissions: header/footer text (separate XML parts, usually
    repeated boilerplate) and embedded images (TODO #1072: VLM extraction
    for images embedded in .docx files).

    Raises:
        ImportError: python-docx is not installed.
        ValueError: the file is missing, unreadable, corrupt, or a zip bomb.
    """
    file_name = Path(docx_path).name

    try:
        from docx import Document  # pylint: disable=import-outside-toplevel
        from docx.oxml.ns import qn  # pylint: disable=import-outside-toplevel
    except ImportError as e:
        raise ImportError(
            "python-docx is required for Word document processing. "
            "Install it with: uv pip install python-docx"
        ) from e

    # .docx is a ZIP container: check the uncompressed size before parsing.
    try:
        with zipfile.ZipFile(docx_path, "r") as zf:
            total_uncompressed = sum(info.file_size for info in zf.infolist())
    except zipfile.BadZipFile as e:
        raise ValueError(
            f"Could not read Word file: {file_name}\nReason: {e}\n{_CORRUPT_HINT}"
        ) from e
    except OSError as e:
        raise ValueError(
            f"Could not read Word file: {file_name}\n"
            f"Reason: {e}\n"
            "Check that the path exists and points to a readable .docx file."
        ) from e
    if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
        raise ValueError(
            f"Word file too large after decompression: {file_name}\n"
            f"Uncompressed size: {total_uncompressed / (1024*1024):.0f} MB "
            f"(limit: {MAX_UNCOMPRESSED_BYTES / (1024*1024):.0f} MB)\n"
            "The file may be a zip bomb or contain very large embedded media.\n"
            "Suggestions:\n"
            "  1. Remove unnecessary images/media to reduce file size\n"
            "  2. Save as PDF and index the PDF instead"
        )

    try:
        doc = Document(docx_path)
    except Exception as e:
        raise ValueError(
            f"Could not read Word file: {file_name}\nReason: {e}\n{_CORRUPT_HINT}"
        ) from e

    w_p, w_tbl, w_sdt = qn("w:p"), qn("w:tbl"), qn("w:sdt")
    w_sdt_content = qn("w:sdtContent")
    w_tr, w_tc = qn("w:tr"), qn("w:tc")
    w_t, w_tab = qn("w:t"), qn("w:tab")
    w_br, w_cr = qn("w:br"), qn("w:cr")
    # ``mc`` (markup-compatibility) isn't in python-docx's prefix map,
    # so qn() can't resolve it — use the namespace URI directly.
    mc_fallback = (
        "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
    )

    def _para_runs(elem, out):
        # Walk a paragraph's inline tree in document order, translating
        # leaf elements to text. Descends through runs, hyperlinks,
        # inline content controls (w:sdt), and textbox content so their
        # text is captured — but skips ``mc:Fallback`` (the VML twin of
        # a DrawingML textbox) so shape text is not emitted twice.
        for child in elem:
            tag = child.tag
            if tag == w_t:
                out.append(child.text or "")
            elif tag == w_tab:
                out.append("\t")
            elif tag in (w_br, w_cr):
                out.append("\n")
            elif tag == mc_fallback:
                continue
            else:
                _para_runs(child, out)

    def _paragraph_text(p_elem):
        out = []
        _para_runs(p_elem, out)
        return "".join(out).strip()

    def _iter_children(elem, wanted):
        # Yield direct ``wanted`` children, transparently descending
        # through w:sdt wrappers (repeating-section / row / cell content
        # controls keep their rows and cells inside w:sdtContent).
        for child in elem:
            if child.tag == wanted:
                yield child
            elif child.tag == w_sdt:
                content = child.find(w_sdt_content)
                if content is not None:
                    yield from _iter_children(content, wanted)

    def _emit(elem, parts):
        # Recursively walk a block element in document order, appending
        # text fragments. Unknown tags (section props, etc.) are skipped.
        if elem.tag == w_p:
            para_text = _paragraph_text(elem)
            if para_text:
                parts.append(para_text)
        elif elem.tag == w_tbl:
            for row in _iter_children(elem, w_tr):
                cells = []
                for cell in _iter_children(row, w_tc):
                    cell_parts = []
                    for cell_child in cell:
                        _emit(cell_child, cell_parts)
                    cells.append(" ".join(cell_parts).strip())
                if any(cells):
                    parts.append(" | ".join(cells))
        elif elem.tag == w_sdt:
            # Block-level content control — descend into its content.
            content = elem.find(w_sdt_content)
            if content is not None:
                for sdt_child in content:
                    _emit(sdt_child, parts)

    parts = []
    for child in doc.element.body:
        _emit(child, parts)
    return "\n".join(parts)
