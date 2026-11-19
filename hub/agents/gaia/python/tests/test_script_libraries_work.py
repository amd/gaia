# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Every library a ``run_python`` script is promised does one real thing.

``freeze.SCRIPT_LIBRARIES`` is what the frozen binary bundles for scripts and
what the tool descriptions advertise. Each entry gets one basic operation on a
real file here, so a library that imports but cannot work (a missing native
library, a renamed API) fails a unit test rather than a user's script. A
library absent from the test environment is skipped, not passed.
"""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

import pytest

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"
_spec = importlib.util.spec_from_file_location(
    "gaia_freeze_libs", PACKAGING / "freeze.py"
)
assert _spec and _spec.loader
freeze = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(freeze)


def _openpyxl(m, tmp):
    book = m.Workbook()
    book.active.append(["part", 4])
    book.save(tmp / "a.xlsx")
    assert m.load_workbook(tmp / "a.xlsx").active["B1"].value == 4


def _xlsxwriter(m, tmp):
    book = m.Workbook(str(tmp / "b.xlsx"))
    book.add_worksheet().write(0, 0, "hello")
    book.close()
    assert (tmp / "b.xlsx").stat().st_size > 0


def _docx(m, tmp):
    document = m.Document()
    document.add_paragraph("hello")
    document.save(str(tmp / "c.docx"))
    assert m.Document(str(tmp / "c.docx")).paragraphs[0].text == "hello"


def _pptx(m, tmp):
    deck = m.Presentation()
    deck.slides.add_slide(deck.slide_layouts[0]).shapes.title.text = "hello"
    deck.save(str(tmp / "d.pptx"))
    assert len(m.Presentation(str(tmp / "d.pptx")).slides) == 1


def _reportlab(m, tmp):
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(tmp / "e.pdf"), pagesize=(100, 50))
    c.drawString(10, 20, "hello")
    c.save()
    assert (tmp / "e.pdf").read_bytes().startswith(b"%PDF")


def _pdf(tmp):
    pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas

    path = tmp / "f.pdf"
    c = canvas.Canvas(str(path), pagesize=(100, 50))
    c.drawString(10, 20, "hello")
    c.save()
    return path


def _pypdf(m, tmp):
    assert "hello" in m.PdfReader(str(_pdf(tmp))).pages[0].extract_text()


def _pypdfium2(m, tmp):
    page = m.PdfDocument(str(_pdf(tmp)))[0]
    assert page.render(scale=2).to_pil().size == (200, 100)
    assert "hello" in page.get_textpage().get_text_range()


def _numpy(m, tmp):
    assert int(m.arange(5).sum()) == 10


def _pandas(m, tmp):
    frame = m.DataFrame({"qty": [4, 6]})
    frame.to_csv(tmp / "g.csv", index=False)
    assert int(m.read_csv(tmp / "g.csv")["qty"].sum()) == 10


def _matplotlib(m, tmp):
    m.use("Agg")
    import matplotlib.pyplot as plt

    plt.plot([1, 2, 3])
    plt.savefig(tmp / "h.png")
    plt.close("all")
    assert (tmp / "h.png").read_bytes().startswith(b"\x89PNG")


def _pil(m, tmp):
    from PIL import Image

    Image.new("RGB", (8, 4), "red").save(tmp / "i.png")
    assert Image.open(tmp / "i.png").size == (8, 4)


OPERATIONS = {
    "openpyxl": _openpyxl,
    "xlsxwriter": _xlsxwriter,
    "docx": _docx,
    "pptx": _pptx,
    "reportlab": _reportlab,
    "pypdf": _pypdf,
    "pypdfium2": _pypdfium2,
    "numpy": _numpy,
    "pandas": _pandas,
    "matplotlib": _matplotlib,
    "PIL": _pil,
}


def test_every_script_library_has_an_operation_here():
    """A library added to the binary needs a working-operation test too."""
    assert set(freeze.SCRIPT_LIBRARIES) == set(OPERATIONS)


@pytest.mark.parametrize("name", freeze.SCRIPT_LIBRARIES)
def test_script_library_does_a_basic_operation(name, tmp_path):
    module = pytest.importorskip(name)
    OPERATIONS[name](module, tmp_path)
