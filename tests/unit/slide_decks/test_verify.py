# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""verify_deck catches what is wrong with a deck — including decks this skill did not build.

Each negative test hand-builds a flawed deck with python-pptx, as a person or
another tool would, and asserts the specific check fires; the good decks from
build_deck must stay clean.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Inches, Pt
from slidedeck.build import build_deck
from slidedeck.outline import normalize_outline
from slidedeck.verify import verify_deck

GOOD = [
    {
        "type": "title",
        "title": "Maple Street clinic network review",
        "subtitle": "Quarterly update",
    },
    {
        "type": "bullets",
        "title": "Visits grew 9% while wait times fell",
        "bullets": ["Visits up 9% on the quarter", "Median wait down to 18 minutes"],
        "notes": "Talk track.",
    },
    {
        "type": "chart",
        "title": "Visits rose every month this quarter",
        "categories": ["Jul", "Aug", "Sep"],
        "series": {"Visits": [4100, 4300, 4470]},
        "y_title": "Visits",
        "source": "visits.csv, Jul-Sep 2026",
    },
]


def checks(report):
    return {i["check"] for i in report["issues"]}


def deck(slide_fn, n=1, path=None, layout=6):
    """A bare python-pptx deck: n slides built by slide_fn(slide, prs, i)."""
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    for i in range(n):
        s = prs.slides.add_slide(prs.slide_layouts[layout])
        slide_fn(s, prs, i)
    prs.save(path)
    return path


def titled(slide, text="The clinic network grew nine percent this quarter"):
    t = slide.shapes.title
    t.left, t.top, t.width, t.height = (
        Inches(0.6),
        Inches(0.45),
        Inches(12.1),
        Inches(0.9),
    )
    t.text = text
    for r in t.text_frame.paragraphs[0].runs:
        r.font.size = Pt(32)
    return t


def box(slide, x, y, w, h, text, size=18, name=None):
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tb.text_frame.word_wrap = True
    tb.text_frame.text = text
    for p in tb.text_frame.paragraphs:
        for r in p.runs:
            r.font.size = Pt(size)
    if name:
        tb.name = name
    return tb


def test_good_deck_passes_with_a_high_score(out):
    r = build_deck({"slides": GOOD}, out())
    rep = verify_deck(
        r["path"],
        expected_slides=3,
        render=False,
        outline=normalize_outline({"slides": GOOD}),
    )
    assert rep["status"] == "pass", rep["issues"]
    assert rep["style_score"] >= 95
    assert rep["titles"] == [s["title"] for s in GOOD]
    assert (
        rep["slides_overview"][2].startswith("3. Visits rose")
        and "source: visits.csv" in rep["slides_overview"][2]
    )


def test_wrong_slide_count(out):
    r = build_deck({"slides": GOOD}, out())
    rep = verify_deck(r["path"], expected_slides=5, render=False)
    count = [i for i in rep["issues"] if i["check"] == "slide_count"]
    assert count[0]["severity"] == "warning" and "add_slide" in count[0]["fix"]
    assert rep["status"] == "pass"


def test_overflowing_text(out):
    def make(s, prs, i):
        titled(s)
        box(
            s,
            0.6,
            1.6,
            6,
            1.0,
            "A paragraph that is far too long for its little box. " * 12,
        )

    rep = verify_deck(deck(make, path=out(), layout=5), render=False)
    assert "text_overflow" in checks(rep)


def test_picture_instead_of_chart(out, tmp_path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    png = tmp_path / "plot.png"
    fig, ax = plt.subplots()
    ax.bar(["a", "b"], [1, 2])
    fig.savefig(png)
    plt.close(fig)

    def make(s, prs, i):
        titled(s, "Visits rose every month this quarter")
        s.shapes.add_picture(str(png), Inches(0.6), Inches(1.6), Inches(8), Inches(4.5))
        box(s, 0.6, 6.8, 6, 0.4, "Source: visits.csv", size=12, name="Source")

    rep = verify_deck(deck(make, path=out(), layout=5), render=False)
    assert "picture_not_chart" in checks(rep)
    rep = verify_deck(
        deck(make, path=out("b.pptx"), layout=5),
        render=False,
        require_native_charts=False,
    )
    assert "picture_not_chart" not in checks(rep)


def test_overlapping_shapes(out):
    def make(s, prs, i):
        titled(s)
        box(s, 0.6, 1.6, 6, 2, "First block of text")
        box(s, 3.0, 2.2, 6, 2, "Second block of text")

    rep = verify_deck(deck(make, path=out(), layout=5), render=False)
    assert "overlap" in checks(rep)


def test_empty_placeholder(out):
    def make(s, prs, i):
        s.shapes.title.text = "Wait times fell to 18 minutes on average"

    rep = verify_deck(deck(make, path=out(), layout=1), render=False)
    assert "empty_placeholder" in checks(rep)


def test_missing_title_tiny_font_and_margins(out):
    def make(s, prs, i):
        box(s, 0.05, 1.0, 5, 1, "tiny footnote-sized text in the body", size=8)

    rep = verify_deck(deck(make, path=out()), render=False)
    c = checks(rep)
    assert {"no_title", "tiny_font", "margin"} <= c


def test_low_contrast_text(out):
    def make(s, prs, i):
        titled(s)
        tb = box(s, 0.6, 1.6, 8, 1, "Pale grey words on a white slide")
        tb.text_frame.paragraphs[0].runs[0].font.color.rgb = RGBColor(0xCC, 0xCC, 0xCC)

    rep = verify_deck(deck(make, path=out(), layout=5), render=False)
    assert "contrast" in checks(rep)


def test_chart_without_source_unit_and_with_single_series_legend(out):
    def make(s, prs, i):
        titled(s)
        cd = CategoryChartData()
        cd.categories = ["a", "b", "c"]
        cd.add_series("Visits", (1, 2, 3))
        gf = s.shapes.add_chart(
            XL_CHART_TYPE.COLUMN_CLUSTERED,
            Inches(0.6),
            Inches(1.6),
            Inches(10),
            Inches(4.5),
            cd,
        )
        gf.chart.has_legend = True
        gf.chart.value_axis.minimum_scale = 0.5

    rep = verify_deck(deck(make, path=out(), layout=5), render=False)
    assert {
        "missing_source",
        "chart_legend",
        "bar_baseline",
        "axis_unit",
        "alt_text",
    } <= checks(rep)


def test_chart_cache_that_disagrees_with_its_data_sheet(out):
    good = build_deck({"slides": [GOOD[2]]}, out())["path"]
    # Edit the drawn values in the chart XML but not the embedded workbook.
    src = zipfile.ZipFile(good)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as dst:
        for item in src.infolist():
            blob = src.read(item.filename)
            if item.filename.startswith("ppt/charts/chart") and item.filename.endswith(
                ".xml"
            ):
                blob = blob.replace(b"<c:v>4470.0</c:v>", b"<c:v>9999</c:v>")
            dst.writestr(item, blob)
    bad = Path(out("bad.pptx"))
    bad.write_bytes(buf.getvalue())
    rep = verify_deck(
        str(bad), render=False, outline=normalize_outline({"slides": [GOOD[2]]})
    )
    assert {"chart_data_mismatch", "chart_values"} <= checks(rep)


def test_missing_notes_when_required(out):
    r = build_deck({"slides": GOOD}, out())
    rep = verify_deck(r["path"], render=False, require_notes=True)
    missing = [i for i in rep["issues"] if i["check"] == "missing_notes"]
    assert [i["slide"] for i in missing] == [
        3
    ], "the cover is exempt; slide 2 has notes"


def test_too_many_words_bullets_long_titles_and_topic_titles(out):
    wordy = {
        "type": "bullets",
        "title": "This title keeps going well past the point where a reader can take it in at a glance today",
        "bullets": [
            f"Bullet {i} carries far more words than any single bullet on a slide ever should"
            for i in range(8)
        ],
    }
    topic = {"type": "bullets", "title": "Revenue", "bullets": ["Up 4%"]}
    r = build_deck({"slides": [wordy, topic]}, out())
    c = set(r["verify"]["summary"])
    assert {
        "too_many_words",
        "too_many_bullets",
        "bullet_length",
        "title_length",
        "topic_title",
    } <= c
    # The same deck under a user's 'detailed' density and topic-title style.
    rep = verify_deck(
        r["path"], render=False, prefs={"density": "detailed", "title_style": "topic"}
    )
    assert "topic_title" not in checks(rep)


def test_unfilled_todo_slot(out):
    r = build_deck(
        {
            "slides": [
                {
                    "type": "bullets",
                    "title": "TODO: the headline result",
                    "bullets": ["TODO: three takeaways"],
                }
            ]
        },
        out(),
    )
    assert (
        "unfilled_slot" in r["verify"]["summary"] and r["status"] == "built_with_issues"
    )


def test_off_theme_colours_and_font_families(out):
    def make(s, prs, i):
        titled(s)
        for k, (font, rgb) in enumerate(
            [
                ("Comic Sans MS", "FF00FF"),
                ("Courier New", "00FF00"),
                ("Impact", "FF8800"),
            ]
        ):
            tb = box(s, 0.6 + 4 * k, 2, 3.5, 1, f"Text in {font}")
            run = tb.text_frame.paragraphs[0].runs[0]
            run.font.name = font
            run.font.color.rgb = RGBColor.from_string(rgb)

    rep = verify_deck(deck(make, path=out(), layout=5), render=False)
    assert {"font_families", "off_theme_color"} <= checks(rep)


def test_duplicate_titles_and_position_jumps(out):
    def make(s, prs, i):
        t = titled(s, "Every clinic now opens on Saturday mornings")
        if i == 3:
            t.left = Inches(2.5)
        box(s, 0.6, 1.6, 8, 1, "Short point")

    rep = verify_deck(deck(make, n=4, path=out(), layout=5), render=False)
    assert {"duplicate_title", "title_position"} <= checks(rep)


def test_style_score_orders_good_above_bad(out):
    good = build_deck({"slides": GOOD}, out())["verify"]["style_score"]

    def make(s, prs, i):
        box(s, 0.05, 0.2, 13.2, 7, "everything everywhere " * 80, size=9)

    bad = verify_deck(deck(make, n=3, path=out("bad.pptx")), render=False)[
        "style_score"
    ]
    assert good > bad + 30


def test_a_foreign_deck_with_inherited_sizes_is_measured(out):
    """Text with no explicit size inherits from the layout/master; no false 'tiny font'."""

    def make(s, prs, i):
        # Short enough to fit the inherited 44pt on one line even with estimated widths.
        s.shapes.title.text = "Clinic hours expanded"
        s.placeholders[1].text_frame.text = "Saturday hours added"

    rep = verify_deck(deck(make, path=out(), layout=1), render=False)
    assert not {"tiny_font", "small_font", "text_overflow"} & checks(rep)


# ---------------------------------------------------------------- render


def test_render_confirms_a_good_deck_and_writes_pngs(out, renderer, tmp_path):
    r = build_deck({"slides": GOOD}, out())
    png_dir = tmp_path / "png"
    png_dir.mkdir()
    rep = verify_deck(
        r["path"], expected_slides=3, renderer=renderer, render_dir=str(png_dir)
    )
    assert rep["render"]["rendered"] and len(rep["render"]["pngs"]) == 3
    assert rep["status"] == "pass", rep["issues"]


def test_render_confirms_overflow_on_the_page(out, renderer):
    def make(s, prs, i):
        from pptx.enum.text import MSO_AUTO_SIZE

        titled(s)
        tb = box(
            s,
            0.6,
            1.6,
            5,
            0.8,
            "A paragraph that is far too long for its little box. " * 10,
        )
        tb.text_frame.auto_size = MSO_AUTO_SIZE.NONE

    rep = verify_deck(deck(make, path=out(), layout=5), renderer=renderer)
    assert {"text_overflow", "render_overflow"} <= checks(rep), rep["issues"]


def test_a_title_repeated_lower_on_the_slide_is_not_overflow(out, renderer):
    """The card below says the title's words again; that is not the title spilling."""

    def make(s, prs, i):
        titled(s, "Cut setup to under 5 minutes")
        box(s, 0.6, 3.5, 6, 0.8, "Cut setup to under 5 minutes")

    rep = verify_deck(deck(make, path=out(), layout=5), renderer=renderer)
    assert "render_overflow" not in checks(rep), rep["issues"]


def test_without_a_renderer_the_report_says_so(out):
    r = build_deck({"slides": GOOD}, out())
    rep = verify_deck(r["path"], render=True, renderer=None)
    assert rep["render"]["rendered"] is False and "estimates" in rep["render"]["reason"]


# ------------------------------------------- render without LibreOffice


def _pdf_of(lines_per_page, path):
    """A PDF with one 13.33 x 7.5 inch page per slide: (x, y from the top, text) lines."""
    from reportlab.pdfgen import canvas

    page = canvas.Canvas(str(path), pagesize=(960, 540))
    for lines in lines_per_page:
        page.setFont("Helvetica", 18)
        for x, y, text in lines:
            page.drawString(x, 540 - y, text)
        page.showPage()
    page.save()
    return path


def _one_box(text):
    def make(s, prs, i):
        titled(s)
        box(s, 0.6, 1.6, 6, 0.8, text)

    return make


def test_a_renderer_that_times_out_or_fails_keeps_the_structural_report(out):
    import subprocess

    r = build_deck({"slides": GOOD}, out())

    def hangs(deck_path):
        raise subprocess.TimeoutExpired("soffice", 240)

    def breaks(deck_path):
        raise RuntimeError("LibreOffice exited with code 1 without a PDF.")

    for renderer in (hangs, breaks):
        rep = verify_deck(r["path"], expected_slides=3, renderer=renderer)
        assert rep["render"]["rendered"] is False
        assert "rendering failed" in rep["render"]["reason"]
        assert rep["status"] == "pass" and rep["slides"] == 3, rep["issues"]


def test_a_pdf_that_cannot_be_read_costs_only_the_render_check(out, tmp_path):
    r = build_deck({"slides": GOOD}, out())
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf")
    rep = verify_deck(r["path"], renderer=lambda deck_path: broken)
    assert rep["render"]["rendered"] is False and rep["slides"] == 3


def test_the_render_check_reads_a_pdf_and_writes_pngs(out, tmp_path):
    """The pypdfium2 path, on a PDF made here: no LibreOffice needed."""
    path = deck(_one_box("Saturday hours added"), path=out(), layout=5)
    pdf = _pdf_of([[(50, 140, "Saturday hours added")]], tmp_path / "ok.pdf")
    pngs = tmp_path / "png"
    pngs.mkdir()
    rep = verify_deck(path, renderer=lambda deck_path: pdf, render_dir=str(pngs))
    assert rep["render"]["rendered"] is True
    assert [Path(p).name for p in rep["render"]["pngs"]] == ["slide_01.png"]
    assert (pngs / "slide_01.png").read_bytes()[:4] == b"\x89PNG"
    assert "render_overflow" not in checks(rep), rep["issues"]


def test_the_render_check_flags_text_drawn_below_its_box(out, tmp_path):
    path = deck(_one_box("Saturday hours added today"), path=out(), layout=5)
    # The box ends 2.4 inches (173pt) down; the last word is drawn at 300pt.
    pdf = _pdf_of(
        [[(50, 140, "Saturday hours added"), (50, 300, "today")]], tmp_path / "o.pdf"
    )
    rep = verify_deck(path, renderer=lambda deck_path: pdf)
    assert "render_overflow" in checks(rep), rep["issues"]


def test_a_page_count_that_differs_from_the_deck_is_reported(out, tmp_path):
    path = deck(_one_box("Saturday hours added"), path=out(), layout=5)
    pdf = _pdf_of([[(50, 140, "Saturday hours added")], []], tmp_path / "two.pdf")
    rep = verify_deck(path, renderer=lambda deck_path: pdf)
    assert "render" in checks(rep)


def test_pdf_words_join_across_a_hyphen_at_the_line_end(tmp_path):
    from slidedeck import image

    pdf = _pdf_of(
        [[(60, 140, "Costs fell for the well-"), (60, 170, "known northern depots")]],
        tmp_path / "h.pdf",
    )
    doc = image.open_pdf(pdf)
    try:
        words = image.page_words(doc[0])
    finally:
        doc.close()
    assert [w[4] for w in words] == [
        "Costs",
        "fell",
        "for",
        "the",
        "wellknown",
        "northern",
        "depots",
    ]
    left, top, right, bottom, _text = words[0]
    assert 59 < left < right < 130 and 110 < top < bottom < 150


def test_slide_images_are_saved_and_a_blank_page_is_refused(tmp_path):
    import pytest
    from PIL import Image
    from slidedeck import image
    from slidedeck.outline import OutlineError

    pdf = _pdf_of([[(60, 140, "Visits rose every month")], []], tmp_path / "s.pdf")
    assert image.page_count(pdf) == 2
    saved = image.save_pages(pdf, [1], [tmp_path / "one.png"], "png", 640)
    assert saved == [
        {"slide": 1, "path": str(tmp_path / "one.png"), "width": 640, "height": 360}
    ]
    image.save_pages(pdf, [1], [tmp_path / "one.jpg"], "jpeg", 640)
    assert (tmp_path / "one.jpg").read_bytes()[:3] == b"\xff\xd8\xff"
    with pytest.raises(OutlineError, match="blank"):
        image.save_pages(pdf, [2], [tmp_path / "two.png"], "png", 640)
    assert not (tmp_path / "two.png").exists()
    assert image.is_blank(Image.new("RGB", (900, 500), "white"))
    # A small mark between the bytes a strided sample reads is still a drawing.
    dotted = Image.new("RGB", (900, 500), "white")
    dotted.paste("black", (700, 333, 720, 353))
    assert not image.is_blank(dotted)


def test_text_in_a_shape_with_no_size_is_an_error(out):
    def make(s, prs, i):
        titled(s)
        box(s, 0.6, 1.6, 0, 0.8, "Saturday hours added", name="Collapsed")

    rep = verify_deck(deck(make, path=out(), layout=5), render=False)
    assert "zero_size" in checks(rep), rep["issues"]
