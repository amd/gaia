# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Render individual slides to PNG, named so their origin is never in doubt.

Assembling an executive deck means pulling the best slide from each experiment,
and the failure mode of that is a picture nobody can trace: six months later a
number is challenged and no one knows which run produced it. So every file is
named ``<source-folder>__slide-NN__<title-slug>.png`` — the folder it came from,
its position in that deck, and what it said.

Each slide is rendered alone, in the original stylesheet, at deck aspect ratio.
Rendering the whole page and cropping would be faster and would silently drift
whenever a deck's slide height changed.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Tuple

#: The deck's own slide size. Rendering larger silently hid overflow that a
#: browser clips at 720px, so a slide could look complete here and be cut
#: off in the room.
WIDTH, HEIGHT = 1280, 720

CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "google-chrome",
    "chromium",
)


def browser() -> str:
    for candidate in CHROME_CANDIDATES:
        found = shutil.which(candidate) or (
            candidate if Path(candidate).exists() else None
        )
        if found:
            return found
    raise RuntimeError(
        "No Chrome or Edge found to render slides. Install one, or export the "
        "deck to PDF instead and screenshot manually."
    )


def slugify(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&amp;", "&").replace("&nbsp;", " ")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:60] or "untitled"


def split_deck(html: str) -> Tuple[str, List[Tuple[str, str]]]:
    """Return (head-with-styles, [(title, section-html)])."""
    head = html.split('<div class="deck">')[0]
    sections = re.findall(r'(<section class="slide".*?</section>)', html, re.S)
    out = []
    for section in sections:
        m = re.search(r'<h1 class="title">(.*?)</h1>', section, re.S)
        out.append((m.group(1) if m else "untitled", section))
    return head, out


def render(
    deck: Path, out_dir: Path, wanted: List[int] = None, label: str = ""
) -> List[Path]:
    html = deck.read_text(encoding="utf-8")
    head, slides = split_deck(html)
    if not slides:
        raise SystemExit(f"no slides found in {deck}")

    source = label or deck.parent.name
    out_dir.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    exe = browser()

    for index, (title, section) in enumerate(slides, 1):
        if wanted and index not in wanted:
            continue
        # Each slide is rendered on its own page so the capture is exactly the
        # slide, whatever the deck's internal spacing happens to be.
        page = (
            head
            + "<style>body{margin:0;background:#fff}"
            + f".slide{{width:{WIDTH}px;height:{HEIGHT}px;margin:0;overflow:hidden;"
            + "box-shadow:none;border:0;page-break-after:avoid}</style>"
            + '<div class="deck">'
            + section
            + "</div></body></html>"
        )
        tmp = Path(tempfile.mkdtemp()) / "slide.html"
        tmp.write_text(page, encoding="utf-8")
        dest = out_dir / f"{source}__slide-{index:02d}__{slugify(title)}.png"
        subprocess.run(  # noqa: S603 - fixed argv, no shell
            [
                exe,
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                f"--window-size={WIDTH},{HEIGHT}",
                f"--screenshot={dest}",
                str(tmp),
            ],
            capture_output=True,
            timeout=120,
        )
        shutil.rmtree(tmp.parent, ignore_errors=True)
        if dest.exists():
            written.append(dest)
            print(f"  {dest.name}")
        else:
            print(f"  FAILED slide {index}: {slugify(title)}")
    return written


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--deck", type=Path, required=True, help="a slides.html")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument(
        "--slides",
        default="",
        help="comma-separated 1-based slide numbers; default renders all",
    )
    ap.add_argument(
        "--label",
        default="",
        help="source name used in the filename; defaults to the deck's folder",
    )
    a = ap.parse_args()
    wanted = [int(x) for x in a.slides.split(",") if x.strip()] if a.slides else None
    written = render(a.deck, a.out, wanted, a.label)
    print(f"\n{len(written)} slide(s) -> {a.out}")


if __name__ == "__main__":
    main()
