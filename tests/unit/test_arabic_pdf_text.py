# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Arabic PDF text must reach the RAG index whole: digits and Latin runs
inside an Arabic line, and base letters instead of presentation forms."""

import io
import unicodedata
from pathlib import Path

import pytest

pypdf = pytest.importorskip("pypdf")

from gaia.rag.arabic_text import (  # noqa: E402
    extract_pdf_page_text,
    normalize_arabic_pdf_text,
)

# Bytes the content stream draws -> the text ToUnicode says they are.
_GLYPHS = {
    "A": "ب",  # ب
    "B": "ي",  # ي
    "C": "ع",  # ع
    "D": "ﻻ",  # ﻻ  lam-alef ligature (presentation form)
    "E": "٦",  # ٦  Arabic-Indic six
    "F": "٢",  # ٢  Arabic-Indic two
    "G": "١",  # ١  Arabic-Indic one
    "H": "٧",  # ٧  Arabic-Indic seven
    "I": "٨",  # ٨  Arabic-Indic eight
    "J": "٫",  # ٫  Arabic decimal separator
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


def _line(*pieces, y=100) -> bytes:
    """A content stream drawing each (x, text) piece, in the order given."""
    return b" ".join(
        b"BT /F1 12 Tf 1 0 0 1 %d %d Tm (%s) Tj ET" % (x, y, text) for x, text in pieces
    )


def _page_text(content: bytes) -> str:
    return extract_pdf_page_text(pypdf.PdfReader(io.BytesIO(_pdf(content))).pages[0])


def test_line_stored_in_screen_order_reads_right_to_left():
    # Right to left on the page: "AB", 150, "BC", 275, "CA", the way Chrome
    # stores them left to right. Before the fix "CA" led and 275 sat beside "AB".
    text = _page_text(
        _line((50, b"CA"), (100, b"275"), (150, b"BC"), (200, b"150"), (250, b"AB"))
    )

    assert text.split() == ["يب", "150", "عي", "275", "بع"]


def test_full_stop_stays_at_the_end_of_a_reordered_line():
    text = _page_text(_line((50, b"."), (60, b"4417"), (100, b"AB")))

    assert text.strip() == "يب 4417."


def test_percent_sign_stays_against_its_number():
    text = _page_text(_line((50, b"%"), (60, b"34"), (100, b"AB")))

    assert text.strip() == "يب 34%"


def test_a_split_number_stays_in_one_piece():
    text = _page_text(_line((50, b"20"), (62, b"24"), (100, b"AB")))

    assert text.split() == ["يب", "2024"]


def test_line_already_in_reading_order_is_not_touched():
    # Word stores the rightmost run first, so the file already reads correctly.
    content = _line((250, b"AB"), (200, b"150"), (150, b"BC"))

    assert _page_text(content) == _extract(content)


def test_quote_marks_alone_do_not_flip_a_line():
    content = _line((50, b"AB"), (100, b"."), (150, b"BC"))

    assert _page_text(content) == _extract(content)


def test_english_line_quoting_an_arabic_word_is_not_touched():
    content = _line((50, b"net total"), (120, b"AB"))

    assert _page_text(content) == _extract(content)


def test_arabic_digit_counts_as_a_number_when_reordering():
    text = _page_text(_line((50, b"BC"), (100, b"E"), (150, b"AB")))

    assert text.split() == ["يب", "٦", "عي"]


def test_lines_are_repaired_on_their_own():
    content = _line((50, b"BC"), (100, b"5"), (150, b"AB"), y=200) + _line(
        (250, b"AB"), (200, b"7"), (150, b"BC"), y=100
    )

    lines = [line.split() for line in _page_text(content).splitlines() if line.strip()]

    assert lines[0] == ["يب", "5", "عي"]
    assert "7" in "".join(lines[1])


def test_page_without_text_pieces_comes_back_as_is():
    class Page:
        def extract_text(self, visitor_operand_before=None, visitor_text=None):
            return "plain"

    assert extract_pdf_page_text(Page()) == "plain"


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


def test_a_repeated_line_is_repaired_where_it_stands():
    from gaia.rag.arabic_text import _repair_visual_order_lines

    # The first line is already in reading order; only the second was stored visually.
    first = "مدة العقد 12 شهرا"
    stored = "شهرا 12 مدة العقد"
    text = f"{stored}\nفقرة أخرى\n{stored}\n"
    pieces = [(50, 700, "شهرا"), (100, 700, "12"), (150, 700, "مدة العقد")]
    repaired = _repair_visual_order_lines(
        text,
        [(10, 800, stored)] + [(10, 750, "فقرة أخرى")] + pieces,
    )
    assert repaired == f"{stored}\nفقرة أخرى\n{first}\n"


# pypdf reverses Arabic-Indic digits as if they read right to left; they don't.
def test_arabic_indic_number_reads_in_order():
    # On the page: "يب ٢١٦ عي", the number drawn left to right.
    text = _page_text(_line((50, b"BC"), (100, b"FGE"), (150, b"AB")))

    assert text.split() == ["يب", "٢١٦", "عي"]


def test_arabic_indic_decimal_reads_in_order():
    text = _page_text(_line((50, b"BC"), (100, b"HJI"), (150, b"AB")))

    assert text.split() == ["يب", "٧٫٨", "عي"]


def test_arabic_indic_number_in_a_line_already_in_reading_order():
    text = _page_text(_line((250, b"AB"), (200, b"FGE"), (150, b"BC")))

    assert "٢١٦" in text and "٦١٢" not in text


def test_a_number_drawn_digit_by_digit_keeps_its_order():
    text = _page_text(
        _line((250, b"AB"), (200, b"F"), (206, b"G"), (212, b"E"), (150, b"BC"))
    )

    assert "٢١٦" in text.replace(" ", "")


# A hyphen after an Arabic word splits a date into runs Chrome lays out right to left.
def test_hyphenated_date_reads_year_first():
    text = _page_text(
        _line(
            (50, b"."),
            (60, b"15"),
            (80, b"-"),
            (84, b"02"),
            (100, b"-"),
            (104, b"2027"),
            (150, b"AB"),
        )
    )

    assert text.strip() == "يب 2027-02-15."


def test_number_range_after_a_latin_word_keeps_its_order():
    # After a Latin word the hyphen joins the numbers into one left-to-right run.
    text = _page_text(
        _line(
            (50, b"BCABCABC"),
            (100, b"dell"),
            (130, b"15"),
            (142, b"-"),
            (146, b"20"),
            (180, b"ABCABCAB"),
        )
    )

    assert "dell15-20" in text.split()


def test_hyphenated_code_with_letters_keeps_its_order():
    text = _page_text(
        _line(
            (50, b"BCABCABC"),
            (100, b"ab"),
            (112, b"-"),
            (116, b"12"),
            (160, b"ABCABCAB"),
        )
    )

    assert "ab-12" in text


def test_latin_words_keep_the_space_chrome_drops():
    text = _page_text(
        _line((50, b"BCABCABC"), (100, b"dell"), (130, b"xps"), (160, b"ABCABCAB"))
    )

    assert text.split() == ["يبعيبعيب", "dell", "xps", "عيبعيبعي"]


_CHROME_LINES = [
    "بلغ عدد المحطات في نهاية السجل ٢١٦ محطة.",
    "يبلغ ارتفاع السد ٤٧٫٨ مترًا والعرض ١٢٣٤ مترًا.",
    "رقم الهاتف ٠٥٠١٢٣٤٥٦٧ والرمز ٩٠٢٨١٥ فقط.",
    "يبدأ تنفيذ المشروع في 2027-02-15.",
    "يبدأ تنفيذ المشروع في ٢٠٢٧-٠٢-١٥ صباحًا.",
    "انتهى العمل بتاريخ 15/02/2027 كما هو مخطط.",
    "تبلغ ميزانية مشروع الواحة الزرقاء 3,915,200 دينار.",
    "مدير المشروع هو نزار القدسي.",
    "وافقت اللجنة على الخطة الجديدة دون أي تعديل يذكر.",
    "استخدم الفريق منصة Microsoft Azure لتشغيل الخدمة.",
    "قدم Ahmed Khan تقريره إلى مجلس الإدارة أمس.",
    "يعمل الخادم على المنفذ 8080 مع الإصدار v2.4.1 الجديد.",
    "تم شراء جهاز Dell XPS 15 بسعر 4,500 ريال.",
    "نسبة النمو ١٢٫٥٪ في عام ٢٠٢٣.",
    "نسبة النمو 12.5% في عام 2023.",
]


def test_lines_printed_by_chrome_read_as_written():
    """Each line of a page headless Chrome printed from ``_CHROME_LINES``."""
    pdf = Path(__file__).parents[1] / "fixtures" / "rag" / "arabic_lines_chrome.pdf"
    page = pypdf.PdfReader(str(pdf)).pages[0]
    text = normalize_arabic_pdf_text(extract_pdf_page_text(page))

    lines = [
        unicodedata.normalize("NFC", line.strip())
        for line in text.splitlines()
        if line.strip()
    ]
    expected = [unicodedata.normalize("NFC", line) for line in _CHROME_LINES]
    # "XPS 15" and "v2.4.1" reach pypdf alike, so whether a word and the number
    # after it had a space between them is lost; only that line is compared loosely.
    loose = expected.index("تم شراء جهاز Dell XPS 15 بسعر 4,500 ريال.")
    assert (
        lines[:loose] + lines[loose + 1 :] == expected[:loose] + expected[loose + 1 :]
    )
    assert lines[loose].replace(" ", "") == expected[loose].replace(" ", "")
