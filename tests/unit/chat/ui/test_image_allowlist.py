# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Images can be indexed when picked, but a folder walk never indexes them (#730)."""

from gaia.ui.utils import ALLOWED_EXTENSIONS, FOLDER_INDEX_EXTENSIONS
from gaia.utils.image_formats import IMAGE_EXTENSIONS


def test_every_image_the_sdk_indexes_can_be_picked():
    assert IMAGE_EXTENSIONS <= ALLOWED_EXTENSIONS


def test_images_are_explicit_only_never_folder_indexed():
    assert not IMAGE_EXTENSIONS & FOLDER_INDEX_EXTENSIONS
    assert ALLOWED_EXTENSIONS == FOLDER_INDEX_EXTENSIONS | IMAGE_EXTENSIONS
