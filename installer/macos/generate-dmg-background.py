#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Generate the DMG background image for the GAIA Agent UI macOS installer.

Run locally (no CI hook) whenever the DMG layout changes:

    .venv/bin/python installer/macos/generate-dmg-background.py

Design direction: matches the GAIA Agent UI dark theme tokens from
``src/gaia/apps/webui/src/styles/index.css``, using *less* decoration
rather than more — no explicit arrows, no dashed circles, no heavy
graphics. Premium installers (Slack, Discord, VS Code, Linear) rely on
layout and soft lighting to guide the eye, not clip-art arrows.

The icon-center coordinates in this script MUST match the ``dmg.contents``
block in ``src/gaia/apps/webui/electron-builder.yml``.
"""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ---- Layout -------------------------------------------------------------

WIDTH = 660
HEIGHT = 400

# Icon CENTER coordinates — must match electron-builder.yml exactly.
ICON_APP_CENTER = (160, 200)
ICON_APPS_CENTER = (500, 200)

# ---- GAIA Agent UI dark palette ----------------------------------------

BG_TOP = (8, 8, 13)           # --bg-sidebar #08080d
BG_BOTTOM = (14, 14, 22)      # --bg-primary #0e0e16

# Soft glow under each drop target — acts as ambient stage lighting,
# not a hard halo ring. The eye reads "two landing zones" without
# needing an arrow or outline.
GLOW_COLOR = (100, 110, 160)  # cool blue-purple
GLOW_PEAK_ALPHA = 35          # subtle, not a spotlight
GLOW_INNER_R = 35             # glow starts just outside the icon area
GLOW_OUTER_R = 110            # glow fades to zero at this radius

# Caption and wordmark tones.
CAPTION_COLOR = (200, 205, 225, 175)
WORDMARK_COLOR = (95, 98, 120, 130)


# ---- Background ---------------------------------------------------------


def _gradient_background() -> Image.Image:
    """Vertical linear gradient. Keeps the look restrained — no radial
    vignettes, no rainbow. Reads like the Agent UI sidebar→content
    transition.
    """
    img = Image.new("RGB", (WIDTH, HEIGHT), BG_TOP)
    pixels = img.load()
    for y in range(HEIGHT):
        t = y / (HEIGHT - 1)
        r = round(BG_TOP[0] * (1 - t) + BG_BOTTOM[0] * t)
        g = round(BG_TOP[1] * (1 - t) + BG_BOTTOM[1] * t)
        b = round(BG_TOP[2] * (1 - t) + BG_BOTTOM[2] * t)
        for x in range(WIDTH):
            pixels[x, y] = (r, g, b)
    return img


def _grain_overlay(size: tuple[int, int]) -> Image.Image:
    """Low-opacity monochrome noise to match the Agent UI's ``#root::after``
    SVG turbulence overlay. Deterministic via a fixed seed.
    """
    rng = random.Random(20260410)
    grain = Image.new("L", size, 0)
    px = grain.load()
    w, h = size
    for y in range(h):
        for x in range(w):
            px[x, y] = max(0, min(255, int(rng.gauss(0, 7))))
    grain = grain.filter(ImageFilter.GaussianBlur(radius=0.4))
    alpha = grain.point(lambda a: int(a * 0.3))
    white = Image.new("RGBA", size, (255, 255, 255, 255))
    white.putalpha(alpha)
    return white


# ---- Soft glow -----------------------------------------------------------


def _soft_glow(
    size: tuple[int, int],
    center: tuple[int, int],
) -> Image.Image:
    """Radial soft glow centered on a drop target. The glow is wide and
    subtle — it lifts the icon out of the background like overhead stage
    lighting, without drawing a visible circle or ring.
    """
    glow = Image.new("RGBA", size, (0, 0, 0, 0))
    cx, cy = center
    px = glow.load()
    w, h = size
    span = GLOW_OUTER_R - GLOW_INNER_R
    if span <= 0:
        return glow

    for y in range(max(0, cy - GLOW_OUTER_R), min(h, cy + GLOW_OUTER_R + 1)):
        dy2 = (y - cy) ** 2
        for x in range(max(0, cx - GLOW_OUTER_R), min(w, cx + GLOW_OUTER_R + 1)):
            d = (dy2 + (x - cx) ** 2) ** 0.5
            if d < GLOW_INNER_R:
                alpha = GLOW_PEAK_ALPHA
            elif d > GLOW_OUTER_R:
                continue
            else:
                t = (d - GLOW_INNER_R) / span
                alpha = int(GLOW_PEAK_ALPHA * (1 - t) ** 2)
            if alpha > 0:
                px[x, y] = (*GLOW_COLOR, alpha)
    return glow.filter(ImageFilter.GaussianBlur(radius=12))


# ---- Fonts ---------------------------------------------------------------


def _load_font(size: int, *, mono: bool = False) -> ImageFont.ImageFont:
    sans = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNS.ttf",
    ]
    monospace = [
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Monaco.ttf",
    ]
    for path in (monospace if mono else sans):
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


# ---- Main ---------------------------------------------------------------


def main() -> None:
    base = _gradient_background().convert("RGBA")

    # Grain.
    base = Image.alpha_composite(base, _grain_overlay(base.size))

    # Soft glows behind each drop target.
    for center in (ICON_APP_CENTER, ICON_APPS_CENTER):
        base = Image.alpha_composite(base, _soft_glow(base.size, center))

    # Typography.
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Caption — centered below the icon row.
    sans = _load_font(14)
    caption = "Drag GAIA to Applications"
    bbox = draw.textbbox((0, 0), caption, font=sans)
    text_w = bbox[2] - bbox[0]
    caption_y = ICON_APP_CENTER[1] + 80
    draw.text(
        ((WIDTH - text_w) // 2, caption_y),
        caption,
        font=sans,
        fill=CAPTION_COLOR,
    )

    # Wordmark — bottom right, nearly invisible.
    mono = _load_font(10, mono=True)
    wordmark = "GAIA"
    wb = draw.textbbox((0, 0), wordmark, font=mono)
    ww = wb[2] - wb[0]
    draw.text(
        (WIDTH - ww - 20, HEIGHT - 26),
        wordmark,
        font=mono,
        fill=WORDMARK_COLOR,
    )

    composed = Image.alpha_composite(base, overlay).convert("RGB")

    out = Path(__file__).parent / "dmg-background.png"
    composed.save(out, "PNG", optimize=True)
    print(f"wrote {out} ({out.stat().st_size:,} bytes, {WIDTH}x{HEIGHT})")


if __name__ == "__main__":
    main()
