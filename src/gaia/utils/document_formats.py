# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Document formats GAIA cannot read, and what the user should convert them to."""

_LEGACY_OFFICE = "a legacy binary Office format"
_OPEN_DOCUMENT = "an OpenDocument format"

#: Extension -> (what it is, the format to re-save it as).
UNSUPPORTED_DOCUMENTS = {
    ".doc": (_LEGACY_OFFICE, ".docx"),
    ".ppt": (_LEGACY_OFFICE, ".pptx"),
    ".xls": (_LEGACY_OFFICE, ".xlsx"),
    ".odt": (_OPEN_DOCUMENT, ".docx"),
    ".odp": (_OPEN_DOCUMENT, ".pptx"),
    ".ods": (_OPEN_DOCUMENT, ".xlsx"),
}


def unsupported_document_message(path: str, ext: str) -> str:
    """Why *path* cannot be read and how to convert it; *ext* must be unsupported."""
    kind, modern = UNSUPPORTED_DOCUMENTS[ext]
    return (
        f"GAIA cannot read {ext} files: {path} is {kind}. Re-save it as "
        f"{modern} or PDF, then index or read that copy."
    )
