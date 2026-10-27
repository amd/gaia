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
MAX_CHAPTER_BYTES = 16 * 1024 * 1024

#: ``<`` one book's chapters may hold. A parsed tag costs ~1.5 KB of RAM and
#: ~20 us, so this keeps a book near 0.6 GB; the King James Bible has 51,000.
MAX_CHAPTER_TAGS = 400_000

#: Size and ``<`` caps for each of container.xml, the OPF and encryption.xml.
MAX_PACKAGE_BYTES = 1024 * 1024
MAX_PACKAGE_TAGS = 100_000

#: Characters one book may produce, about three times War and Peace.
MAX_TEXT_CHARS = 10_000_000

_TAG_NAME = re.compile(rb"<[A-Za-z][\w:.-]*")
_TAG_STOP = re.compile(rb"""[>"']""")

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


def _drop_attributes(markup: bytes) -> bytes:
    """Strip start-tag attributes: text never needs them and each costs parser RAM.

    Linear: every byte is scanned a bounded number of times, and a tag that
    never closes loses its attributes up to the next ``<``.
    """
    out = []
    pos = 0
    while (name := _TAG_NAME.search(markup, pos)) is not None:
        out.append(markup[pos : name.end()])
        end = markup.find(b"<", name.end())
        end = len(markup) if end == -1 else end
        pos = end
        cursor = name.end()
        while (stop := _TAG_STOP.search(markup, cursor, end)) is not None:
            if stop[0] == b">":
                if markup[stop.start() - 1 : stop.start()] == b"/":
                    out.append(b"/")
                out.append(b">")
                pos = stop.end()
                break
            closing = markup.find(stop[0], stop.end(), end)
            # An unmatched quote (``class=it's``) is an ordinary character.
            cursor = stop.end() if closing == -1 else closing + 1
    out.append(markup[pos:])
    return b"".join(out)


def _chapter_text(markup: bytes) -> str:
    # pylint: disable-next=import-outside-toplevel
    from bs4 import CData, NavigableString, Tag

    # One read-only pass: editing the tree costs a scan of the parent per edit.
    parts = []
    soup = _soup(_drop_attributes(markup))
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


def _package_soup(zf: zipfile.ZipFile, name: str, file_name: str):
    """Parse a small package file; KeyError when it is absent."""
    if zf.getinfo(name).file_size > MAX_PACKAGE_BYTES:
        raise _too_large(
            file_name,
            f"its {name} is over {MAX_PACKAGE_BYTES // 1024:,} KB, "
            "the most GAIA reads from that file",
        )
    markup = _read(zf, name, file_name)
    if markup.count(b"<") > MAX_PACKAGE_TAGS:
        raise _too_large(
            file_name,
            f"its {name} has more than {MAX_PACKAGE_TAGS:,} tags, "
            "the most GAIA reads from that file",
        )
    return _soup(markup)


def _spine_paths(zf: zipfile.ZipFile, file_name: str) -> List[str]:
    try:
        container = _package_soup(zf, "META-INF/container.xml", file_name)
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

    opf = _package_soup(zf, opf_path, file_name)
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
        encryption = _package_soup(zf, "META-INF/encryption.xml", file_name)
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
        f"Reason: {reason}.\n"
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
        markup_bytes = tags = text_chars = 0
        per_book = "the most GAIA indexes from one book"
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
                    f"{MAX_CHAPTER_BYTES // (1024 * 1024)} MB of markup, {per_book}",
                )
            markup = _read(zf, path, file_name)
            # Counted before parsing: the parsed tree costs RAM per tag.
            tags += markup.count(b"<")
            if tags > MAX_CHAPTER_TAGS:
                raise _too_large(
                    file_name,
                    f"its chapters hold more than {MAX_CHAPTER_TAGS:,} tags, "
                    f"{per_book}",
                )
            text = _chapter_text(markup)
            text_chars += len(text)
            if text_chars > MAX_TEXT_CHARS:
                raise _too_large(
                    file_name,
                    f"it holds more than {MAX_TEXT_CHARS:,} characters, {per_book}",
                )
            if text:
                chapters.append(text)

    return "\n\n".join(chapters), len(chapters)
