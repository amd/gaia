# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Flatten transparent images onto white before a vision model reads them.

Vision models drop the alpha channel, so a transparent background reads as
black and dark text on it disappears.
"""

import io

_ALPHA_MODES = ("RGBA", "LA", "PA", "RGBa", "La")


class UnreadableImageError(ValueError):
    """Bytes handed to a vision model that Pillow cannot decode as an image."""


def declares_transparency(img) -> bool:
    """Whether *img* has an alpha channel or a transparent palette entry."""
    return img.mode in _ALPHA_MODES or "transparency" in img.info


def flatten_transparency(img):
    """An opaque RGB copy of *img* with any transparency composited onto white.

    Returns *img* itself when it declares no transparency.
    """
    from PIL import Image  # pylint: disable=import-outside-toplevel

    if not declares_transparency(img):
        return img
    rgba = img.convert("RGBA")
    white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    return Image.alpha_composite(white, rgba).convert("RGB")


def flatten_image_bytes(image_bytes: bytes) -> bytes:
    """*image_bytes* with transparent pixels composited onto white, as PNG.

    Returns the input unchanged, byte for byte, when no pixel is transparent.
    Raises :class:`UnreadableImageError` when the bytes are not an image.
    """
    from PIL import (  # pylint: disable=import-outside-toplevel
        Image,
        UnidentifiedImageError,
    )

    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            if not declares_transparency(img):
                return image_bytes
            rgba = img.convert("RGBA")
            if rgba.getchannel("A").getextrema()[0] == 255:
                return image_bytes
            flat = flatten_transparency(rgba)
    except UnidentifiedImageError as e:
        raise UnreadableImageError(
            f"Could not read the image: its {len(image_bytes)} bytes are not an "
            "image format GAIA can decode. Re-export it as PNG or JPEG and try again."
        ) from e
    except (OSError, ValueError, Image.DecompressionBombError) as e:
        raise UnreadableImageError(
            f"Could not read the image: {e}. "
            "Re-export it as PNG or JPEG and try again."
        ) from e

    out = io.BytesIO()
    flat.save(out, format="PNG")
    return out.getvalue()
