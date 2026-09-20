# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Hold every tool the flagship can offer to the description budget.

A tool's docstring is its schema, and the whole schema set is re-sent on every
model call — so an over-long docstring is paid for on every step of every turn,
for the life of the session. The budgets live in
``gaia.agents.base.tools`` (``MAX_TOOL_DESCRIPTION_CHARS``,
``MAX_TOOL_PARAM_DESCRIPTION_CHARS``); this check fails when a tool exceeds one,
naming the tool, its length, and the budget.

Run standalone or via ``python util/lint.py --tool-descriptions``. To see where
the characters actually go, run ``python util/tool_schema_tokens.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List


def find_violations(
    schemas: List[dict], max_description: int, max_param: int
) -> List[str]:
    """Return one message per over-budget description, in schema order."""
    violations: List[str] = []
    for schema in schemas:
        fn = schema["function"]
        name = fn["name"]
        description = fn.get("description", "")
        if len(description) > max_description:
            violations.append(
                f"{name}: description is {len(description)} chars "
                f"(budget {max_description})"
            )
        for param, spec in fn["parameters"]["properties"].items():
            param_description = spec.get("description", "")
            if len(param_description) > max_param:
                violations.append(
                    f"{name}({param}): description is {len(param_description)} "
                    f"chars (budget {max_param})"
                )
    return violations


def run_check() -> int:
    """Check the flagship's tool schemas. Returns 0 when all are in budget."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from tool_schema_tokens import flagship_tool_schemas, prepend_source_roots

    prepend_source_roots()
    from gaia.agents.base.tools import (
        MAX_TOOL_DESCRIPTION_CHARS,
        MAX_TOOL_PARAM_DESCRIPTION_CHARS,
    )

    schemas = flagship_tool_schemas()
    violations = find_violations(
        schemas, MAX_TOOL_DESCRIPTION_CHARS, MAX_TOOL_PARAM_DESCRIPTION_CHARS
    )

    if violations:
        print(f"[FAIL] {len(violations)} tool schema(s) over budget:")
        for violation in violations:
            print(f"   - {violation}")
        print(
            "\nTrim the docstring: keep what the tool does, when to use it "
            "instead of a neighbour, what it refuses, and what it returns. "
            "Rationale, history, and examples belong in a code comment."
        )
        return 1

    print(
        f"[OK] All {len(schemas)} flagship tool schemas within "
        f"{MAX_TOOL_DESCRIPTION_CHARS}/{MAX_TOOL_PARAM_DESCRIPTION_CHARS} chars."
    )
    return 0


if __name__ == "__main__":
    sys.exit(run_check())
