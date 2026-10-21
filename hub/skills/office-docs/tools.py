# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tools provided by the ``office-docs`` starter skill.

The agent builds workbooks with openpyxl through ``run_python``. openpyxl never
computes a formula, so a wrong reference goes unnoticed until a person opens
the file. ``verify_workbook`` recalculates a copy and reports what is broken.

The analysis lives in the sibling ``workbook_check`` module so it can be tested
without registering the tool. The skill loader puts this folder on ``sys.path``
while it imports ``tools.py``, which is what makes the plain import work.
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path
from typing import Any, Dict

from workbook_check import RECALC_TIMEOUT_S, check_workbook

from gaia.agents.base.tools import tool
from gaia.logger import get_logger

logger = get_logger(__name__)

#: Formats openpyxl opens; .xls and .ods need converting first.
WORKBOOK_SUFFIXES = (".xlsx", ".xlsm")


@tool(timeout=RECALC_TIMEOUT_S + 60, display_label="Checking workbook")
def verify_workbook(file_path: str) -> Dict[str, Any]:
    """Check every formula in an .xlsx workbook; run it after you build or change one.

    Recalculates a copy (the file itself is not changed) and reports #REF!,
    #VALUE!, #DIV/0!, #NAME?, circular references, SUM/SUMIFS over text or
    empty cells, criteria that match no row, and arithmetic on empty or text
    cells — each with the cell and formula. Also returns computed values, so
    report numbers from those. Fix every error, save, and check again.

    Args:
        file_path: Absolute path of the .xlsx or .xlsm file.

    Returns:
        The check result (``status``, ``errors``, ``warnings``, computed
        values), or ``{"status": "error", "error"}`` saying why the file could
        not be checked.
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
    path = os.path.expanduser(file_path)
    if not os.path.isfile(path):
        return {
            "status": "error",
            "error": (
                f"No file at {path}. Pass the absolute path the build script "
                "printed when it saved the workbook."
            ),
        }
    if Path(path).suffix.lower() not in WORKBOOK_SUFFIXES:
        return {
            "status": "error",
            "error": (
                f"verify_workbook checks .xlsx and .xlsm files; {path} is "
                f"'{Path(path).suffix or 'no extension'}'. Save the workbook "
                "as .xlsx (openpyxl's Workbook.save does) and check that file."
            ),
        }
    try:
        return check_workbook(Path(path).resolve())
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
