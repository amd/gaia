# Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Rebuild public/space/hubble-atlas.webp, the starfield's galaxies and clusters.

Downloads each image from the NASA Image and Video Library, crops it square,
sets its sky level to black, feathers its edges so it blends into the field
with no visible frame, and packs it into a 192 px cell, 5 across. The cell
order is what src/scripts/starfield.ts indexes (GALAXY_CELLS, CLUSTER_CELLS,
EDGE_ON) and what public/space/CREDITS.md lists; keep all three in step.
Run:  pip install pillow numpy && python tools/build-hubble-atlas.py
"""

import io
import json
import pathlib
import urllib.request

import numpy as np
from PIL import Image

SITE = pathlib.Path(__file__).resolve().parent.parent
OUT = SITE / "public" / "space" / "hubble-atlas.webp"
CELL, COLS = 192, 5
A = "GSFC_20171208_Archive_"
GALAXIES = [
    "a-galaxy-at-the-center-of-the-hubble-tuning-fork_22206278923_o",
    A + "e000020",
    A + "e000151",
    A + "e000158",
    A + "e000504",
    A + "e000687",
    A + "e000710",
    A + "e000794",
    A + "e000848",
    A + "e000877",
    A + "e001151",
    A + "e001979",
    A + "e002154",
    "PIA04213",
    "PIA04224",
    "PIA15226",
]
CLUSTERS = [A + "e000191", A + "e001017", A + "e000846"]
# Where the edge fade starts and ends, as a share of the half-width; and the
# percentile taken as sky. Galaxies take a higher one so their faint halo
# does not show as a grey disc.
STYLE = {"galaxy": ((0.4, 0.95), 30, 1.3), "cluster": ((0.35, 0.98), 8, 1.0)}


def download(nasa_id: str) -> Image.Image:
    assets = json.load(
        urllib.request.urlopen(
            f"https://images-api.nasa.gov/asset/{nasa_id}", timeout=30
        )
    )
    urls = [x["href"] for x in assets["collection"]["items"]]
    for suffix in ("~medium.jpg", "~small.jpg", "~orig.jpg"):
        url = next((u for u in urls if u.endswith(suffix)), None)
        if url:
            data = urllib.request.urlopen(
                url.replace("http://", "https://"), timeout=60
            ).read()
            return Image.open(io.BytesIO(data)).convert("RGB")
    raise SystemExit(f"{nasa_id}: no JPEG in the NASA asset list")


def prepare(im: Image.Image, kind: str) -> Image.Image:
    (r0, r1), floor_pct, gamma = STYLE[kind]
    side = min(im.size)
    left, top = (im.width - side) // 2, (im.height - side) // 2
    im = im.crop((left, top, left + side, top + side)).resize((256, 256), Image.LANCZOS)
    a = np.asarray(im).astype(np.float32) / 255
    floor = np.percentile(a.reshape(-1, 3), floor_pct, axis=0)
    a = np.clip((a - floor) / (1 - floor), 0, 1) ** gamma
    n = a.shape[0]
    yy, xx = np.mgrid[0:n, 0:n]
    r = np.hypot(xx - n / 2 + 0.5, yy - n / 2 + 0.5) / (n / 2)
    m = np.clip((r1 - r) / (r1 - r0), 0, 1)
    a = a * (m * m * (3 - 2 * m))[..., None]
    return Image.fromarray((a * 255).astype(np.uint8)).resize(
        (CELL, CELL), Image.LANCZOS
    )


def main() -> None:
    cells = [("galaxy", i) for i in GALAXIES] + [("cluster", i) for i in CLUSTERS]
    rows = -(-len(cells) // COLS)
    atlas = Image.new("RGB", (COLS * CELL, rows * CELL))
    for k, (kind, nasa_id) in enumerate(cells):
        atlas.paste(
            prepare(download(nasa_id), kind), ((k % COLS) * CELL, (k // COLS) * CELL)
        )
    atlas.save(OUT, "WEBP", quality=74, method=6)
    print(f"{OUT.relative_to(SITE)}: {OUT.stat().st_size} bytes, {len(cells)} cells")


if __name__ == "__main__":
    main()
