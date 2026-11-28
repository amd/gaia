# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Pieces the installer artwork generators share (macOS DMG background, NSIS sidebar)."""

from PIL import Image

BG_TOP = (8, 8, 13)  # --bg-sidebar #08080d
BG_BOTTOM = (14, 14, 22)  # --bg-primary #0e0e16


def vertical_gradient(width: int, height: int) -> Image.Image:
    """BG_TOP to BG_BOTTOM, top to bottom: restrained, like the Agent UI's sidebar-to-content edge."""
    img = Image.new("RGB", (width, height), BG_TOP)
    pixels = img.load()
    for y in range(height):
        t = y / (height - 1)
        row = tuple(round(BG_TOP[i] * (1 - t) + BG_BOTTOM[i] * t) for i in range(3))
        for x in range(width):
            pixels[x, y] = row
    return img
