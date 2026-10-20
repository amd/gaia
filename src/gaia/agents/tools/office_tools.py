# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""OfficeToolsMixin — checks Office files the agent built, without changing them.

The agent builds workbooks with openpyxl through ``run_python``. openpyxl never
computes a formula, so a wrong reference goes unnoticed until a person opens
the file. ``verify_workbook`` recalculates a copy and reports what is broken.
"""

import os
import zipfile
from pathlib import Path
from typing import Any, Dict

from gaia.agents.tools.path_access import missing_file_error, read_access_error
from gaia.agents.tools.workbook_check import RECALC_TIMEOUT_S, check_workbook
from gaia.logger import get_logger

logger = get_logger(__name__)

#: Formats openpyxl opens; .xls and .ods need converting first.
WORKBOOK_SUFFIXES = (".xlsx", ".xlsm")


class OfficeToolsMixin:
    """
    Mixin providing Office-file checks.

    Tools provided:
    - verify_workbook: Recalculate an .xlsx and report formula errors (read-only)
    """

    def register_office_tools(self) -> None:
        """Register the Office-file tools into _TOOL_REGISTRY."""
        from gaia.agents.base.tools import tool

        @tool(timeout=RECALC_TIMEOUT_S + 60, display_label="Checking workbook")
        def verify_workbook(file_path: str) -> Dict[str, Any]:
            """Check every formula in an .xlsx workbook; run it after you build or change one.

            Recalculates a copy (the file itself is not changed) and reports
            #REF!, #VALUE!, #DIV/0!, #NAME?, circular references, SUM/SUMIFS
            over text or empty cells, criteria that match no row, and
            arithmetic on empty or text cells — each with the cell and
            formula. Also returns computed values, so report numbers from
            those. Fix every error, save, and check again.

            Args:
                file_path: Absolute path of the .xlsx or .xlsm file.
            """
            return self._verify_workbook(file_path)

    def _verify_workbook(self, file_path: str) -> Dict[str, Any]:
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
        denied = read_access_error(self, path)
        if denied:
            return denied
        if not os.path.isfile(path):
            return missing_file_error(path)
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
