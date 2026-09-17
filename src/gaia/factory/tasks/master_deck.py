# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Assemble one executive deck from the strongest slides across experiments.

Each experiment folder produces a deck aimed at whoever is reviewing that run.
An executive review needs something different: one narrative, drawn from
several runs, that opens on **what the work is** and only then shows how the
systems did on it. Leading with results argues that the benchmark matters by
showing the benchmark.

Slides are copied verbatim rather than regenerated. A re-derived figure could
disagree with the deck it came from, and the first question in the room would
be which number is right. The cost is that this deck goes stale when a source
run is re-judged — so every slide keeps a provenance note naming the folder and
position it came from, and ``PROVENANCE.md`` records the whole mapping.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import List, Tuple

from .extract_slides import slugify, split_deck


def assemble(manifest: List[dict], root: Path) -> Tuple[str, List[dict]]:
    """Build the combined HTML and the provenance record."""
    head = ""
    sections: List[str] = []
    provenance: List[dict] = []

    for entry in manifest:
        deck = (root / entry["deck"]).resolve()
        html = deck.read_text(encoding="utf-8")
        deck_head, slides = split_deck(html)
        # The first deck's <head> wins: the stylesheets are shared, and merging
        # two would let the later one silently restyle the earlier one's slides.
        head = head or deck_head
        index = entry["slide"]
        if not 1 <= index <= len(slides):
            raise SystemExit(
                f"{entry['deck']} has {len(slides)} slides; asked for {index}"
            )
        title, section = slides[index - 1]
        # Renumber to this deck's order, and relabel with the section name so
        # the running header reads as one narrative rather than a collage.
        section = re.sub(
            r'<span class="sn">\d+</span><span class="snlab">.*?</span>',
            f'<span class="sn">{len(sections) + 1}</span>'
            f'<span class="snlab">{entry.get("label", "")}</span>',
            section,
            count=1,
        )
        sections.append(section)
        provenance.append(
            {
                "position": len(sections),
                "title": re.sub("<[^>]+>", "", title).strip(),
                "source_deck": entry["deck"],
                "source_slide": index,
                "section": entry.get("label", ""),
                "png": f"exec__slide-{len(sections):02d}__{slugify(title)}.png",
            }
        )

    deck_html = (
        head + '<div class="deck">' + "\n".join(sections) + "</div></body></html>"
    )
    return deck_html, provenance


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--root", type=Path, required=True, help="experiments root")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()

    manifest = json.loads(a.manifest.read_text(encoding="utf-8"))
    deck_html, provenance = assemble(manifest, a.root)
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "slides.html").write_text(deck_html, encoding="utf-8")

    lines = [
        "# Where every slide came from",
        "",
        "Slides are copied verbatim from the experiment decks listed below, so "
        "the numbers here are identical to the ones in those runs. **If a "
        "source run is re-judged, this deck must be rebuilt** — it does not "
        "recompute anything.",
        "",
        "| # | section | slide | source run | slide there |",
        "|---:|---|---|---|---:|",
    ]
    for p in provenance:
        lines.append(
            f"| {p['position']} | {p['section']} | {p['title']} "
            f"| `{p['source_deck']}` | {p['source_slide']} |"
        )
    lines += [
        "",
        "## Rebuilding",
        "",
        "```bash",
        "python -m gaia.factory.tasks.master_deck \\",
        "    --manifest MANIFEST.json --root ~/Work/gaia-experiments --out .",
        "python -m gaia.factory.tasks.extract_slides \\",
        "    --deck slides.html --out slides_png --label exec",
        "```",
        "",
    ]
    (a.out / "PROVENANCE.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {a.out / 'slides.html'} ({len(provenance)} slides)")
    print(f"wrote {a.out / 'PROVENANCE.md'}")


if __name__ == "__main__":
    main()
