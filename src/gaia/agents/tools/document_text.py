# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Full text of Word, PDF, Excel and PowerPoint files for the read tools, in budget-sized parts.

Reading a handful of documents in full is how cross-document facts get noticed;
retrieval only returns the passages a query happened to ask for. Each part fits
the model's tool-result budget and says where the next one starts, so a long
document is read completely instead of being silently cut.
"""

import math
import os
import re
from collections.abc import Mapping
from typing import Any, Dict, List, Optional, Tuple

from gaia.agents.install_hints import missing_package_hint
from gaia.utils.document_formats import (
    UNSUPPORTED_DOCUMENTS,
    unsupported_document_message,
)

#: Extensions read_file extracts text from, and the file_type it reports.
READABLE_DOCUMENTS = {
    ".docx": "docx",
    ".pdf": "pdf",
    ".xlsx": "xlsx",
    ".pptx": "pptx",
}

#: Document formats only index_document can read.
INDEX_ONLY_DOCUMENTS = frozenset({".epub"})

#: Web pages read_file returns as text, without the markup.
HTML_PAGES = frozenset({".html", ".htm"})

#: Chars one read returns when the host has no model-sized budget.
DEFAULT_READ_CHARS = 20000

#: Past this many reads, a document gets a hint that retrieval is cheaper.
FULL_READ_MAX_READS = 3

#: Extraction stops here; a longer document is for index_document.
MAX_EXTRACT_CHARS = 1_000_000

#: A character ``limit`` this small is almost always meant as a line count.
SMALL_LIMIT_CHARS = 1000

#: Extracted documents kept per agent, so paging does not re-parse the file.
_CACHE_ENTRIES = 8

#: What a too-long workbook's overview shows of each sheet, and of how many.
OVERVIEW_ROWS = 5
OVERVIEW_COLUMNS = 12
OVERVIEW_CELL_CHARS = 40
OVERVIEW_SHEETS = 40

#: Most loaded-skill tools one hint names.
_MAX_SKILL_TOOLS = 3
_PATH_PARAMS = ("file_path", "path")


class DocumentTooLong(ValueError):
    """The document's text is past ``MAX_EXTRACT_CHARS``."""


def read_budget(host: Any) -> int:
    """Chars one read may return: the host's tool-result target, so it is never truncated."""
    budget = getattr(host, "_truncation_budget", None)
    return budget()[1] if callable(budget) else DEFAULT_READ_CHARS


def document_type(path: str) -> Optional[str]:
    """``"docx"`` / ``"pdf"`` / ``"xlsx"`` / ``"pptx"`` for a readable document, else ``None``."""
    return READABLE_DOCUMENTS.get(os.path.splitext(path)[1].lower())


def _host_tools(host: Any) -> Optional[Mapping]:
    """The tools *host* may call, or ``None`` when it does not say."""
    registry = getattr(host, "_tools_registry", None)
    return registry if isinstance(registry, Mapping) else None


def skill_tools_for(host: Any, path: str) -> List[str]:
    """Loaded skills' tools that take a file and say they handle *path*'s type.

    Read from what each tool declares (its description and parameters), so a
    skill installed later is found the same way.
    """
    ext = os.path.splitext(path)[1].lower()
    registry = _host_tools(host)
    if not ext or not registry:
        return []
    names_type = re.compile(re.escape(ext) + r"(?!\w)", re.IGNORECASE)
    found = []
    for name, entry in registry.items():
        if not isinstance(entry, Mapping) or not entry.get("skill"):
            continue
        params = entry.get("parameters")
        params = params if isinstance(params, Mapping) else {}
        if not any(key in params for key in _PATH_PARAMS):
            continue
        described = [str(entry.get("description") or "")]
        described += [
            str(spec.get("description") or "")
            for spec in params.values()
            if isinstance(spec, Mapping)
        ]
        if names_type.search(" ".join(described)):
            found.append(name)
    return sorted(found)[:_MAX_SKILL_TOOLS]


def other_routes(host: Any, path: str, ranges: bool = False) -> str:
    """Other ways to read *path*, best first: a loaded skill's tool, then
    (with *ranges*) code that reads a range of rows, then retrieval.

    A tool is named only when *host* has it; a host that does not say what it
    has gets the retrieval route.
    """
    ext = os.path.splitext(path)[1].lower()
    registry = _host_tools(host)
    routes = []
    tools = skill_tools_for(host, path)
    if tools:
        routes.append(
            f"A loaded skill has a tool for {ext} files; use it first: "
            f"{', '.join(tools)}."
        )
    if ranges and registry is not None and "run_python" in registry:
        routes.append(
            "To read a range of rows, use run_python: pandas.read_excel(path, "
            "sheet_name=..., skiprows=..., nrows=...), or openpyxl in "
            "read_only mode with iter_rows(min_row=..., max_row=...)."
        )
    if registry is None or "index_document" in registry:
        routes.append(
            f"To look up a passage of text, call index_document('{path}'), then "
            "query_specific_file."
        )
    return " ".join(routes)


def index_only_error(path: str, host: Any = None) -> Optional[Dict[str, Any]]:
    """The error for a binary document read_file cannot extract, else ``None``."""
    ext = os.path.splitext(path)[1].lower()
    if ext in UNSUPPORTED_DOCUMENTS:
        return {"status": "error", "error": unsupported_document_message(path, ext)}
    if ext not in INDEX_ONLY_DOCUMENTS:
        return None
    return {
        "status": "error",
        "error": (
            f"read_file cannot extract {ext} files. {other_routes(host, path)} "
            "read_file reads .docx, .pdf, .xlsx and .pptx text directly."
        ),
    }


def _too_long(path: str) -> DocumentTooLong:
    return DocumentTooLong(
        f"{os.path.basename(path)} has more than {MAX_EXTRACT_CHARS:,} characters "
        "of text, too long to read in full."
    )


def too_long_error(host: Any, path: str, error: DocumentTooLong) -> Dict[str, Any]:
    """A too-long document's error, with the other ways to read it."""
    return {
        "status": "error",
        "file_path": path,
        "error": f"{error} {other_routes(host, path)}".strip(),
    }


def _pptx_text(path: str) -> Tuple[str, Dict[str, Any]]:
    try:
        from pptx import Presentation  # pylint: disable=import-outside-toplevel
    except ImportError as e:
        raise ImportError(
            "Reading PowerPoint text requires python-pptx. "
            + missing_package_hint("python-pptx")
        ) from e
    from pptx.shapes.picture import (  # pylint: disable=import-outside-toplevel
        Picture,
    )

    from gaia.rag.pptx_utils import (  # pylint: disable=import-outside-toplevel
        extract_notes_from_slide,
        extract_text_from_slide,
    )

    name = os.path.basename(path)
    try:
        # A stream, not the path: given a path, python-pptx reports a locked
        # file as not a package.
        with open(path, "rb") as stream:
            deck = Presentation(stream)
    except OSError:
        raise
    except Exception as e:
        raise ValueError(
            f"Could not read PowerPoint file {name}: {e}. It may be corrupted "
            "or not a .pptx presentation."
        ) from e

    parts, size, unread = [], 0, 0
    for number, slide in enumerate(deck.slides, 1):
        text = extract_text_from_slide(slide, number).strip()
        notes = extract_notes_from_slide(slide)
        unread += sum(
            1
            for shape in slide.shapes
            if getattr(shape, "has_chart", False) or isinstance(shape, Picture)
        )
        part = f"[Slide {number}]\n{text}"
        if notes:
            part += f"\n[Notes]\n{notes}"
        parts.append(part)
        size += len(part) + 2
        if size > MAX_EXTRACT_CHARS:
            raise _too_long(path)
    meta: Dict[str, Any] = {"slides": len(parts)}
    if unread:
        meta["warning"] = (
            f"{unread} chart(s) or picture(s) are not in this text: it holds "
            "the slides' words, tables and speaker notes only."
        )
    return "\n\n".join(parts), meta


def _clip_cell(value: Any) -> str:
    text = "" if value is None else " ".join(str(value).split())
    if len(text) > OVERVIEW_CELL_CHARS:
        return text[: OVERVIEW_CELL_CHARS - 1] + "\u2026"
    return text


def workbook_overview(path: str, max_chars: int) -> Tuple[str, List[Dict[str, Any]]]:
    """Each sheet's name, size and first rows, in at most about *max_chars*.

    For a workbook too long to read in full: enough to see what is where and
    ask for the part that matters. Sizes are the ones the file records;
    ``None`` when it records none.
    """
    try:
        import openpyxl  # pylint: disable=import-outside-toplevel
    except ImportError as e:
        raise ImportError(
            "Reading Excel text requires openpyxl. " + missing_package_hint("openpyxl")
        ) from e
    try:
        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as e:
        raise ValueError(
            f"Could not read Excel file {os.path.basename(path)}: {e}. It may "
            "be corrupted or not an .xlsx workbook."
        ) from e
    try:
        sheets, parts, size = [], [], 0
        for index, sheet in enumerate(book.worksheets):
            rows, columns = sheet.max_row, sheet.max_column
            sheets.append({"sheet": sheet.title, "rows": rows, "columns": columns})
            shape = (
                f"{rows:,} rows x {columns:,} columns"
                if rows and columns
                else "size not recorded in the file"
            )
            block = [f"[Sheet: {sheet.title}] {shape}"]
            if index < OVERVIEW_SHEETS and size < max_chars:
                shown = 0
                for row in sheet.iter_rows(values_only=True):
                    cells = [_clip_cell(cell) for cell in row]
                    while cells and not cells[-1]:
                        cells.pop()
                    if not cells:
                        continue
                    more = len(cells) - OVERVIEW_COLUMNS
                    line = " | ".join(cells[:OVERVIEW_COLUMNS])
                    if more > 0:
                        line += f" | \u2026 (+{more} more columns)"
                    block.append(line)
                    shown += 1
                    if shown == OVERVIEW_ROWS:
                        break
            text = "\n".join(block)
            parts.append(text)
            size += len(text) + 2
        return "\n\n".join(parts), sheets
    finally:
        book.close()


def _workbook_overview_result(
    host: Any, path: str, error: DocumentTooLong
) -> Dict[str, Any]:
    """What a too-long workbook holds, in place of an error that says only that."""
    try:
        overview, sheets = workbook_overview(path, read_budget(host))
    except (ImportError, ValueError) as e:
        return {"status": "error", "file_path": path, "error": str(e)}
    return {
        "status": "success",
        "file_path": path,
        "file_type": "xlsx",
        # Not "content": this is not the file's text, and no read-back of it.
        "overview": overview,
        "sheets": len(sheets),
        "sheet_sizes": sheets,
        "note": (
            f"{error} This is each sheet's size and its first rows, not the "
            f"data. {other_routes(host, path, ranges=True)}"
        ).strip(),
    }


def _pdf_text(path: str) -> Tuple[str, Dict[str, Any]]:
    try:
        from pypdf.errors import (  # pylint: disable=import-outside-toplevel
            LimitReachedError,
        )
    except ImportError as e:
        raise ImportError(
            "Reading PDF text requires pypdf. Install it with: "
            'uv pip install -e ".[rag]"'
        ) from e
    from gaia.rag.pdf_utils import (  # pylint: disable=import-outside-toplevel
        pdf_parse_limits,
    )

    try:
        with pdf_parse_limits():
            return _read_pdf_pages(path)
    except LimitReachedError as e:
        raise ValueError(
            f"{os.path.basename(path)} is too complex for GAIA to read safely "
            f"({e}). Ask the user to print it to a new PDF, or split it, and "
            "share that file."
        ) from e


def _read_pdf_pages(path: str) -> Tuple[str, Dict[str, Any]]:
    from pypdf import PdfReader  # pylint: disable=import-outside-toplevel
    from pypdf.errors import PdfReadError  # pylint: disable=import-outside-toplevel

    name = os.path.basename(path)
    try:
        reader = PdfReader(path)
    except PdfReadError as e:
        raise ValueError(
            f"Could not read PDF {name}: {e}. The file looks corrupted or is not "
            "a PDF; re-download or re-export it."
        ) from e
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError(
            f"{name} is password-protected. Ask the user for an unlocked copy."
        )

    pages, size, has_text = [], 0, False
    for number, page in enumerate(reader.pages, 1):
        text = (page.extract_text() or "").strip()
        has_text = has_text or bool(text)
        pages.append(f"[Page {number}]\n{text}")
        size += len(pages[-1]) + 2
        if size > MAX_EXTRACT_CHARS:
            raise _too_long(path)
    if not has_text:
        raise ValueError(
            f"{name} has {len(pages)} page(s) but no text layer — it is probably "
            "a scan. index_document can read it with the vision model, if one is "
            "installed."
        )
    return "\n\n".join(pages), {"pages": len(pages)}


def _xlsx_text(path: str) -> Tuple[str, Dict[str, Any]]:
    try:
        import openpyxl  # pylint: disable=import-outside-toplevel
    except ImportError as e:
        raise ImportError(
            "Reading Excel text requires openpyxl. Install it with: "
            'uv pip install -e ".[rag]"'
        ) from e

    def load(data_only: bool):
        try:
            return openpyxl.load_workbook(path, read_only=True, data_only=data_only)
        except Exception as e:
            raise ValueError(
                f"Could not read Excel file {os.path.basename(path)}: {e}. It may "
                "be corrupted or not an .xlsx workbook."
            ) from e

    values = load(data_only=True)
    try:
        formulas = load(data_only=False)
    except ValueError:
        values.close()
        raise
    try:
        parts, size, uncomputed = [], 0, 0
        for sheet, source in zip(values.worksheets, formulas.worksheets):
            parts.append(f"[Sheet: {sheet.title}]")
            rows = zip(sheet.iter_rows(values_only=True), source.iter_rows())
            for row, cells_in in rows:
                for value, cell in zip(row, cells_in):
                    if value is None and getattr(cell, "data_type", None) == "f":
                        uncomputed += 1
                cells = ["" if cell is None else str(cell) for cell in row]
                while cells and not cells[-1].strip():
                    cells.pop()
                if cells:
                    parts.append(" | ".join(cells))
                    size += len(parts[-1]) + 1
                    if size > MAX_EXTRACT_CHARS:
                        raise _too_long(path)
        meta: Dict[str, Any] = {"sheets": len(values.worksheets)}
        if uncomputed:
            # A workbook saved by a script has formulas but no computed values.
            meta["warning"] = (
                f"{uncomputed} formula cell(s) have no saved value and read as "
                "blank; open and save the file in Excel, or compute them yourself."
            )
        return "\n".join(parts), meta
    finally:
        values.close()
        formulas.close()


def extract_document_text(path: str) -> Tuple[str, Dict[str, Any]]:
    """``(text, metadata)`` for a .docx, .pdf, .xlsx or .pptx file.

    PDF text carries ``[Page N]`` markers, Excel text ``[Sheet: name]`` and
    PowerPoint text ``[Slide N]``, so a statement can be cited by where it
    came from.

    Raises:
        ImportError: the format's parser is not installed.
        ValueError: the file is unreadable, encrypted, has no text, or is too
            long to read in full.
    """
    kind = document_type(path)
    if kind == "pdf":
        return _pdf_text(path)
    if kind == "xlsx":
        return _xlsx_text(path)
    if kind == "pptx":
        return _pptx_text(path)
    if kind == "docx":
        from gaia.rag.docx_utils import (  # pylint: disable=import-outside-toplevel
            extract_docx_text,
        )

        text = extract_docx_text(path)
        if len(text) > MAX_EXTRACT_CHARS:
            raise _too_long(path)
        return text, {}
    raise ValueError(f"Not a .docx, .pdf, .xlsx or .pptx file: {path}")


def cached_document_text(host: Any, path: str) -> Tuple[str, Dict[str, Any]]:
    """:func:`extract_document_text`, reused while the file is unchanged."""
    real = os.path.realpath(path)
    stat = os.stat(real)
    key = (real, stat.st_mtime_ns, stat.st_size)
    cache = getattr(host, "_document_text_cache", None)
    if cache is None:
        cache = {}
        host._document_text_cache = cache
    entry = cache.pop(key, None)
    if entry is None:
        try:
            entry = extract_document_text(real)
        except DocumentTooLong as too_long:
            # Kept too: finding out again means parsing a million characters.
            entry = too_long
    cache[key] = entry
    while len(cache) > _CACHE_ENTRIES:
        cache.pop(next(iter(cache)))
    if isinstance(entry, DocumentTooLong):
        raise entry
    return entry


def read_document(
    host: Any, path: str, offset: int = 0, limit: Optional[int] = None
) -> Dict[str, Any]:
    """One part of a document's extracted text, and where the next part starts.

    The caller has already checked that *path* may be read. A part is at most
    the host's read budget; ``next_offset`` is ``None`` once the end is reached.
    """
    kind = document_type(path)
    if (
        not isinstance(offset, int)
        or isinstance(offset, bool)
        or offset < 0
        or (limit is not None and (not isinstance(limit, int) or limit < 1))
    ):
        return {
            "status": "error",
            "error": "offset must be 0 or more and limit 1 or more characters.",
        }
    try:
        text, meta = cached_document_text(host, path)
    except DocumentTooLong as e:
        if kind == "xlsx":
            return _workbook_overview_result(host, path, e)
        return too_long_error(host, path, e)
    except (ImportError, ValueError) as e:
        return {"status": "error", "file_path": path, "error": str(e)}
    return _text_part(host, path, kind, text, meta, offset, limit)


def html_page_text(path: str) -> str:
    """The readable text of an HTML file: no tags, scripts or styles.

    Raises:
        ImportError: the HTML parser is not installed.
        ValueError: the page is too large to parse.
    """
    try:
        import bs4  # noqa: F401  pylint: disable=import-outside-toplevel,unused-import
    except ImportError as e:
        raise ImportError(
            "Reading an HTML file as text requires beautifulsoup4. "
            + missing_package_hint("beautifulsoup4")
        ) from e
    from gaia.rag.html_utils import (  # pylint: disable=import-outside-toplevel
        extract_html_text,
    )

    with open(path, "rb") as stream:
        return extract_html_text(path, stream.read())


def read_html_text(
    host: Any, path: str, offset: int = 0, limit: Optional[int] = None
) -> Dict[str, Any]:
    """One part of an HTML file's text, paged like a document's.

    The markup is what the edit tools match, so the result says how to get it.
    """
    how_raw = (
        "For the markup itself, e.g. before editing the file, call read_file "
        "with raw=true or with start_line/end_line."
    )
    if (
        not isinstance(offset, int)
        or isinstance(offset, bool)
        or offset < 0
        or (limit is not None and (not isinstance(limit, int) or limit < 1))
    ):
        return {
            "status": "error",
            "error": "offset must be 0 or more and limit 1 or more characters.",
        }
    real = os.path.realpath(path)
    stat = os.stat(real)
    key = (real, stat.st_mtime_ns, stat.st_size, "html")
    cache = getattr(host, "_document_text_cache", None)
    if cache is None:
        cache = {}
        host._document_text_cache = cache
    text = cache.pop(key, None)
    if text is None:
        try:
            text = html_page_text(real)
        except (ImportError, ValueError) as e:
            return {"status": "error", "file_path": path, "error": f"{e} {how_raw}"}
    cache[key] = text
    while len(cache) > _CACHE_ENTRIES:
        cache.pop(next(iter(cache)))
    part = _text_part(host, path, "html", text, {}, offset, limit)
    if part.get("status") == "success":
        removed = (
            "Removed: this is the page's text."
            if text.strip()
            else "Removed, and nothing is left: the page has no text outside "
            "its markup and scripts."
        )
        part["markup"] = f"{removed} {how_raw}"
        part.pop("hint", None)
    return part


def _text_part(
    host: Any,
    path: str,
    kind: Optional[str],
    text: str,
    meta: Dict[str, Any],
    offset: int,
    limit: Optional[int],
) -> Dict[str, Any]:
    """Characters *offset*.. of *text*, within the host's read budget."""
    total = len(text)
    if offset and offset >= total:
        return {
            "status": "error",
            "error": f"offset {offset} is past the end: the text has {total} characters.",
        }
    budget = read_budget(host)
    size = budget if limit is None else min(limit, budget)
    end = min(total, offset + size)
    if end < total:
        # End the part on a line break when one is near, so no line is split.
        cut = text.rfind("\n", offset + size * 4 // 5, end)
        if cut > offset:
            end = cut + 1

    result: Dict[str, Any] = {
        "status": "success",
        "file_path": path,
        "file_type": kind,
        "content": text[offset:end],
        "offset": offset,
        "total_chars": total,
        "next_offset": end if end < total else None,
        **meta,
    }
    if end < total:
        result["note"] = (
            f"Partial: characters {offset:,}-{end:,} of {total:,}. Call read_file "
            f"with offset={end} for the next part. Read every part before "
            "comparing documents or saying something is not in this one."
        )
    if offset == 0 and total > FULL_READ_MAX_READS * budget:
        result["hint"] = (
            f"Long document: about {math.ceil(total / size)} reads in full. To look "
            "up specific facts, index_document + query_specific_file is cheaper; "
            "read it all only when the task needs every line."
        )
    return result


def text_page_notes(
    page: Dict[str, Any], size_bytes: int, line_ranges: bool = True
) -> Dict[str, Any]:
    """Say how much of the file a character page holds, and where the rest is."""
    returned = len(page.get("content") or "")
    start = page.get("offset") or 0
    page = {**page, "chars_returned": returned, "size_bytes": size_bytes}
    if page.get("next_offset") is not None:
        by_line = (
            ", or read by line number with start_line/end_line" if line_ranges else ""
        )
        page["note"] = (
            f"Partial: characters {start:,}-{start + returned:,} of a "
            f"{size_bytes:,}-byte file. Continue with "
            f"offset={page['next_offset']}{by_line}."
        )
    return page


def with_small_limit_warning(
    result: Dict[str, Any], limit: Optional[int], line_ranges: bool = True
) -> Dict[str, Any]:
    """A tiny ``limit`` is usually a line count; say it counts characters."""
    if (
        result.get("status") != "success"
        or not isinstance(limit, int)
        or isinstance(limit, bool)
        or limit >= SMALL_LIMIT_CHARS
    ):
        return result
    returned = len(result.get("content") or "")
    by_line = "To read lines, use start_line/end_line instead. " if line_ranges else ""
    return {
        **result,
        "warning": (
            f"limit={limit} counts characters, not lines, so only {returned} "
            f"characters came back. {by_line}For more text per read, raise "
            "limit or leave it out."
        ),
    }
