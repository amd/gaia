# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Check that a saved .docx or .pptx is a real Office file, and count what is in it.

A model asked for a Word file sometimes writes Markdown or plain text under a
``.docx`` name. Word refuses to open it, and nothing tells the agent. This
module opens the file as the zip package it has to be, confirms the package is
the kind its name says, and counts headings, tables, pictures, slides and
charts so the agent can compare them with what it meant to build.

It reads with the standard library only, so the check does not depend on
python-docx or python-pptx being importable, and it never writes.
"""

from __future__ import annotations

import codecs
import posixpath
import re
import zipfile
from pathlib import Path
from typing import IO, Any, Callable, Dict, List, Tuple
from xml.etree import ElementTree

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_PIC = "{http://schemas.openxmlformats.org/drawingml/2006/picture}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_TYPES = "{http://schemas.openxmlformats.org/package/2006/content-types}"
_CHART_URI = "http://schemas.openxmlformats.org/drawingml/2006/chart"

#: Suffix -> the kind of package it must hold.
DOCUMENT_KINDS = {".docx": "word", ".pptx": "powerpoint"}

#: Kind -> (what to call the file, a marker of its main part's content type,
#: how to build one).
_KINDS = {
    "word": (
        "a Word file",
        ("wordprocessingml", "ms-word"),
        "Build the document with python-docx in run_python and save it with "
        "Document.save(path); never write a .docx with write_file.",
    ),
    "powerpoint": (
        "a PowerPoint file",
        ("presentationml", "ms-powerpoint"),
        "Build the deck with python-pptx in run_python and save it with "
        "Presentation.save(path); never write a .pptx with write_file.",
    ),
    "excel": ("an Excel workbook", ("spreadsheetml", "ms-excel"), ""),
}

#: A part larger than this is not read: a zip can expand to far more than it holds.
MAX_PART_BYTES = 64 * 1024 * 1024
MAX_LISTED = 40
_TEXT_LIMIT = 100

_HEADING_STYLE = re.compile(r"^heading (\d)$")
_MARKDOWN = re.compile(r"^\s{0,3}#{1,6}\s|\*\*\S")


class NotOfficeFile(Exception):
    """The file is not the kind of Office file its name says. The message is
    written for the agent: what the file is and how to make a real one."""


def _sniff(head: bytes) -> str:
    """What a file that is not a zip package looks like, from its first bytes."""
    if not head:
        return "an empty file"
    if head.startswith(b"%PDF"):
        return "a PDF"
    if head.startswith(b"{\\rtf"):
        return "an RTF file"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "an old binary Office file (.doc or .ppt)"
    try:
        text = codecs.getincrementaldecoder("utf-8")().decode(head)
    except UnicodeDecodeError:
        return "not a zip package"
    if "\x00" in text:
        return "not a zip package"
    if text.lstrip().startswith("<"):
        return "XML or HTML text"
    return "plain text"


def _part(archive: zipfile.ZipFile, name: str, path: Path) -> ElementTree.Element:
    """The parsed XML part *name*."""
    try:
        info = archive.getinfo(name)
    except KeyError as e:
        raise NotOfficeFile(
            f"{path} is damaged: its part '{name}' is missing. Build the file "
            "again and save it with the library."
        ) from e
    if info.file_size > MAX_PART_BYTES:
        raise NotOfficeFile(
            f"{path} cannot be checked: its part '{name}' is "
            f"{info.file_size / 1048576:.0f} MB, over the "
            f"{MAX_PART_BYTES // 1048576} MB this tool reads."
        )
    try:
        return ElementTree.fromstring(archive.read(info))
    except ElementTree.ParseError as e:
        raise NotOfficeFile(
            f"{path} is damaged: its part '{name}' is not valid XML ({e}). "
            "Build the file again and save it with the library."
        ) from e


def _package_kind(archive: zipfile.ZipFile, path: Path) -> Tuple[str, str]:
    """``(kind, main part name)`` of an Office package; kind is a `_KINDS` key."""
    if "[Content_Types].xml" not in archive.namelist():
        return "", ""
    for override in _part(archive, "[Content_Types].xml", path).iter(
        f"{_TYPES}Override"
    ):
        content_type = override.get("ContentType", "")
        if not content_type.endswith("main+xml"):
            continue
        for kind, (_, markers, _) in _KINDS.items():
            if any(marker in content_type for marker in markers):
                return kind, override.get("PartName", "").lstrip("/")
    return "", ""


def _text(element: ElementTree.Element, tag: str) -> str:
    return "".join(node.text or "" for node in element.iter(tag))


def _heading_levels(archive: zipfile.ZipFile, main: str, path: Path) -> Dict[str, int]:
    """Style id -> heading level, following styles based on a heading style."""
    name = posixpath.join(posixpath.dirname(main), "styles.xml")
    if name not in archive.namelist():
        return {}
    level: Dict[str, int] = {}
    based_on: Dict[str, str] = {}
    for style in _part(archive, name, path).iter(f"{_W}style"):
        style_id = style.get(f"{_W}styleId", "")
        label = style.find(f"{_W}name")
        match = _HEADING_STYLE.match(
            (label.get(f"{_W}val", "") if label is not None else "").lower()
        )
        if match:
            level[style_id] = int(match[1])
        parent = style.find(f"{_W}basedOn")
        if parent is not None:
            based_on[style_id] = parent.get(f"{_W}val", "")
    for style_id in based_on:
        seen, current = set(), style_id
        while current in based_on and current not in level and current not in seen:
            seen.add(current)
            current = based_on[current]
        if current in level:
            level.setdefault(style_id, level[current])
    return level


def _word(archive: zipfile.ZipFile, main: str, path: Path) -> Dict[str, Any]:
    levels = _heading_levels(archive, main, path)
    body = _part(archive, main, path)
    paragraphs = words = markdown = 0
    headings: Dict[str, int] = {}
    heading_text: List[str] = []
    for paragraph in body.iter(f"{_W}p"):
        text = _text(paragraph, f"{_W}t").strip()
        if not text:
            continue
        paragraphs += 1
        words += len(text.split())
        style = paragraph.find(f"{_W}pPr/{_W}pStyle")
        level = levels.get(style.get(f"{_W}val", "")) if style is not None else None
        if level is not None:
            headings[str(level)] = headings.get(str(level), 0) + 1
            if len(heading_text) < MAX_LISTED:
                heading_text.append(text[:_TEXT_LIMIT])
        elif _MARKDOWN.search(text):
            markdown += 1
    tables = sum(1 for _ in body.iter(f"{_W}tbl"))
    pictures = sum(1 for _ in body.iter(f"{_PIC}pic"))
    pictures += sum(1 for _ in body.iter(f"{_W}pict"))

    warnings: List[str] = []
    if not (paragraphs or tables or pictures):
        warnings.append(
            "The document is empty: no text, tables or pictures. Add the "
            "content, save it again and check it again."
        )
    elif not headings:
        warnings.append(
            "No heading styles. A memo or report needs real headings "
            "(doc.add_heading(text, level=1)); a short letter does not."
        )
    if markdown:
        warnings.append(
            f"{markdown} paragraph(s) hold Markdown markup typed as text "
            "(# or **). Use add_heading and bold runs instead."
        )
    return {
        "paragraphs": paragraphs,
        "headings": dict(sorted(headings.items())),
        "heading_text": heading_text,
        "tables": tables,
        "pictures": pictures,
        "words": words,
        "warnings": warnings,
        "summary": (
            f"A Word file with {paragraphs} paragraph(s), "
            f"{sum(headings.values())} heading(s), {tables} table(s), "
            f"{pictures} picture(s) and {words} word(s)."
        ),
    }


def _slide_parts(archive: zipfile.ZipFile, main: str, path: Path) -> List[str]:
    """The slides' part names, in presentation order."""
    folder = posixpath.dirname(main)
    rels_name = posixpath.join(folder, "_rels", posixpath.basename(main) + ".rels")
    targets = {
        rel.get("Id", ""): rel.get("Target", "")
        for rel in _part(archive, rels_name, path).iter(f"{_REL}Relationship")
    }
    parts: List[str] = []
    for slide in _part(archive, main, path).iter(f"{_P}sldId"):
        target = targets.get(slide.get(f"{_R}id", ""), "")
        if target.startswith("/"):
            parts.append(target.lstrip("/"))
        else:
            parts.append(posixpath.normpath(posixpath.join(folder, target)))
    return parts


def _slide_title(slide: ElementTree.Element) -> str:
    for shape in slide.iter(f"{_P}sp"):
        holder = shape.find(f"{_P}nvSpPr/{_P}nvPr/{_P}ph")
        if holder is not None and holder.get("type") in ("title", "ctrTitle"):
            return " ".join(_text(shape, f"{_A}t").split())
    return ""


def _powerpoint(archive: zipfile.ZipFile, main: str, path: Path) -> Dict[str, Any]:
    titles: List[str] = []
    untitled: List[int] = []
    charts = pictures = 0
    parts = _slide_parts(archive, main, path)
    for number, name in enumerate(parts, start=1):
        slide = _part(archive, name, path)
        title = _slide_title(slide)
        if not title:
            untitled.append(number)
        if len(titles) < MAX_LISTED:
            titles.append(title[:_TEXT_LIMIT])
        charts += sum(
            1
            for data in slide.iter(f"{_A}graphicData")
            if data.get("uri") == _CHART_URI
        )
        pictures += sum(1 for _ in slide.iter(f"{_P}pic"))

    warnings: List[str] = []
    if not parts:
        warnings.append(
            "The deck has no slides. Add them with prs.slides.add_slide, save "
            "it again and check it again."
        )
    if untitled:
        shown = ", ".join(str(n) for n in untitled[:MAX_LISTED])
        warnings.append(f"Slide(s) with no title: {shown}. Give every slide a title.")
    return {
        "slides": len(parts),
        "titles": titles,
        "charts": charts,
        "pictures": pictures,
        "slides_without_title": untitled[:MAX_LISTED],
        "warnings": warnings,
        "summary": (
            f"A PowerPoint file with {len(parts)} slide(s), {charts} native "
            f"chart(s) and {pictures} picture(s)."
        ),
    }


def check_document(
    path: Path, *, opener: Callable[[Path], IO[bytes]]
) -> Dict[str, Any]:
    """Confirm *path* is the Office file its suffix says and count its contents.

    Args:
        path: An existing file whose suffix is in :data:`DOCUMENT_KINDS`.
        opener: Opens *path* for reading bytes. The tool passes the skill
            context's ``open_read``, so the file is read only if the agent
            may read it.

    Returns:
        ``status``, ``kind``, the counts for that kind, ``warnings`` and a
        one-line ``summary``.

    Raises:
        NotOfficeFile: *path* is not a package of that kind, or is damaged.
    """
    expected = DOCUMENT_KINDS[path.suffix.lower()]
    wanted, _, how = _KINDS[expected]
    with opener(path) as stream:
        head = stream.read(2048)
        stream.seek(0)
        if not zipfile.is_zipfile(stream):
            raise NotOfficeFile(
                f"{path} is not {wanted}: it is {_sniff(head)} saved under a "
                f"{path.suffix.lower()} name. {how}"
            )
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            kind, main = _package_kind(archive, path)
            if kind != expected:
                actual = _KINDS[kind][0] if kind else "a zip archive"
                raise NotOfficeFile(
                    f"{path} is not {wanted}: it is {actual} saved under a "
                    f"{path.suffix.lower()} name. {how}"
                )
            counts = (_word if kind == "word" else _powerpoint)(archive, main, path)
    return {"status": "success", "file_path": str(path), "kind": kind, **counts}
