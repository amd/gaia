# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""TypeScript runtime tools for Code Agent."""

import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict

from gaia.agents.base.tools import tool

logger = logging.getLogger(__name__)


class TypeScriptToolsMixin:
    """Mixin providing TypeScript runtime tools for the Code Agent.

    This mixin provides essential TypeScript development tools including:
    - Package manager operations (npm/yarn/pnpm)
    - TypeScript compilation validation
    - Dependency installation with type definitions
    """

    def register_typescript_tools(self) -> None:
        """Register TypeScript runtime tools with the agent."""

        @tool
        def validate_typescript(project_path: str) -> Dict[str, Any]:
            """Validate TypeScript code compilation and linting.

            Args:
                project_path: Path to the TypeScript project

            Returns:
                Dictionary with validation results and any errors found
            """
            try:
                proj_path = Path(project_path)

                if not proj_path.exists():
                    return {
                        "success": False,
                        "error": f"Project path does not exist: {project_path}",
                    }

                # Check for tsconfig.json
                tsconfig = proj_path / "tsconfig.json"
                if not tsconfig.exists():
                    return {
                        "success": False,
                        "error": "tsconfig.json not found in project",
                    }

                results = {
                    "success": True,
                    "typescript_valid": True,
                    "typescript_errors": [],
                    "eslint_valid": True,
                    "eslint_errors": [],
                }

                # Run TypeScript compiler check
                logger.info(f"Running TypeScript validation in {project_path}")

                tsc_result = subprocess.run(
                    ["npx", "tsc", "--noEmit"],
                    cwd=project_path,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )

                if tsc_result.returncode != 0:
                    results["typescript_valid"] = False
                    results["typescript_errors"] = tsc_result.stdout.split("\n")
                    logger.warning(f"TypeScript validation failed: {tsc_result.stdout}")

                # Run ESLint check (if .eslintrc or eslint config exists)
                eslint_configs = [
                    ".eslintrc",
                    ".eslintrc.js",
                    ".eslintrc.json",
                    "eslint.config.js",
                ]

                has_eslint_config = any(
                    (proj_path / config).exists() for config in eslint_configs
                )

                if has_eslint_config:
                    logger.info("Running ESLint validation")

                    eslint_result = subprocess.run(
                        ["npx", "eslint", "src/**/*.{ts,tsx}", "--format=json"],
                        cwd=project_path,
                        capture_output=True,
                        text=True,
                        timeout=60,
                        check=False,
                    )

                    if eslint_result.returncode != 0:
                        try:
                            eslint_output = json.loads(eslint_result.stdout)
                            results["eslint_valid"] = False
                            results["eslint_errors"] = eslint_output
                        except json.JSONDecodeError:
                            results["eslint_errors"] = [eslint_result.stdout]

                results["success"] = (
                    results["typescript_valid"] and results["eslint_valid"]
                )

                return results

            except subprocess.TimeoutExpired:
                return {"success": False, "error": "Validation timed out"}
            except Exception as e:
                logger.error(f"Error validating TypeScript: {e}")
                return {"success": False, "error": str(e)}
