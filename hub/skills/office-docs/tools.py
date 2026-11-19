# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tools provided by the ``office-docs`` starter skill.

The agent builds Office files with openpyxl, python-docx and python-pptx
through ``run_python``. Two things go wrong that no script reports: openpyxl
never computes a formula, so a wrong reference goes unnoticed until a person
opens the file; and a model can save plain text under a ``.docx`` name.
``verify_workbook`` recalculates a copy of a workbook and reports what is
broken. ``verify_document`` confirms a .docx or .pptx is a real file of that
kind and counts what is in it.

The analysis lives in the sibling ``workbook_check`` and ``document_check``
modules so it can be tested without registering the tools. The skill loader
puts this folder on ``sys.path`` while it imports ``tools.py``, which is what
makes the plain imports work.

Files are read through the agent's allowed-folders check
(``gaia.skills.context.check_read`` and ``open_read``), and LibreOffice runs on
a copy in the call's temporary folder, as SKILL.md declares
(``program:run:soffice``, ``scratch:write``).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import IO, Any, Dict

import document_check
import workbook_check

from gaia.agents.base.tools import tool
from gaia.logger import get_logger
from gaia.skills.context import check_read, open_read

logger = get_logger(__name__)

#: Formats openpyxl opens; .xls and .ods need converting first.
WORKBOOK_SUFFIXES = (".xlsx", ".xlsm")


def _open_checked(path: Path) -> IO[bytes]:
    return open_read(path, "rb")


def _no_file(path: Path) -> Dict[str, Any]:
    return {
        "status": "error",
        "error": (
            f"No file at {path}. Pass the absolute path the build script "
            "printed when it saved the file."
        ),
    }


@tool(timeout=workbook_check.RECALC_TIMEOUT_S + 60, display_label="Checking workbook")
def verify_workbook(file_path: str) -> Dict[str, Any]:
    """Check every formula in an .xlsx workbook; run it after you build or change one.

    Recalculates a copy when LibreOffice is installed (the file is not
    changed) and names each broken formula by cell. Fix every error and check
    again. Report numbers from ``computed_values``; without them, do what
    ``next`` says.

    Args:
        file_path: Absolute path of the .xlsx or .xlsm file.

    Returns:
        ``status`` (``success``, ``errors_found`` or ``no_formulas``), ``ok``,
        ``errors``, ``warnings`` and computed values, or
        ``{"status": "error", "error"}``.
    """
    if importlib.util.find_spec("openpyxl") is None:
        return {
            "status": "error",
            "error": (
                "openpyxl is not installed, so workbooks cannot be checked. "
                "Ask the user to run `gaia init --profile chat`, which "
                "installs it."
            ),
        }
    path = check_read(file_path)
    if not path.is_file():
        return _no_file(path)
    suffix = path.suffix.lower()
    if suffix in document_check.DOCUMENT_KINDS:
        return {
            "status": "error",
            "error": (
                f"verify_workbook checks .xlsx and .xlsm files; {path} is "
                f"'{suffix}'. Call office-docs/verify_document for it."
            ),
        }
    if suffix not in WORKBOOK_SUFFIXES:
        return {
            "status": "error",
            "error": (
                f"verify_workbook checks .xlsx and .xlsm files; {path} is "
                f"'{path.suffix or 'no extension'}'. Save the workbook "
                "as .xlsx (openpyxl's Workbook.save does) and check that file."
            ),
        }
    if workbook_check.soffice_installed():
        recalc: Dict[str, Any] = {"recalculator": workbook_check.recalculate}
    else:
        recalc = {"not_recalculated": workbook_check.NO_LIBREOFFICE}
    # Only these two are the file's fault; anything else is a bug in the
    # check and must surface as one, not as "rebuild the workbook".
    try:
        return workbook_check.check_workbook(path, opener=_open_checked, **recalc)
    except workbook_check.NotAWorkbook as e:
        logger.warning("verify_workbook could not open %s: %s", path, e)
        return {
            "status": "error",
            "error": (
                f"Could not open {path} as a workbook: {e}. Rebuild it "
                "with openpyxl and save it with Workbook.save."
            ),
        }
    except workbook_check.WorkbookTooLarge as e:
        return {"status": "error", "error": str(e)}


@tool(display_label="Checking document")
def verify_document(file_path: str) -> Dict[str, Any]:
    """Check a saved .docx or .pptx is a real Word or PowerPoint file; run it after you save one.

    Never changes the file. Returns what is in it: for .docx the paragraphs,
    headings by level, tables, pictures and words; for .pptx the slides, their
    titles, native charts and pictures. Compare the counts with what you
    built, and fix what ``warnings`` or the error names.

    Args:
        file_path: Absolute path of the .docx or .pptx file.

    Returns:
        ``status``, ``kind``, the counts, ``warnings`` and ``summary``, or
        ``{"status": "error", "error"}``.
    """
    path = check_read(file_path)
    if not path.is_file():
        return _no_file(path)
    suffix = path.suffix.lower()
    if suffix in WORKBOOK_SUFFIXES:
        return {
            "status": "error",
            "error": (
                f"verify_document checks .docx and .pptx files; {path} is a "
                "workbook. Call office-docs/verify_workbook for it."
            ),
        }
    if suffix not in document_check.DOCUMENT_KINDS:
        return {
            "status": "error",
            "error": (
                f"verify_document checks .docx and .pptx files; {path} is "
                f"'{path.suffix or 'no extension'}'. Save the document with "
                "python-docx (Document.save) or python-pptx "
                "(Presentation.save) under a .docx or .pptx name and check "
                "that file."
            ),
        }
    try:
        return document_check.check_document(path, opener=_open_checked)
    except document_check.NotOfficeFile as e:
        logger.warning("verify_document: %s", e)
        return {"status": "error", "error": str(e)}
