# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Images can be indexed when picked, but a folder walk never indexes them (#730)."""

import pytest
from fastapi import HTTPException

from gaia.ui.utils import (
    ALLOWED_EXTENSIONS,
    FOLDER_INDEX_EXTENSIONS,
    sanitize_document_path,
)
from gaia.utils.image_formats import IMAGE_EXTENSIONS


def test_every_image_the_sdk_indexes_can_be_picked():
    assert IMAGE_EXTENSIONS <= ALLOWED_EXTENSIONS


def test_images_are_explicit_only_never_folder_indexed():
    assert not IMAGE_EXTENSIONS & FOLDER_INDEX_EXTENSIONS
    assert ALLOWED_EXTENSIONS == FOLDER_INDEX_EXTENSIONS | IMAGE_EXTENSIONS


@pytest.mark.parametrize("name", ["shot.png", "scan.JPG", "page.webp"])
def test_image_paths_are_accepted(tmp_path, name):
    assert sanitize_document_path(str(tmp_path / name)).name == name


def test_heic_is_still_rejected_with_a_conversion_hint(tmp_path):
    with pytest.raises(HTTPException) as excinfo:
        sanitize_document_path(str(tmp_path / "photo.heic"))
    assert excinfo.value.status_code == 400
    assert "PNG or JPEG" in excinfo.value.detail
