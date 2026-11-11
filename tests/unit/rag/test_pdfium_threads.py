# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""PDFium is not thread-safe, even across documents; GAIA must serialize it.

The Agent UI indexes several documents at once, and pypdfium2 releases the GIL
in every call, so unserialized use corrupts the heap. The stress runs in a
subprocess so a segfault fails this test instead of killing pytest.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytest.importorskip("pypdfium2")
pytest.importorskip("reportlab")

SRC = Path(__file__).resolve().parents[3] / "src"

_STRESS = textwrap.dedent("""
    import random, sys, threading
    from pathlib import Path

    from PIL import Image
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    from gaia.rag.pdf_utils import (
        extract_images_from_page_pdfium,
        get_image_positions_on_page,
    )
    from gaia.utils import pdf_page_to_image

    out = Path(sys.argv[1])
    PAGES = 15
    pdfs = []
    for d in range(4):
        path = out / f"doc{d}.pdf"
        c = canvas.Canvas(str(path), pagesize=(400, 500))
        for p in range(PAGES):
            for i in range(3):
                noise = Image.effect_noise((160, 120), 40 + d * 10 + p + i).convert("RGB")
                c.drawImage(ImageReader(noise), 20 + i * 20, 20 + i * 140, 160, 120)
            c.drawString(10, 480, f"doc {d} page {p}")
            c.showPage()
        c.save()
        pdfs.append(str(path))

    errors = []

    def work(seed):
        rng = random.Random(seed)
        try:
            for _ in range(40):
                pdf = rng.choice(pdfs)
                page = rng.randrange(PAGES)
                assert extract_images_from_page_pdfium(pdf, page + 1)
                assert get_image_positions_on_page(pdf, page)
                assert pdf_page_to_image(pdf, page, scale=1.0)
        except Exception as e:  # reported to the parent, which fails the test
            errors.append(repr(e))

    threads = [threading.Thread(target=work, args=(s,)) for s in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors:
        sys.exit("worker errors: " + "; ".join(errors[:3]))
    print("ok")
    """)


def test_concurrent_pdfium_use_from_many_threads_does_not_crash(tmp_path):
    result = subprocess.run(
        [sys.executable, "-c", _STRESS, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=600,
        env={**os.environ, "PYTHONPATH": str(SRC)},
    )
    assert result.returncode == 0 and result.stdout.strip() == "ok", (
        f"exit {result.returncode}\nstdout: {result.stdout[-2000:]}\n"
        f"stderr: {result.stderr[-2000:]}"
    )
