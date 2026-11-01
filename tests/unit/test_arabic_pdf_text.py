# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Arabic PDF text must reach the RAG index whole: digits and Latin runs
inside an Arabic line, and base letters instead of presentation forms."""

import io

import pytest

pypdf = pytest.importorskip("pypdf")

from gaia.rag.arabic_text import normalize_arabic_pdf_text  # noqa: E402

# Bytes the content stream draws -> the text ToUnicode says they are.
_GLYPHS = {
    "A": "ب",  # ب
    "B": "ي",  # ي
    "C": "ع",  # ع
    "D": "ﻻ",  # ﻻ  lam-alef ligature (presentation form)
}


def _pdf(content: bytes) -> bytes:
    """A one-page PDF whose font maps A-D to Arabic and leaves ASCII as-is."""
    bfchar = "".join(
        f"<{ord(k):02X}> <{ord(v):04X}>\n" for k, v in _GLYPHS.items()
    ) + "".join(f"<{c:02X}> <{c:04X}>\n" for c in range(0x20, 0x41))
    cmap = (
        "/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
        "/CMapName /T def 1 begincodespacerange <00> <FF> endcodespacerange\n"
        f"{len(_GLYPHS) + 0x21} beginbfchar\n{bfchar}endbfchar\n"
        "endcmap CMapName currentdict /CMap defineresource pop end end"
    ).encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /ToUnicode 6 0 R >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(cmap), cmap),
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n%s\nendobj\n" % (i, body))
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (len(objs) + 1, xref)
    )
    return out.getvalue()


def _extract(content: bytes) -> str:
    reader = pypdf.PdfReader(io.BytesIO(_pdf(content)))
    return reader.pages[0].extract_text()


def test_digits_inside_an_arabic_run_survive_extraction():
    text = _extract(b"BT /F1 12 Tf 10 100 Td (ABC 40% CBA) Tj ET")

    assert "40" in text
    assert "بيع" in text.replace(" ", "") or "عيب" in text
    # Every Arabic letter drawn is still there, on both sides of the number.
    assert text.count("ب") == 2 and text.count("ع") == 2


def test_latin_inside_an_arabic_run_survives_extraction():
    text = _extract(b"BT /F1 12 Tf 10 100 Td (AB net CA) Tj ET")

    assert "net" in text
    assert text.count("ب") == 2


def test_presentation_forms_become_base_letters():
    text = normalize_arabic_pdf_text(_extract(b"BT /F1 12 Tf 10 100 Td (DA) Tj ET"))

    assert "ﻻ" not in text
    assert "لا" in text  # lam, alef — logical order


def test_arabic_page_gets_arabic_yeh_and_heh_back():
    # Chrome's PDFs report medial yeh and heh with the Persian letters.
    text = normalize_arabic_pdf_text("دلیل ھذا في")

    assert text == "دليل هذا في"


def test_persian_page_keeps_its_letters():
    persian = "کتابی برای شما"

    assert normalize_arabic_pdf_text(persian) == persian


def test_normalization_leaves_other_compatibility_characters_alone():
    assert normalize_arabic_pdf_text("m² ﬁle") == "m² ﬁle"
    assert normalize_arabic_pdf_text("") == ""
