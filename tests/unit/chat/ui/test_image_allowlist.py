# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Images can be indexed when picked, but a folder walk never indexes them (#730)."""

import pytest

from gaia.ui.utils import (
    ALLOWED_EXTENSIONS,
    FOLDER_INDEX_EXTENSIONS,
    unsupported_type_message,
)
from gaia.utils.image_formats import IMAGE_EXTENSIONS


def test_every_image_the_sdk_indexes_can_be_picked():
    assert IMAGE_EXTENSIONS <= ALLOWED_EXTENSIONS


def test_images_are_explicit_only_never_folder_indexed():
    assert not IMAGE_EXTENSIONS & FOLDER_INDEX_EXTENSIONS
    assert ALLOWED_EXTENSIONS == FOLDER_INDEX_EXTENSIONS | IMAGE_EXTENSIONS


@pytest.mark.parametrize("ext", [".png", ".jpg", ".webp"])
def test_image_types_are_accepted(ext):
    assert ext in ALLOWED_EXTENSIONS


def test_heic_is_still_rejected_with_a_conversion_hint():
    assert ".heic" not in ALLOWED_EXTENSIONS
    assert "PNG or JPEG" in unsupported_type_message("photo.heic")
