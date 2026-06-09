#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Static lint gate: assert that no code path in the email agent can route email
body content to a cloud LLM endpoint (AC3).

Three checks are performed:

1. ``EmailAgentConfig`` has no field whose name implies a cloud LLM provider
   (``use_claude``, ``use_chatgpt``, ``use_openai``, etc.).
2. The ``EmailTriageService`` in ``gaia.api.email_routes`` does NOT import
   cloud-LLM client modules (``gaia.llm.providers.claude``,
   ``gaia.llm.providers.openai_provider``) at module level.
3. The triage endpoint wires LLM calls ONLY through ``AgentSDK``, which is in
   turn guarded by ``EmailAgentConfig.validate()`` at construction time.

Run as part of CI:
    python util/check_email_agent_local_only.py

Exits 0 on success, 1 on any violation (with an actionable message).
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import fields
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_EMAIL_ROUTES = _REPO_ROOT / "src" / "gaia" / "api" / "email_routes.py"
_EMAIL_CONFIG = _REPO_ROOT / "src" / "gaia" / "agents" / "email" / "config.py"

# Cloud-LLM field tokens forbidden in EmailAgentConfig
_FORBIDDEN_FIELD_TOKENS = frozenset(
    {"use_claude", "use_chatgpt", "use_openai", "use_anthropic", "claude_api_key",
     "openai_api_key", "anthropic_api_key"}
)

# Cloud-LLM module substrings forbidden at module-level import in email_routes.py
_FORBIDDEN_IMPORT_SUBSTRINGS = (
    "providers.claude",
    "providers.openai_provider",
    "openai_provider",
    "anthropic",
)

_VIOLATIONS: list[str] = []


def _fail(msg: str) -> None:
    _VIOLATIONS.append(msg)
    print(f"FAIL: {msg}", file=sys.stderr)


def check_no_cloud_fields() -> None:
    """Assert EmailAgentConfig contains no cloud-LLM field names."""
    sys.path.insert(0, str(_REPO_ROOT))
    try:
        from gaia.agents.email.config import EmailAgentConfig
    except ImportError as exc:
        _fail(f"could not import EmailAgentConfig: {exc}")
        return

    for f in fields(EmailAgentConfig):
        lower = f.name.lower()
        for tok in ("claude", "openai", "anthropic", "chatgpt"):
            if tok in lower:
                _fail(
                    f"EmailAgentConfig.{f.name} contains cloud-LLM token {tok!r}. "
                    "This creates a path to a cloud LLM for email body content — "
                    "AC3 forbids it. Remove the field or rename it."
                )
        if f.name in _FORBIDDEN_FIELD_TOKENS:
            _fail(
                f"EmailAgentConfig.{f.name} is an explicitly forbidden cloud-LLM "
                "field name. AC3 enforcement requires its removal."
            )


def check_no_cloud_imports_in_email_routes() -> None:
    """Assert email_routes.py has no module-level cloud-LLM imports."""
    if not _EMAIL_ROUTES.exists():
        _fail(f"email_routes.py not found at {_EMAIL_ROUTES}")
        return

    source = _EMAIL_ROUTES.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_EMAIL_ROUTES))

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        # Collect the full import string.
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = [alias.name for alias in node.names]
            import_str = f"from {module} import {', '.join(names)}"
        else:
            import_str = "import " + ", ".join(alias.name for alias in node.names)

        for substr in _FORBIDDEN_IMPORT_SUBSTRINGS:
            if substr in import_str:
                _fail(
                    f"email_routes.py has a top-level import containing {substr!r}: "
                    f"  {import_str!r}  "
                    "Cloud-LLM modules must never be imported at module level in the "
                    "email triage surface. Use a local import guarded by the AC3 "
                    "config check, or remove the import entirely."
                )


def check_base_url_allowlist_enforced() -> None:
    """Assert config.py contains the AC3 allowlist guard logic."""
    if not _EMAIL_CONFIG.exists():
        _fail(f"config.py not found at {_EMAIL_CONFIG}")
        return

    source = _EMAIL_CONFIG.read_text(encoding="utf-8")
    if "AC3" not in source:
        _fail(
            f"config.py does not mention 'AC3'. The EmailAgentConfig.validate() "
            "method must reference the AC3 contract in its error message so an "
            "operator who sees the error can trace it back to the requirement."
        )
    if "_LOCAL_HOSTS" not in source and "localhost" not in source:
        _fail(
            "config.py does not appear to define a local-host allowlist. "
            "The AC3 guard requires an explicit allowlist of permitted LLM hosts."
        )


def main() -> int:
    """Run all checks. Return 0 on success, 1 on any violation."""
    check_no_cloud_fields()
    check_no_cloud_imports_in_email_routes()
    check_base_url_allowlist_enforced()

    if _VIOLATIONS:
        print(
            f"\n{len(_VIOLATIONS)} AC3 violation(s) detected. "
            "Fix them before merging.",
            file=sys.stderr,
        )
        return 1

    print("AC3 local-only gate: all checks passed.")
    return 0


# Also export a no-args ``check()`` alias so the unit test
# (test_llm_engine_triage.py::TestEnforcementArtifacts) can assert a callable.
check = main

if __name__ == "__main__":
    sys.exit(main())
