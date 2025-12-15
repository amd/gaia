# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Prisma database tools for Code Agent.

This mixin provides tools for managing Prisma database setup and operations,
enforcing the correct workflow to prevent common errors.
"""

import logging
import subprocess
from pathlib import Path
from typing import Any, Dict

from gaia.agents.base.tools import tool

logger = logging.getLogger(__name__)

# Prisma singleton template for Next.js (prevents connection pool exhaustion)
PRISMA_SINGLETON_TEMPLATE = """// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
import { PrismaClient } from "@prisma/client";

const globalForPrisma = globalThis as unknown as {
  prisma: PrismaClient | undefined;
};

export const prisma = globalForPrisma.prisma ?? new PrismaClient();

if (process.env.NODE_ENV !== "production") {
  globalForPrisma.prisma = prisma;
}
"""


class PrismaToolsMixin:
    """Mixin providing Prisma database management tools for the Code Agent."""

    def register_prisma_tools(self) -> None:
        """Register Prisma database tools with the agent."""

        @tool
        def setup_prisma(
            project_dir: str,
            regenerate: bool = True,
            push_db: bool = True,
        ) -> Dict[str, Any]:
            """Set up or update Prisma client after schema changes.

            This tool enforces the correct Prisma workflow:
            1. Generates Prisma Client (TypeScript types)
            2. Pushes schema to database
            3. Verifies the singleton file exists
            4. Returns the correct import paths to use

            Call this tool:
            - After creating or modifying prisma/schema.prisma
            - When you see "Cannot find name 'Todo'" or similar type errors
            - When you see "Module '@prisma/client' has no exported member" errors

            Args:
                project_dir: Path to the Next.js project directory
                regenerate: Whether to run prisma generate (default: True)
                push_db: Whether to run prisma db push (default: True)

            Returns:
                Dictionary with:
                - success: bool
                - generated: bool (whether prisma generate ran)
                - pushed: bool (whether prisma db push ran)
                - singleton_path: str (path to singleton file)
                - import_patterns: dict (correct import statements to use)
                - output: str (command output)
                - error: str (if failed)
            """
            try:
                project_path = Path(project_dir).resolve()
                if not project_path.exists():
                    return {
                        "success": False,
                        "error": f"Project directory does not exist: {project_dir}",
                        "error_type": "validation_error",
                    }

                schema_path = project_path / "prisma" / "schema.prisma"
                if not schema_path.exists():
                    return {
                        "success": False,
                        "error": f"Prisma schema not found at {schema_path}. Run 'npx prisma init' first.",
                        "error_type": "schema_error",
                    }

                output_lines = []
                generated = False
                pushed = False

                # Step 1: Generate Prisma Client
                if regenerate:
                    logger.info(f"Running prisma generate in {project_dir}")
                    result = subprocess.run(
                        ["npx", "prisma", "generate"],
                        cwd=str(project_path),
                        capture_output=True,
                        text=True,
                        timeout=120,
                        check=False,
                    )
                    output_lines.append("=== prisma generate ===")
                    output_lines.append(result.stdout)
                    if result.stderr:
                        output_lines.append(result.stderr)

                    if result.returncode != 0:
                        return {
                            "success": False,
                            "generated": False,
                            "error": f"prisma generate failed: {result.stderr}",
                            "error_type": "client_error",
                            "output": "\n".join(output_lines),
                        }
                    generated = True

                # Step 2: Push schema to database
                if push_db:
                    logger.info(f"Running prisma db push in {project_dir}")
                    result = subprocess.run(
                        ["npx", "prisma", "db", "push"],
                        cwd=str(project_path),
                        capture_output=True,
                        text=True,
                        timeout=120,
                        check=False,
                    )
                    output_lines.append("\n=== prisma db push ===")
                    output_lines.append(result.stdout)
                    if result.stderr:
                        output_lines.append(result.stderr)

                    if result.returncode != 0:
                        return {
                            "success": False,
                            "generated": generated,
                            "pushed": False,
                            "error": f"prisma db push failed: {result.stderr}",
                            "error_type": "migration_error",
                            "output": "\n".join(output_lines),
                        }
                    pushed = True

                # Step 3: Create singleton file if it doesn't exist
                singleton_path = project_path / "src" / "lib" / "prisma.ts"
                singleton_created = False

                if not singleton_path.exists():
                    singleton_path.parent.mkdir(parents=True, exist_ok=True)
                    singleton_path.write_text(
                        PRISMA_SINGLETON_TEMPLATE, encoding="utf-8"
                    )
                    singleton_created = True
                    output_lines.append(
                        f"\nCreated Prisma singleton at {singleton_path}"
                    )
                    logger.info(f"Created Prisma singleton at {singleton_path}")

                return {
                    "success": True,
                    "generated": generated,
                    "pushed": pushed,
                    "singleton_created": singleton_created,
                    "singleton_path": str(singleton_path.relative_to(project_path)),
                    "import_patterns": {
                        "client_instance": "import { prisma } from '@/lib/prisma'",
                        "model_types": "import { Todo, User } from '@prisma/client'",
                        "prisma_namespace": "import { Prisma } from '@prisma/client'",
                    },
                    "output": "\n".join(output_lines),
                }

            except subprocess.TimeoutExpired:
                return {
                    "success": False,
                    "error": "Prisma command timed out (exceeded 120 seconds)",
                    "error_type": "runtime_error",
                }
            except Exception as e:
                logger.error(f"Error in setup_prisma: {e}", exc_info=True)
                return {
                    "success": False,
                    "error": str(e),
                    "error_type": "runtime_error",
                }
