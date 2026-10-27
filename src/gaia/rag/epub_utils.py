# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""EPUB e-book text extraction for RAG indexing.

An EPUB is a ZIP of XHTML chapters whose reading order is the OPF "spine".
Parsed with the standard library and BeautifulSoup's ``html.parser``, which
ignores DTD entity definitions, so a hostile book cannot inflate itself on parse.
"""

import posixpath
import re
import warnings
import zipfile
import zlib
from pathlib import Path
from typing import List, Tuple
from urllib.parse import unquote

#: Uncompressed size above which an .epub is refused as a likely zip bomb.
MAX_UNCOMPRESSED_BYTES = 500 * 1024 * 1024

#: Chapter markup one book may hand the HTML parser, summed over its chapters.
MAX_CHAPTER_BYTES = 64 * 1024 * 1024

#: Characters one book may produce, about three times War and Peace.
MAX_TEXT_CHARS = 10_000_000

_XHTML_TYPES = {"application/xhtml+xml", "text/html"}
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "section",
        "article",
        "blockquote",
        "pre",
        "li",
        "tr",
        "br",
        "hr",
        "table",
        "ul",
        "ol",
        "dt",
        "dd",
    }
)
_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_SKIPPED = frozenset({"script", "style"})
_BLOCK_END = object()


def _tag(name: str):
    # html.parser keeps namespace prefixes (``opf:item``), so match either form.
    return re.compile(rf"^(?:[\w.-]+:)?{name}$")


_CORRUPT_HINT = (
    "The file appears to be corrupted or not a valid .epub file.\n"
    "Suggestions:\n"
    "  1. Re-download the book\n"
    "  2. Open it in an e-book reader to confirm it is readable\n"
    "  3. Convert it to PDF and index the PDF instead"
)


def _soup(markup: bytes):
    # pylint: disable-next=import-outside-toplevel
    from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        return BeautifulSoup(markup, "html.parser")


def _chapter_text(markup: bytes) -> str:
    # pylint: disable-next=import-outside-toplevel
    from bs4 import CData, NavigableString, Tag

    # One read-only pass: editing the tree costs a scan of the parent per edit.
    parts = []
    soup = _soup(markup)
    stack = [soup.body or soup]
    while stack:
        node = stack.pop()
        if node is _BLOCK_END:
            parts.append("\n")
        elif isinstance(node, NavigableString):
            # Exact types, as get_text matches: no comments, script or style text.
            if type(node) in (NavigableString, CData):
                parts.append(node)
        elif isinstance(node, Tag) and node.name not in _SKIPPED:
            if node.name in _HEADINGS:
                # Headings become Markdown so the chunker sees chapter boundaries.
                title = " ".join(node.get_text(" ").split())
                level = int(node.name[1])
                parts.append(f"\n\n{'#' * level} {title}\n\n" if title else "\n")
                continue
            if node.name in _BLOCK_TAGS:
                stack.append(_BLOCK_END)
            stack.extend(reversed(node.contents))
    lines = [" ".join(line.split()) for line in "".join(parts).splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _read(zf: zipfile.ZipFile, name: str, file_name: str) -> bytes:
    """Read one member; a damaged or unsupported entry raises ValueError."""
    try:
        return zf.read(name)
    except (zipfile.BadZipFile, NotImplementedError, zlib.error, EOFError) as e:
        raise ValueError(
            f"Could not read EPUB file: {file_name}\n"
            f"Reason: entry '{name}' is unreadable ({e})\n{_CORRUPT_HINT}"
        ) from e
    except RuntimeError as e:
        # A password-protected entry; after NotImplementedError, its subclass.
        raise ValueError(
            f"Could not read EPUB file: {file_name}\n"
            f"Reason: entry '{name}' is password-protected ({e})\n"
            "Remove the password with the tool that set it, or convert the "
            "book to PDF and index the PDF instead."
        ) from e


def _member_path(href: str) -> str:
    """Map an href to its zip member name: drop ``#fragment``, ``./``, ``%20``, ``/``."""
    return posixpath.normpath(unquote(href.split("#")[0])).lstrip("/")


def _spine_paths(zf: zipfile.ZipFile, file_name: str) -> List[str]:
    try:
        container = _soup(_read(zf, "META-INF/container.xml", file_name))
    except KeyError as e:
        raise ValueError(
            f"Could not read EPUB file: {file_name}\n"
            f"Reason: META-INF/container.xml is missing\n{_CORRUPT_HINT}"
        ) from e
    rootfile = container.find(_tag("rootfile"))
    opf_path = rootfile.get("full-path") if rootfile else None
    if not opf_path or opf_path not in zf.namelist():
        raise ValueError(
            f"Could not read EPUB file: {file_name}\n"
            f"Reason: the package (.opf) file is missing\n{_CORRUPT_HINT}"
        )

    opf = _soup(_read(zf, opf_path, file_name))
    base = posixpath.dirname(opf_path)
    manifest = {
        item.get("id"): item
        for item in opf.find_all(_tag("item"))
        if item.get("id") and item.get("href")
    }
    # A dict keeps reading order and lists a chapter the spine repeats once.
    paths = {}
    for ref in opf.find_all(_tag("itemref")):
        item = manifest.get(ref.get("idref"))
        if item is None or item.get("media-type") not in _XHTML_TYPES:
            continue
        paths[_member_path(posixpath.join(base, item["href"]))] = None
    return list(paths)


def _encrypted_paths(zf: zipfile.ZipFile, file_name: str) -> set:
    try:
        encryption = _soup(_read(zf, "META-INF/encryption.xml", file_name))
    except KeyError:
        return set()
    return {
        _member_path(ref["uri"])
        for ref in encryption.find_all(_tag("cipherreference"))
        if ref.get("uri")
    }


def _too_large(file_name: str, reason: str) -> ValueError:
    return ValueError(
        f"EPUB file too large to index: {file_name}\n"
        f"Reason: {reason}, the most GAIA indexes from one book.\n"
        "Split it into smaller books (Calibre can do this), or index only the "
        "chapters you need as PDF or text files."
    )


def extract_epub_text(epub_path: str) -> Tuple[str, int]:
    """Extract an EPUB's text in reading order, one Markdown section per chapter.

    Returns:
        ``(text, num_chapters)`` — chapters are the spine documents that
        held any text.

    Raises:
        ValueError: the file is missing, corrupt, password- or DRM-protected,
            or too large to index.
    """
    file_name = Path(epub_path).name

    try:
        zf = zipfile.ZipFile(epub_path, "r")
    except zipfile.BadZipFile as e:
        raise ValueError(
            f"Could not read EPUB file: {file_name}\nReason: {e}\n{_CORRUPT_HINT}"
        ) from e
    except OSError as e:
        raise ValueError(
            f"Could not read EPUB file: {file_name}\n"
            f"Reason: {e}\n"
            "Check that the path exists and points to a readable .epub file."
        ) from e

    with zf:
        total_uncompressed = sum(info.file_size for info in zf.infolist())
        if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
            raise ValueError(
                f"EPUB file too large after decompression: {file_name}\n"
                f"Uncompressed size: {total_uncompressed / (1024*1024):.0f} MB "
                f"(limit: {MAX_UNCOMPRESSED_BYTES / (1024*1024):.0f} MB)\n"
                "The file may be a zip bomb or contain very large embedded media.\n"
                "Convert it to PDF and index the PDF instead."
            )

        spine = _spine_paths(zf, file_name)
        if _encrypted_paths(zf, file_name) & set(spine):
            raise ValueError(
                f"Cannot read EPUB file: {file_name}\n"
                "Its chapters are DRM-encrypted, so the text cannot be extracted.\n"
                "Use a DRM-free copy of the book, or export it to PDF from the "
                "reader that holds the license."
            )

        names = set(zf.namelist())
        chapters = []
        markup_bytes = text_chars = 0
        for path in spine:
            if path not in names:
                raise ValueError(
                    f"Could not read EPUB file: {file_name}\n"
                    f"Reason: chapter '{path}' is listed but missing\n"
                    f"{_CORRUPT_HINT}"
                )
            markup_bytes += zf.getinfo(path).file_size
            if markup_bytes > MAX_CHAPTER_BYTES:
                raise _too_large(
                    file_name,
                    "its chapters hold more than "
                    f"{MAX_CHAPTER_BYTES // (1024 * 1024)} MB of markup",
                )
            text = _chapter_text(_read(zf, path, file_name))
            text_chars += len(text)
            if text_chars > MAX_TEXT_CHARS:
                raise _too_large(
                    file_name, f"it holds more than {MAX_TEXT_CHARS:,} characters"
                )
            if text:
                chapters.append(text)

    return "\n\n".join(chapters), len(chapters)
