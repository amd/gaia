# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Image formats the RAG SDK indexes by reading their text with the vision model."""

# Raster formats Pillow decodes; each is re-encoded as PNG before the VLM sees it.
# Indexed only when the user names the file: a directory walk would run the VLM
# on every icon and screenshot it finds.
IMAGE_EXTENSIONS = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
)
