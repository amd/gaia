# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Validation tools for Code Agent.

This module provides tools for testing and validating generated applications.
Uses curl-based testing to avoid temporary files and complex setup.
"""

import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any, Dict

from gaia.agents.base.tools import tool
from gaia.agents.code.prompts.code_patterns import pluralize
from gaia.agents.code.tools.cli_tools import is_port_available
from gaia.agents.code.tools.web_dev_tools import read_prisma_model

logger = logging.getLogger(__name__)


def generate_test_payload(fields: Dict[str, str]) -> Dict[str, Any]:
    """Generate test payload data from field definitions.

    Creates appropriate test values for each field type.
    This is used by test_crud_api to generate realistic test data
    based on the actual Prisma schema.

    Args:
        fields: Dictionary mapping field names to their types

    Returns:
        Dictionary with field names mapped to test values
    """
    payload = {}
    for field_name, field_type in fields.items():
        # Skip auto-generated fields
        if field_name.lower() in {"id", "createdat", "updatedat"}:
            continue

        # Generate appropriate test values based on type
        field_type_lower = field_type.lower()
        if field_type_lower in ("string", "text"):
            payload[field_name] = f"Test {field_name.replace('_', ' ').title()}"
        elif field_type_lower in ("int", "number", "integer"):
            payload[field_name] = 42
        elif field_type_lower == "float":
            payload[field_name] = 3.14
        elif field_type_lower == "boolean":
            # Default to false for most boolean fields, true for common patterns
            if field_name.lower() in ("active", "enabled", "visible"):
                payload[field_name] = True
            else:
                payload[field_name] = False
        elif field_type_lower in ("datetime", "date", "timestamp"):
            payload[field_name] = "2025-01-01T00:00:00.000Z"
        else:
            # Default to string for unknown types
            payload[field_name] = f"Test {field_name}"

    return payload


class ValidationToolsMixin:
    """Mixin providing validation and testing tools for the Code Agent."""

    def register_validation_tools(self) -> None:
        """Register validation tools with the agent."""

        @tool
        def test_crud_api(
            project_dir: str, model_name: str, port: int = 3000
        ) -> Dict[str, Any]:
            """Test CRUD API endpoints using curl.

            Validates that all CRUD operations work correctly by:
            - Creating a test record (POST)
            - Listing all records (GET list)
            - Getting single record (GET single)
            - Updating record (PATCH)
            - Deleting record (DELETE)

            Ensures dev server is running before testing.

            Args:
                project_dir: Path to the Next.js project directory
                model_name: Model name to test (e.g., "Todo", "Post")
                port: Port where dev server is running (default: 3000)

            Returns:
                {
                    "success": bool,
                    "result": {
                        "tests_passed": int,
                        "tests_failed": int,
                        "results": {
                            "POST": {"status": int, "pass": bool},
                            "GET_LIST": {"status": int, "pass": bool},
                            "GET_SINGLE": {"status": int, "pass": bool},
                            "PATCH": {"status": int, "pass": bool},
                            "DELETE": {"status": int, "pass": bool}
                        }
                    }
                }

                On error:
                {
                    "success": False,
                    "error": str,
                    "error_type": "endpoint_missing" | "validation_error" | "database_error" | "runtime_error"
                }
            """
            server_started_by_us = False
            server_pid = None

            try:
                logger.info(f"Testing CRUD API for {model_name}")

                # Check if dev server is running (port in use = server running)
                server_was_running = not is_port_available(port)

                if not server_was_running:
                    logger.info(f"Dev server not running on port {port}, starting...")

                    # Start dev server in background using CLI tools mixin
                    start_result = self._run_background_command(
                        command="npm run dev",
                        work_path=Path(project_dir),
                        startup_timeout=15,
                        expected_port=port,
                        auto_respond="",
                    )

                    if not start_result.get("success"):
                        return {
                            "success": False,
                            "error": "Failed to start dev server",
                            "details": start_result,
                            "error_type": "runtime_error",
                        }

                    server_started_by_us = True
                    server_pid = start_result.get("pid")
                    logger.info(f"Dev server started with PID {server_pid}")

                    # Give Next.js a moment to fully initialize
                    time.sleep(3)
                else:
                    logger.info(f"Dev server already running on port {port}")

                base_url = f"http://localhost:{port}"
                resource = model_name.lower()
                resource_plural = pluralize(resource)
                api_url = f"{base_url}/api/{resource_plural}"

                # Phase 2 Fix (Issue #885): Read schema to get actual fields
                # instead of using hardcoded {"name": "Test Item"}
                model_info = read_prisma_model(project_dir, model_name)
                if model_info["success"]:
                    # Convert Prisma types to our field types
                    prisma_to_field_type = {
                        "String": "string",
                        "Int": "number",
                        "Float": "float",
                        "Boolean": "boolean",
                        "DateTime": "datetime",
                    }
                    schema_fields = {}
                    for field_name, prisma_type in model_info["fields"].items():
                        if field_name.lower() not in {"id", "createdat", "updatedat"}:
                            schema_fields[field_name] = prisma_to_field_type.get(
                                prisma_type, "string"
                            )
                    test_payload = generate_test_payload(schema_fields)
                    logger.info(f"Generated test payload from schema: {test_payload}")
                else:
                    # Fallback: Use generic test payload but log warning
                    logger.warning(
                        f"Could not read Prisma schema for {model_name}, "
                        f"using generic test payload. Error: {model_info.get('error')}"
                    )
                    test_payload = {
                        "title": "Test Item",
                        "description": "Test description",
                    }

                # Escape the payload for shell
                test_payload_json = json.dumps(test_payload)

                results = {}
                created_id = None

                # Test 1: POST (create)
                logger.info("Testing POST (create)...")
                post_result = self._run_foreground_command(
                    command=(
                        f"curl -s -w '\\n%{{http_code}}' -X POST "
                        f"-H 'Content-Type: application/json' "
                        f"-d '{test_payload_json}' '{api_url}'"
                    ),
                    work_path=Path(project_dir) if project_dir else Path.cwd(),
                    timeout=10,
                    auto_respond="y\n",
                )

                if post_result.get("status") == "success":
                    output = post_result.get("stdout", "")
                    lines = output.strip().split("\n")
                    status_code = int(lines[-1]) if lines else 0
                    results["POST"] = {
                        "status": status_code,
                        "pass": status_code == 201,
                    }

                    # Extract created ID from response
                    if status_code == 201 and len(lines) > 1:
                        try:
                            response_data = json.loads(lines[0])
                            created_id = response_data.get("id")
                        except Exception:
                            logger.warning(
                                "Could not parse POST response to extract ID"
                            )
                else:
                    results["POST"] = {
                        "status": 0,
                        "pass": False,
                        "error": "Command failed",
                    }

                # Test 2: GET (list)
                logger.info("Testing GET (list)...")
                get_list_result = self._run_foreground_command(
                    command=f"curl -s -w '\\n%{{http_code}}' '{api_url}'",
                    work_path=Path(project_dir) if project_dir else Path.cwd(),
                    timeout=10,
                    auto_respond="y\n",
                )

                if get_list_result.get("status") == "success":
                    output = get_list_result.get("stdout", "")
                    lines = output.strip().split("\n")
                    status_code = int(lines[-1]) if lines else 0
                    results["GET_LIST"] = {
                        "status": status_code,
                        "pass": status_code == 200,
                    }
                else:
                    results["GET_LIST"] = {
                        "status": 0,
                        "pass": False,
                        "error": "Command failed",
                    }

                # Test 3: GET (single) - only if we have an ID
                if created_id:
                    logger.info(f"Testing GET (single) with ID {created_id}...")
                    get_single_result = self._run_foreground_command(
                        command=f"curl -s -w '\\n%{{http_code}}' '{api_url}/{created_id}'",
                        work_path=Path(project_dir) if project_dir else Path.cwd(),
                        timeout=10,
                        auto_respond="y\n",
                    )

                    if get_single_result.get("status") == "success":
                        output = get_single_result.get("stdout", "")
                        lines = output.strip().split("\n")
                        status_code = int(lines[-1]) if lines else 0
                        results["GET_SINGLE"] = {
                            "status": status_code,
                            "pass": status_code == 200,
                        }
                    else:
                        results["GET_SINGLE"] = {
                            "status": 0,
                            "pass": False,
                            "error": "Command failed",
                        }
                else:
                    results["GET_SINGLE"] = {
                        "status": 0,
                        "pass": False,
                        "error": "No ID to test",
                    }

                # Test 4: PATCH (update) - only if we have an ID
                if created_id:
                    logger.info(f"Testing PATCH (update) with ID {created_id}...")
                    # Generate update payload - modify the first string field
                    update_payload = {}
                    for key, value in test_payload.items():
                        if isinstance(value, str):
                            update_payload[key] = f"Updated {value}"
                            break
                    if not update_payload:
                        # Fallback: use first field with "Updated" prefix
                        first_key = next(iter(test_payload), None)
                        if first_key:
                            update_payload[first_key] = "Updated Value"
                    update_payload_json = json.dumps(update_payload)

                    patch_result = self._run_foreground_command(
                        command=(
                            f"curl -s -w '\\n%{{http_code}}' -X PATCH "
                            f"-H 'Content-Type: application/json' "
                            f"-d '{update_payload_json}' '{api_url}/{created_id}'"
                        ),
                        work_path=Path(project_dir) if project_dir else Path.cwd(),
                        timeout=10,
                        auto_respond="y\n",
                    )

                    if patch_result.get("status") == "success":
                        output = patch_result.get("stdout", "")
                        lines = output.strip().split("\n")
                        status_code = int(lines[-1]) if lines else 0
                        results["PATCH"] = {
                            "status": status_code,
                            "pass": status_code == 200,
                        }
                    else:
                        results["PATCH"] = {
                            "status": 0,
                            "pass": False,
                            "error": "Command failed",
                        }
                else:
                    results["PATCH"] = {
                        "status": 0,
                        "pass": False,
                        "error": "No ID to test",
                    }

                # Test 5: DELETE - only if we have an ID
                if created_id:
                    logger.info(f"Testing DELETE with ID {created_id}...")
                    delete_result = self._run_foreground_command(
                        command=f"curl -s -w '\\n%{{http_code}}' -X DELETE '{api_url}/{created_id}'",
                        work_path=Path(project_dir) if project_dir else Path.cwd(),
                        timeout=10,
                        auto_respond="y\n",
                    )

                    if delete_result.get("status") == "success":
                        output = delete_result.get("stdout", "")
                        lines = output.strip().split("\n")
                        status_code = int(lines[-1]) if lines else 0
                        results["DELETE"] = {
                            "status": status_code,
                            "pass": status_code == 200,
                        }
                    else:
                        results["DELETE"] = {
                            "status": 0,
                            "pass": False,
                            "error": "Command failed",
                        }
                else:
                    results["DELETE"] = {
                        "status": 0,
                        "pass": False,
                        "error": "No ID to test",
                    }

                # Calculate summary
                passed = sum(1 for r in results.values() if r.get("pass", False))
                failed = len(results) - passed

                logger.info(f"Tests completed: {passed} passed, {failed} failed")

                return {
                    "success": passed == len(results),
                    "result": {
                        "tests_passed": passed,
                        "tests_failed": failed,
                        "results": results,
                    },
                }

            except Exception as e:
                logger.error(f"Error in test_crud_api: {e}", exc_info=True)
                return {
                    "success": False,
                    "error": str(e),
                    "error_type": "runtime_error",
                }

            finally:
                # Clean up: stop dev server if we started it
                if server_started_by_us and server_pid:
                    logger.info(f"Stopping dev server (PID {server_pid})...")
                    try:
                        self._stop_process(server_pid, force=False)
                        logger.info("Dev server stopped successfully")
                    except Exception as cleanup_error:
                        logger.warning(f"Error stopping dev server: {cleanup_error}")

        @tool
        def validate_typescript(project_dir: str) -> Dict[str, Any]:
            """Validate TypeScript code before declaring success.

            Runs TypeScript compiler in no-emit mode to check for type errors
            without generating output files. This is the ultimate guardrail to
            catch import errors, missing types, and other TypeScript issues
            before they reach npm run build.

            TIER 4 ERROR MESSAGING: When validation fails, this tool returns
            specific rule citations to teach the LLM what went wrong.

            Args:
                project_dir: Path to the Next.js project directory

            Returns:
                On success:
                {
                    "success": True,
                    "message": "TypeScript validation passed"
                }

                On failure:
                {
                    "success": False,
                    "error": "TypeScript validation failed",
                    "errors": str,  # Full tsc error output
                    "rule": str,    # Which rule was violated
                    "violation": str,  # Specific violation
                    "fix": str,     # How to fix it
                    "hint": "Fix the type errors listed above, then run validate_typescript again"
                }
            """
            try:
                logger.debug(f"Validating TypeScript in {project_dir}")

                result = subprocess.run(
                    ["npx", "tsc", "--noEmit", "--skipLibCheck"],
                    cwd=project_dir,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )

                if result.returncode == 0:
                    logger.info("TypeScript validation passed")
                    return {
                        "success": True,
                        "message": "TypeScript validation passed - no type errors found",
                    }

                # Parse errors to provide specific guidance
                error_output = result.stderr if result.stderr else result.stdout
                logger.warning(f"TypeScript validation failed:\n{error_output}")

                # Detect common error patterns and provide rule citations
                rule = None
                violation = None
                fix = None

                if "Cannot find name" in error_output:
                    violation = "Missing type import"
                    rule = "Client components must use: import type { X } from '@prisma/client'"
                    fix = "Add the missing type import at the top of the file"
                elif "Cannot find module '@/lib/prisma'" in error_output:
                    violation = "Missing prisma singleton"
                    rule = "Server components must import: import { prisma } from '@/lib/prisma'"
                    fix = "Ensure src/lib/prisma.ts exists with the Prisma singleton"
                elif (
                    "Module '\"@prisma/client\"' has no exported member" in error_output
                ):
                    violation = "Prisma types not generated"
                    rule = "Run prisma generate after schema changes"
                    fix = "Run: npx prisma generate"
                elif (
                    "'prisma' is not defined" in error_output
                    or "Cannot find name 'prisma'" in error_output
                ):
                    violation = "Direct prisma import in client component"
                    rule = "NEVER import prisma client directly in client components"
                    fix = "Use API routes for database access from client components"

                response = {
                    "success": False,
                    "error": "TypeScript validation failed",
                    "errors": error_output,
                    "hint": "Fix the type errors listed above, then run validate_typescript again",
                }

                # Add Tier 4 teaching if we detected a specific violation
                if rule and violation and fix:
                    response.update({"violation": violation, "rule": rule, "fix": fix})

                return response

            except subprocess.TimeoutExpired:
                logger.error("TypeScript validation timed out")
                return {
                    "success": False,
                    "error": "TypeScript validation timed out after 60 seconds",
                    "hint": "Check for infinite type recursion or very large project",
                }
            except FileNotFoundError:
                logger.error("TypeScript compiler not found")
                return {
                    "success": False,
                    "error": "TypeScript compiler (tsc) not found",
                    "hint": "Ensure TypeScript is installed: npm install --save-dev typescript",
                }
            except Exception as e:
                logger.error(f"Error in validate_typescript: {e}", exc_info=True)
                return {
                    "success": False,
                    "error": f"Validation error: {str(e)}",
                    "hint": "Check that project_dir is valid and contains a Next.js project",
                }

        @tool
        def validate_crud_structure(
            project_dir: str, resource_name: str
        ) -> Dict[str, Any]:
            """Validate that all required CRUD files exist before declaring success.

            Checks for a complete CRUD application structure including:
            - List page, New page, Detail page
            - Form component, Actions component
            - API routes (collection and item)

            This should be called AFTER building a CRUD app to ensure nothing was skipped.

            Args:
                project_dir: Path to the Next.js project directory
                resource_name: Resource name in singular form (e.g., "todo", "post")

            Returns:
                Dictionary with:
                - success: bool
                - missing_files: list of missing file paths
                - message: summary of validation result
            """
            try:
                project_path = Path(project_dir)
                resource_plural = resource_name.lower() + "s"  # Simple pluralization
                resource_capitalized = resource_name.capitalize()

                # Define all required files for a complete CRUD app
                required_files = {
                    "List page": f"src/app/{resource_plural}/page.tsx",
                    "New page": f"src/app/{resource_plural}/new/page.tsx",
                    "Detail page": f"src/app/{resource_plural}/[id]/page.tsx",
                    "Form component": f"src/components/{resource_capitalized}Form.tsx",
                    "Actions component": f"src/components/{resource_capitalized}Actions.tsx",
                    "Collection API": f"src/app/api/{resource_plural}/route.ts",
                    "Item API": f"src/app/api/{resource_plural}/[id]/route.ts",
                }

                missing_files = []
                existing_files = []

                for description, file_path in required_files.items():
                    full_path = project_path / file_path
                    if full_path.exists():
                        existing_files.append(f"✅ {description}: {file_path}")
                    else:
                        missing_files.append(
                            {
                                "description": description,
                                "path": file_path,
                                "create_with": self._get_create_command(
                                    description, resource_name
                                ),
                            }
                        )

                if not missing_files:
                    logger.info(f"CRUD structure validation passed for {resource_name}")
                    return {
                        "success": True,
                        "message": f"Complete CRUD structure validated for {resource_name}",
                        "existing_files": existing_files,
                    }

                # Build detailed error message with fix instructions
                error_details = f"Missing {len(missing_files)} required file(s) for {resource_name} CRUD app:\n\n"
                for item in missing_files:
                    error_details += f"❌ {item['description']}: {item['path']}\n"
                    error_details += f"   Fix: {item['create_with']}\n\n"

                logger.warning(
                    f"CRUD structure validation failed: {len(missing_files)} files missing"
                )

                return {
                    "success": False,
                    "error": f"Incomplete CRUD structure: {len(missing_files)} file(s) missing",
                    "missing_files": missing_files,
                    "existing_files": existing_files,
                    "details": error_details,
                    "hint": "Create the missing files using the fix commands listed above",
                }

            except Exception as e:
                logger.error(f"Error in validate_crud_structure: {e}", exc_info=True)
                return {
                    "success": False,
                    "error": f"Validation error: {str(e)}",
                    "hint": "Check that project_dir is valid and resource_name is correct",
                }

        @tool
        def validate_styles(
            project_dir: str, _resource_name: str = None
        ) -> Dict[str, Any]:
            """Validate CSS files and design system consistency.

            This tool validates:
            1. CSS files contain valid CSS (not TypeScript/JavaScript) - CRITICAL
            2. globals.css has Tailwind directives
            3. layout.tsx imports globals.css
            4. (Optional) Custom classes used in components are defined

            Addresses Issue #1002: CSS file written with TypeScript code.

            Args:
                project_dir: Path to the Next.js project directory
                resource_name: Optional resource name for component class checks

            Returns:
                On success:
                {
                    "success": True,
                    "is_valid": True,
                    "message": "Styling validated successfully",
                    "files_checked": [list of files]
                }

                On failure:
                {
                    "success": False,
                    "is_valid": False,
                    "errors": [list of CRITICAL errors],
                    "warnings": [list of warnings],
                    "hint": "How to fix"
                }
            """
            import re

            try:
                project_path = Path(project_dir)
                errors = []
                warnings = []
                files_checked = []

                # 1. Check globals.css for TypeScript content (CRITICAL)
                globals_css = project_path / "src" / "app" / "globals.css"
                if globals_css.exists():
                    files_checked.append("src/app/globals.css")
                    content = globals_css.read_text()

                    # TypeScript/JavaScript detection patterns
                    typescript_indicators = [
                        (r"^\s*import\s+.*from", "import statement"),
                        (
                            r"^\s*export\s+(default|const|function|class|async)",
                            "export statement",
                        ),
                        (r'"use client"|\'use client\'', "React client directive"),
                        (r"^\s*interface\s+\w+", "TypeScript interface"),
                        (r"^\s*type\s+\w+\s*=", "TypeScript type alias"),
                        (r"^\s*const\s+\w+\s*[=:]", "const declaration"),
                        (r"^\s*let\s+\w+\s*[=:]", "let declaration"),
                        (r"^\s*function\s+\w+", "function declaration"),
                        (r"^\s*async\s+function", "async function"),
                        (r"<[A-Z][a-zA-Z]*[\s/>]", "JSX component tag"),
                        (r"useState|useEffect|useRouter|usePathname", "React hook"),
                    ]

                    for pattern, description in typescript_indicators:
                        if re.search(pattern, content, re.MULTILINE):
                            errors.append(
                                f"CRITICAL: globals.css contains {description}. "
                                f"This file has TypeScript/JSX code instead of CSS."
                            )

                    # Check for balanced braces
                    if content.count("{") != content.count("}"):
                        errors.append("globals.css has mismatched braces")

                    # Check for Tailwind directives
                    has_tailwind = (
                        "@tailwind" in content or '@import "tailwindcss' in content
                    )
                    if not has_tailwind and len(content.strip()) > 50:
                        warnings.append(
                            "globals.css is missing Tailwind directives "
                            "(@tailwind base/components/utilities)"
                        )
                else:
                    errors.append("globals.css not found at src/app/globals.css")

                # 2. Check layout.tsx imports globals.css
                layout_tsx = project_path / "src" / "app" / "layout.tsx"
                if layout_tsx.exists():
                    files_checked.append("src/app/layout.tsx")
                    layout_content = layout_tsx.read_text()

                    # Check for globals.css import
                    globals_import = (
                        './globals.css"' in layout_content
                        or "./globals.css'" in layout_content
                        or "@/app/globals.css" in layout_content
                    )
                    if not globals_import:
                        warnings.append(
                            "layout.tsx does not import globals.css. "
                            "Global styles may not be applied to pages."
                        )
                else:
                    warnings.append("layout.tsx not found at src/app/layout.tsx")

                # 3. Check all CSS files for TypeScript content
                for css_file in project_path.glob("**/*.css"):
                    if css_file == globals_css:
                        continue  # Already checked

                    files_checked.append(str(css_file.relative_to(project_path)))
                    css_content = css_file.read_text()

                    # Quick check for obvious TypeScript patterns
                    if re.search(r"^\s*import\s+", css_content, re.MULTILINE):
                        errors.append(
                            f"CRITICAL: {css_file.name} contains import statement. "
                            f"This is TypeScript, not CSS."
                        )
                    if re.search(r"^\s*export\s+", css_content, re.MULTILINE):
                        errors.append(
                            f"CRITICAL: {css_file.name} contains export statement. "
                            f"This is TypeScript, not CSS."
                        )

                # Build result
                is_valid = len(errors) == 0
                if is_valid:
                    logger.info("Styling validation passed")
                    result = {
                        "success": True,
                        "is_valid": True,
                        "message": "Styling validated successfully",
                        "files_checked": files_checked,
                    }
                    if warnings:
                        result["warnings"] = warnings
                    return result

                logger.warning(f"Styling validation failed: {errors}")
                return {
                    "success": False,
                    "is_valid": False,
                    "errors": errors,
                    "warnings": warnings,
                    "files_checked": files_checked,
                    "hint": (
                        "CRITICAL errors indicate CSS files contain TypeScript code. "
                        "Regenerate globals.css with valid CSS content including "
                        "Tailwind directives (@tailwind base/components/utilities)."
                    ),
                }

            except Exception as e:
                logger.error(f"Error in validate_styles: {e}", exc_info=True)
                return {
                    "success": False,
                    "is_valid": False,
                    "error": f"Validation error: {str(e)}",
                    "hint": "Check that project_dir is valid and contains a Next.js project",
                }

    def _get_create_command(self, description: str, resource_name: str) -> str:
        """Get the tool command needed to create a missing file.

        This is a helper method used by validate_crud_structure to provide
        fix instructions for missing CRUD files.

        Args:
            description: Human-readable description of the file type
            resource_name: Singular resource name (e.g., "todo", "post")

        Returns:
            Tool command string to create the missing file
        """
        if "Detail page" in description:
            return f'manage_react_component(variant="detail", resource_name="{resource_name}")'
        elif "Actions component" in description:
            return f'manage_react_component(variant="actions", resource_name="{resource_name}")'
        elif "List page" in description:
            return f'manage_react_component(variant="list", resource_name="{resource_name}")'
        elif "New page" in description:
            return f'manage_react_component(variant="new", resource_name="{resource_name}")'
        elif "Form component" in description:
            return f'manage_react_component(variant="form", resource_name="{resource_name}")'
        elif "API" in description:
            return f'manage_api_endpoint(resource_name="{resource_name}", operations=["GET", "POST", "PATCH", "DELETE"])'
        return "Unknown - check documentation"
