#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Generate the NSIS welcome/finish sidebar BMP for the GAIA Agent UI
Windows installer.

Run locally (no CI hook) whenever the sidebar design changes:

    .venv/bin/python installer/nsis/generate-installer-sidebar.py

Output: ``installer/nsis/installer-sidebar.bmp`` (164×314, 24-bit BMP —
NSIS MUI2 ``MUI_WELCOMEFINISHPAGE_BITMAP`` requires 24-bit or 8-bit
indexed, NOT 32-bit with alpha).

Design matches the GAIA Agent UI dark theme tokens from
``src/gaia/apps/webui/src/styles/index.css`` and the macOS DMG
background (``installer/macos/generate-dmg-background.py``).
"""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ---- Dimensions ---------------------------------------------------------

# NSIS MUI2 welcome/finish sidebar. Non-negotiable.
WIDTH = 164
HEIGHT = 314

# ---- GAIA Agent UI dark palette (same as macOS DMG) --------------------

BG_TOP = (8, 8, 13)           # --bg-sidebar #08080d
BG_BOTTOM = (14, 14, 22)      # --bg-primary #0e0e16

GLOW_COLOR = (70, 80, 130)                # cool purple-blue glow
TITLE_COLOR = (232, 232, 240)             # --text-primary
SUBTITLE_COLOR = (152, 152, 176)          # --text-secondary
TAGLINE_COLOR = (133, 133, 160)           # --text-muted
ACCENT_COLOR = (237, 28, 36)              # --amd-red
WORDMARK_COLOR = (120, 120, 145)          # muted brand mark


# ---- Paths --------------------------------------------------------------

# Robot app icon — extracted from the generated .icns file. We re-extract
# on demand so this generator works from a clean checkout.
SCRIPT_DIR = Path(__file__).parent
ICNS_SOURCE = (SCRIPT_DIR / ".." / "macos" / "icon.icns").resolve()
OUTPUT = SCRIPT_DIR / "installer-sidebar.bmp"


def _extract_app_icon() -> Image.Image:
    """Return the 256×256 variant of the app icon with alpha intact.

    Uses ``iconutil`` to unpack the ``.icns`` into a temp iconset. The
    256 size is the sweet spot: large enough to downscale cleanly to
    128×128 on the sidebar without blurriness, small enough to keep
    this script fast.
    """
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        iconset = Path(td) / "icon.iconset"
        subprocess.run(
            ["iconutil", "-c", "iconset", str(ICNS_SOURCE), "-o", str(iconset)],
            check=True,
            capture_output=True,
        )
        candidates = [
            iconset / "icon_256x256.png",
            iconset / "icon_128x128@2x.png",
            iconset / "icon_512x512.png",
        ]
        for candidate in candidates:
            if candidate.exists():
                return Image.open(candidate).convert("RGBA")
    raise FileNotFoundError(f"no suitable PNG found in {ICNS_SOURCE}")


# ---- Background ---------------------------------------------------------


def _gradient_background() -> Image.Image:
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
    rng = random.Random(20260410)
    grain = Image.new("L", size, 0)
    px = grain.load()
    w, h = size
    for y in range(h):
        for x in range(w):
            v = int(rng.gauss(0, 7))
            px[x, y] = max(0, min(255, v))
    grain = grain.filter(ImageFilter.GaussianBlur(radius=0.4))
    alpha = grain.point(lambda a: int(a * 0.35))
    white = Image.new("RGBA", size, (255, 255, 255, 255))
    white.putalpha(alpha)
    return white


# ---- Glow ---------------------------------------------------------------


def _radial_glow(
    size: tuple[int, int],
    center: tuple[int, int],
    inner_radius: int,
    outer_radius: int,
    color: tuple[int, int, int],
) -> Image.Image:
    """Soft radial glow centered on an icon.

    Creates the subtle bloom that tells the eye 'this element is the
    focal point'. The glow fades to transparent between ``inner_radius``
    and ``outer_radius``; anything inside ``inner_radius`` is fully
    transparent so the icon itself isn't tinted.
    """
    glow = Image.new("RGBA", size, (0, 0, 0, 0))
    cx, cy = center
    px = glow.load()
    w, h = size
    span = outer_radius - inner_radius
    for y in range(h):
        dy2 = (y - cy) ** 2
        for x in range(w):
            d = (dy2 + (x - cx) ** 2) ** 0.5
            if d <= inner_radius:
                continue
            if d >= outer_radius:
                continue
            t = (d - inner_radius) / span
            alpha = int((1 - t) ** 2 * 45)
            if alpha > 0:
                px[x, y] = (*color, alpha)
    return glow.filter(ImageFilter.GaussianBlur(radius=6))


# ---- Fonts --------------------------------------------------------------


def _load_font(size: int, *, bold: bool = False, mono: bool = False):
    mac_bold = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Helvetica.ttc",
    ]
    mac_regular = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNS.ttf",
    ]
    mac_mono = [
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Monaco.ttf",
    ]
    paths = mac_mono if mono else (mac_bold if bold else mac_regular)
    for path in paths:
        if Path(path).exists():
            try:
                font = ImageFont.truetype(path, size=size)
                if bold:
                    # Helvetica.ttc contains multiple faces; index 1 is bold.
                    try:
                        return ImageFont.truetype(path, size=size, index=1)
                    except Exception:
                        pass
                return font
            except OSError:
                continue
    return ImageFont.load_default()


# ---- Main ---------------------------------------------------------------


def main() -> None:
    base = _gradient_background().convert("RGBA")

    # Grain — matches the subtle noise in the Agent UI and DMG.
    base = Image.alpha_composite(base, _grain_overlay(base.size))

    # ── Icon composition ──────────────────────────────────────────────
    icon = _extract_app_icon()
    # Scale to a tasteful sidebar size. 110px reads as the focal point
    # without overwhelming the narrow 164px width.
    icon_size = 110
    icon = icon.resize((icon_size, icon_size), Image.LANCZOS)
    icon_cx = WIDTH // 2
    icon_cy = 90
    icon_pos = (icon_cx - icon_size // 2, icon_cy - icon_size // 2)

    # Soft glow behind the icon.
    glow = _radial_glow(
        base.size,
        (icon_cx, icon_cy),
        inner_radius=icon_size // 2 - 2,
        outer_radius=icon_size // 2 + 30,
        color=GLOW_COLOR,
    )
    base = Image.alpha_composite(base, glow)

    # Icon itself.
    base.paste(icon, icon_pos, icon)

    # ── Typography overlay ───────────────────────────────────────────
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Title: "GAIA" — big, bold, bright.
    title_font = _load_font(34, bold=True)
    title = "GAIA"
    tb = draw.textbbox((0, 0), title, font=title_font)
    tw = tb[2] - tb[0]
    title_y = 170
    draw.text(
        ((WIDTH - tw) // 2, title_y),
        title,
        font=title_font,
        fill=TITLE_COLOR,
    )

    # Subtitle: "Agent UI"
    sub_font = _load_font(13)
    sub = "Agent UI"
    sb = draw.textbbox((0, 0), sub, font=sub_font)
    sw = sb[2] - sb[0]
    sub_y = title_y + 40
    draw.text(
        ((WIDTH - sw) // 2, sub_y),
        sub,
        font=sub_font,
        fill=SUBTITLE_COLOR,
    )

    # AMD red accent line — a thin horizontal rule below the subtitle.
    accent_y = sub_y + 24
    accent_w = 24
    accent_x0 = (WIDTH - accent_w) // 2
    draw.line(
        [(accent_x0, accent_y), (accent_x0 + accent_w, accent_y)],
        fill=ACCENT_COLOR,
        width=2,
    )

    # Tagline — "Privacy-first AI"
    tag_font = _load_font(10)
    tagline = "Privacy-first local AI"
    tg = draw.textbbox((0, 0), tagline, font=tag_font)
    tgw = tg[2] - tg[0]
    tagline_y = accent_y + 12
    draw.text(
        ((WIDTH - tgw) // 2, tagline_y),
        tagline,
        font=tag_font,
        fill=TAGLINE_COLOR,
    )

    # Wordmark at the bottom — monospace, subtle, brand-forward.
    wm_font = _load_font(8, mono=True)
    wordmark = "AMD RYZEN AI"
    wb = draw.textbbox((0, 0), wordmark, font=wm_font)
    ww = wb[2] - wb[0]
    draw.text(
        ((WIDTH - ww) // 2, HEIGHT - 20),
        wordmark,
        font=wm_font,
        fill=WORDMARK_COLOR,
    )

    composed = Image.alpha_composite(base, overlay).convert("RGB")

    # NSIS requires 24-bit BMP (not 32-bit). PIL's 'BMP' encoder on an
    # RGB-mode image produces 24-bit by default.
    composed.save(OUTPUT, "BMP")

    size_kb = OUTPUT.stat().st_size // 1024
    print(f"wrote {OUTPUT} ({size_kb} KB, {WIDTH}x{HEIGHT}, 24-bit BMP)")


if __name__ == "__main__":
    main()
