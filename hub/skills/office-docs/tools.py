# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tools provided by the ``office-docs`` starter skill.

The agent builds workbooks with openpyxl through ``run_python``. openpyxl never
computes a formula, so a wrong reference goes unnoticed until a person opens
the file. ``verify_workbook`` recalculates a copy and reports what is broken.

The analysis lives in the sibling ``workbook_check`` module so it can be tested
without registering the tool. The skill loader puts this folder on ``sys.path``
while it imports ``tools.py``, which is what makes the plain import work.

The workbook is read through the agent's allowed-folders check
(``gaia.skills.context.check_read``), and LibreOffice runs on a copy in the
call's temporary folder, as SKILL.md declares (``program:run:soffice``,
``scratch:write``).
"""

from __future__ import annotations

import zipfile
from typing import Any, Dict

import workbook_check

from gaia.agents.base.tools import tool
from gaia.logger import get_logger
from gaia.skills.context import check_read

logger = get_logger(__name__)

#: Formats openpyxl opens; .xls and .ods need converting first.
WORKBOOK_SUFFIXES = (".xlsx", ".xlsm")


@tool(timeout=workbook_check.RECALC_TIMEOUT_S + 60, display_label="Checking workbook")
def verify_workbook(file_path: str) -> Dict[str, Any]:
    """Check every formula in an .xlsx workbook; run it after you build or change one.

    Recalculates a copy (the file is not changed) and names each broken
    formula by cell. Report numbers from the computed values it returns; fix
    every error and check again.

    Args:
        file_path: Absolute path of the .xlsx or .xlsm file.

    Returns:
        ``status``, ``errors``, ``warnings`` and computed values, or
        ``{"status": "error", "error"}``.
    """
    try:
        from openpyxl.utils.exceptions import InvalidFileException
    except ImportError:
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
        return {
            "status": "error",
            "error": (
                f"No file at {path}. Pass the absolute path the build script "
                "printed when it saved the workbook."
            ),
        }
    if path.suffix.lower() not in WORKBOOK_SUFFIXES:
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
        recalc = {"no_recalc_note": workbook_check.NO_LIBREOFFICE_NOTE}
    try:
        return workbook_check.check_workbook(path, **recalc)
    except (
        InvalidFileException,
        zipfile.BadZipFile,
        KeyError,
        ValueError,
        OSError,
    ) as e:
        # What openpyxl raises for a file that is not a valid workbook.
        logger.warning("verify_workbook could not open %s: %s", path, e)
        return {
            "status": "error",
            "error": (
                f"Could not open {path} as a workbook: {e}. Rebuild it "
                "with openpyxl and save it again."
            ),
        }
