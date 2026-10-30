# Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Rebuild public/fonts/gaia-scripts.woff2 from src/data/scramble-scripts.ts.

Fetches a Noto subset per writing system from the Google Fonts API (only the
characters the scramble uses), drops the layout tables a lone glyph never
needs, and merges them into one woff2. Run after adding or changing a
character:  pip install fonttools brotli && python tools/subset-scripts-font.py
"""

import io
import pathlib
import re
import urllib.parse
import urllib.request

from fontTools import merge
from fontTools.ttLib import TTFont

SITE = pathlib.Path(__file__).resolve().parent.parent
DATA = SITE / "src" / "data" / "scramble-scripts.ts"
OUT = SITE / "public" / "fonts" / "gaia-scripts.woff2"
FAMILY = {
    "cuneiform": "Noto Sans Cuneiform",
    "egyptian": "Noto Sans Egyptian Hieroglyphs",
    "lineara": "Noto Sans Linear A",
    "linearb": "Noto Sans Linear B",
    "chinese": "Noto Serif SC",
    "phoenician": "Noto Sans Phoenician",
    "greek": "Noto Sans",
    "oldpersian": "Noto Sans Old Persian",
    "aramaic": "Noto Sans Imperial Aramaic",
    "tifinagh": "Noto Sans Tifinagh",
    "brahmi": "Noto Sans Brahmi",
    "runes": "Noto Sans Runic",
    "geez": "Noto Sans Ethiopic",
    "ogham": "Noto Sans Ogham",
    "arabic": "Noto Naskh Arabic",
    "oldturkic": "Noto Sans Old Turkic",
    "glagolitic": "Noto Sans Glagolitic",
}
# Characters outside their system's font: λ is Greek, ∑ and ∇ are maths. ASCII renders in Antonio.
EXTRA = {"Noto Sans": "λ", "Noto Sans Math": "∑∇"}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
DROP = (
    "GSUB",
    "GPOS",
    "GDEF",
    "BASE",
    "JSTF",
    "MATH",
    "vhea",
    "vmtx",
    "VORG",
    "fpgm",
    "prep",
    "cvt ",
    "gasp",
    "hdmx",
    "LTSH",
    "kern",
)


def chars_by_system() -> dict[str, str]:
    src = DATA.read_text(encoding="utf-8")
    out = {}
    for block in re.split(r"\n  \{\n    id: ", src)[1:]:
        sid = re.match(r"'(\w+)'", block).group(1)
        out[sid] = "".join(re.findall(r"char: '(.)'", block))
    return out


def fetch(family: str, text: str) -> TTFont:
    url = "https://fonts.googleapis.com/css2?" + urllib.parse.urlencode(
        {"family": family, "text": text}
    )
    css = (
        urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": UA}), timeout=30
        )
        .read()
        .decode()
    )
    (src,) = re.findall(r"url\((https://[^)]+)\)", css)
    font = TTFont(
        io.BytesIO(
            urllib.request.urlopen(
                urllib.request.Request(src, headers={"User-Agent": UA}), timeout=30
            ).read()
        )
    )
    font.flavor = None
    for tag in DROP:
        if tag in font:
            del font[tag]
    return font


def main() -> None:
    wanted: dict[str, str] = {}
    for sid, text in chars_by_system().items():
        if sid in ("ice", "code"):
            continue
        if sid not in FAMILY:
            raise SystemExit(f"{sid}: no Noto family mapped in FAMILY; add one.")
        wanted[FAMILY[sid]] = wanted.get(FAMILY[sid], "") + text
    for family, text in EXTRA.items():
        wanted[family] = wanted.get(family, "") + text
    tmp = SITE / "tools" / ".font-parts"
    tmp.mkdir(exist_ok=True)
    parts = []
    for i, (family, text) in enumerate(wanted.items()):
        path = tmp / f"{i:02d}.ttf"
        fetch(family, text).save(path)
        parts.append(str(path))
    font = merge.Merger(options=merge.Options(drop_tables=["vhea", "vmtx"])).merge(
        parts
    )
    name = font["name"]
    for nid in (1, 2, 4, 6, 16, 17):
        name.removeNames(nameID=nid)
    for nid, value in (
        (1, "GAIA Scripts"),
        (2, "Regular"),
        (4, "GAIA Scripts"),
        (6, "GAIAScripts-Regular"),
    ):
        name.setName(value, nid, 3, 1, 0x409)
    font.flavor = "woff2"
    font.save(OUT)
    for p in parts:
        pathlib.Path(p).unlink()
    tmp.rmdir()
    cmap = font.getBestCmap()
    missing = [c for t in wanted.values() for c in t if ord(c) not in cmap]
    if missing:
        raise SystemExit(f"the merged font is missing {missing}")
    print(
        f"{OUT.relative_to(SITE)}: {OUT.stat().st_size} bytes, {len(cmap)} characters"
    )


if __name__ == "__main__":
    main()
