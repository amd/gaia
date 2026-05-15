#!/usr/bin/env python3
import argparse
import os
from textwrap import wrap

SAMPLE_PARAGRAPH = (
    "This is a synthetic safety handbook section about water safety. "
    "It contains repeated guidance to simulate a text-heavy PDF used for RAG indexing. "
    "Guidance covers labeling of water containers, maintaining sanitary water conditions, "
    "and emergency isolation procedures for contaminated water. Follow-up steps include notifying the safety officer and tagging affected areas. "
)

def make_pdf(path: str, target_bytes: int = 1_500_000) -> None:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter

    width, height = letter
    font_name = "Helvetica"
    font_size = 10
    left_margin = 72
    top = height - 72

    # Heuristic: approximate bytes per page and compute pages needed
    bytes_per_page = 6000
    num_pages = max(10, int(target_bytes // bytes_per_page))

    c = canvas.Canvas(path, pagesize=letter)
    section = 1
    for _ in range(num_pages):
        text = c.beginText(left_margin, top)
        text.setFont(font_name, font_size)
        block = "Section %d:\n\n" % section + (SAMPLE_PARAGRAPH * 20)
        for para in block.split("\n\n"):
            wrapped = wrap(para, 100)
            for ln in wrapped:
                text.textLine(ln)
                if text.getY() < 72:
                    c.drawText(text)
                    c.showPage()
                    text = c.beginText(left_margin, top)
                    text.setFont(font_name, font_size)
        c.drawText(text)
        c.showPage()
        section += 1

    c.save()

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="../documents/safety_handbook_large.pdf")
    p.add_argument("--size", type=int, default=1_500_000)
    args = p.parse_args()
    out = os.path.abspath(os.path.join(os.path.dirname(__file__), args.out))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    print(f"Generating PDF fixture at: {out}")
    make_pdf(out, target_bytes=args.size)
    print(f"Done. Final size: {os.path.getsize(out)} bytes")

if __name__ == "__main__":
    main()
