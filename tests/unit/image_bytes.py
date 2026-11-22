# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Real image bytes for tests: ``extract_from_image`` decodes its input first."""

import io

from PIL import Image


def png_bytes(color=(255, 0, 0), size=(1, 1)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


PNG = png_bytes()
