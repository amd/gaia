# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""TypeScript runtime tools for Code Agent."""

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse

from gaia.agents.base.tools import tool

logger = logging.getLogger(__name__)


class TypeScriptToolsMixin:
    """Mixin providing TypeScript runtime tools for the Code Agent.

    This mixin provides essential TypeScript development tools including:
    - Template fetching from GitHub repositories
    - Package manager operations (npm/yarn/pnpm)
    - TypeScript compilation validation
    - Dependency installation with type definitions
    """

    def register_typescript_tools(self) -> None:
        """Register TypeScript runtime tools with the agent."""

        @tool
        def fetch_template(template_name: str, destination: str) -> Dict[str, Any]:
            """Fetch a template from local templates directory.

            Args:
                template_name: Local template name (e.g., 'react-vite-ts', 'express-mongodb-ts')
                destination: Local destination path for the template

            Returns:
                Dictionary with success status, destination path, and template info
            """
            try:
                dest_path = Path(destination)

                # Get the local templates directory
                templates_dir = Path(__file__).parent.parent / "templates"
                template_path = templates_dir / template_name

                # Check if local template exists
                if not template_path.exists():
                    return {
                        "success": False,
                        "error": f"Template '{template_name}' not found in local templates directory: {templates_dir}",
                    }

                logger.info(f"Using local template: {template_path}")

                # Copy template to destination
                if dest_path.exists():
                    # If destination exists, copy files into it
                    shutil.copytree(template_path, dest_path, dirs_exist_ok=True)
                else:
                    # Otherwise create new directory
                    shutil.copytree(template_path, dest_path)

                # If template has .env.example, copy it to .env
                env_example = dest_path / ".env.example"
                env_file = dest_path / ".env"
                if env_example.exists() and not env_file.exists():
                    shutil.copy2(env_example, env_file)
                    logger.info("Created .env from .env.example")

                logger.info(f"Template copied successfully to: {dest_path}")

                return {
                    "success": True,
                    "destination": str(dest_path),
                    "template_name": template_name,
                    "template_path": str(template_path),
                }

            except Exception as e:
                logger.error(f"Error fetching template: {e}")
                return {"success": False, "error": str(e)}

        @tool
        def fetch_github_template(
            template_url: str, destination: str
        ) -> Dict[str, Any]:
            """Fetch a GitHub template and cache it for reuse.

            Args:
                template_url: GitHub repository URL or tree URL
                destination: Local destination path for the template

            Returns:
                Dictionary with success status, destination path, and cache info
            """
            try:
                dest_path = Path(destination)
                cache_dir = self.cache_dir / "templates"
                cache_dir.mkdir(parents=True, exist_ok=True)

                # Extract repo info from URL
                parsed = urlparse(template_url)
                path_parts = parsed.path.strip("/").split("/")

                if len(path_parts) < 2:
                    return {
                        "success": False,
                        "error": f"Invalid GitHub URL: {template_url}",
                    }

                owner, repo = path_parts[0], path_parts[1]

                # Create cache key based on URL
                cache_key = f"{owner}_{repo}".replace("/", "_")
                cached_template = cache_dir / cache_key

                # Check if template is cached
                if cached_template.exists():
                    logger.info(f"Using cached template: {cached_template}")
                    shutil.copytree(cached_template, dest_path, dirs_exist_ok=True)
                    return {
                        "success": True,
                        "destination": str(dest_path),
                        "cached": True,
                        "cache_path": str(cached_template),
                    }

                # Clone the repository
                logger.info(f"Fetching template from {template_url}")

                # Use git clone with depth=1 for faster fetching
                clone_url = f"https://github.com/{owner}/{repo}.git"
                temp_clone = cache_dir / f"temp_{cache_key}"

                if temp_clone.exists():
                    shutil.rmtree(temp_clone)

                result = subprocess.run(
                    ["git", "clone", "--depth", "1", clone_url, str(temp_clone)],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    check=False,
                )

                if result.returncode != 0:
                    return {
                        "success": False,
                        "error": f"Git clone failed: {result.stderr}",
                    }

                # If URL points to specific subdirectory, extract it
                if len(path_parts) > 4 and path_parts[2] == "tree":
                    subdir = "/".join(path_parts[4:])
                    source_dir = temp_clone / subdir
                else:
                    source_dir = temp_clone

                if not source_dir.exists():
                    return {
                        "success": False,
                        "error": f"Source directory not found: {source_dir}",
                    }

                # Copy to cache
                shutil.copytree(source_dir, cached_template, dirs_exist_ok=True)

                # Remove .git directory from cache
                git_dir = cached_template / ".git"
                if git_dir.exists():
                    shutil.rmtree(git_dir)

                # Clean up temp clone
                shutil.rmtree(temp_clone)

                # Copy to destination
                shutil.copytree(cached_template, dest_path, dirs_exist_ok=True)

                logger.info(f"Template fetched and cached successfully: {dest_path}")

                return {
                    "success": True,
                    "destination": str(dest_path),
                    "cached": False,
                    "cache_path": str(cached_template),
                }

            except subprocess.TimeoutExpired:
                return {
                    "success": False,
                    "error": "Git clone timed out after 5 minutes",
                }
            except Exception as e:
                logger.error(f"Error fetching template: {e}")
                return {"success": False, "error": str(e)}

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
