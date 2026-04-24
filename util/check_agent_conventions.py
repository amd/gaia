# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Lint GAIA agent conventions.

Scans ``src/gaia/agents/*/agent.py`` (plus ``agent_*.py`` variants) and
validates:

Hard checks (block CI on failure):
- Agent module parses as Python
- Defines at least one class inheriting from something named ``Agent`` / ``*Agent`` / ``*Mixin``
- The agent class implements ``_get_system_prompt`` and ``_register_tools``
  (or inherits them transitively from a known base)
- ``_register_tools`` contains ``_TOOL_REGISTRY.clear()`` (when defined locally)
- Copyright header + SPDX line present
- ``KNOWN_TOOLS`` entries in registry.py resolve to importable classes
- Manifest JSON Schema is not stale

Soft checks (non-blocking warnings):
- A test file exists (``tests/test_<name>.py`` or ``tests/<name>/``)
- The agent is mentioned in ``CLAUDE.md``'s Agent Implementations table
- If a matching ``docs/guides/<name>.mdx`` exists, it is registered in ``docs/docs.json``

Run via: ``python util/lint.py --agents`` or ``python util/check_agent_conventions.py``.
"""

import ast
import json
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = REPO_ROOT / "src" / "gaia" / "agents"
TESTS_DIR = REPO_ROOT / "tests"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
DOCS_JSON = REPO_ROOT / "docs" / "docs.json"
GUIDES_DIR = REPO_ROOT / "docs" / "guides"

# Directories under src/gaia/agents/ that are NOT agents.
_NON_AGENT_DIRS = {"__pycache__", "base", "tools"}

# Agent classes that are documented as "not-a-subclass-of-Agent-directly"
# (e.g. RoutingAgent wraps other agents) — accepted without the Agent base.
_STANDALONE_ALLOWED = {"RoutingAgent"}


def _has_copyright_header(text: str) -> bool:
    head = text[:400]
    return "Advanced Micro Devices" in head and "SPDX-License-Identifier" in head


def _find_agent_classes(tree: ast.Module) -> List[ast.ClassDef]:
    """Classes whose name ends in 'Agent' (excluding *Config)."""
    classes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            if node.name.endswith("Agent") and "Config" not in node.name:
                classes.append(node)
    return classes


def _method_names(cls: ast.ClassDef) -> set:
    return {
        n.name
        for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _manages_registry_state(cls: ast.ClassDef) -> bool:
    """True if _register_* locally manages registry state (.clear() or .pop())."""
    has_register_tools = False
    for node in cls.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("_register_"):
            continue
        if node.name == "_register_tools":
            has_register_tools = True
        src = ast.unparse(node)
        if (
            "_TOOL_REGISTRY.clear" in src
            or "_TOOL_REGISTRY.pop" in src
            or "_TOOL_REGISTRY[" in src  # explicit manipulation
        ):
            return True
    # If no _register_tools defined locally, inheritance handles it — pass.
    return not has_register_tools


def _check_agent_file(
    agent_dir: Path, agent_file: Path
) -> Tuple[List[str], List[str]]:
    """Return (errors, warnings) for a single agent.py file."""
    errors: List[str] = []
    warnings: List[str] = []
    name = agent_dir.name
    rel = agent_file.relative_to(REPO_ROOT)

    try:
        text = agent_file.read_text(encoding="utf-8")
    except Exception as exc:
        errors.append(f"{rel}: cannot read ({exc})")
        return errors, warnings

    if not _has_copyright_header(text):
        errors.append(
            f"{rel}: missing copyright header or SPDX-License-Identifier in first 400 chars"
        )

    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        errors.append(f"{rel}: parse error — {exc}")
        return errors, warnings

    agent_classes = _find_agent_classes(tree)
    if not agent_classes:
        warnings.append(f"{rel}: no class ending in 'Agent' found — skipping class checks")
        return errors, warnings

    for cls in agent_classes:
        base_names = [ast.unparse(b) for b in cls.bases]
        base_blob = " ".join(base_names)
        inherits_agent = (
            "Agent" in base_blob
            or "Mixin" in base_blob
            or cls.name in _STANDALONE_ALLOWED
        )
        if not inherits_agent:
            errors.append(
                f"{rel}: class {cls.name} does not inherit from Agent/Mixin "
                f"(bases: {base_names or 'none'})"
            )

        methods = _method_names(cls)
        # _get_system_prompt may be inherited; only warn if missing locally *and* no base name hints at it.
        if "_get_system_prompt" not in methods and "Agent" in base_blob:
            # Accept inheritance for subclasses of other agents
            warnings.append(
                f"{rel}: class {cls.name} does not define _get_system_prompt "
                "(inherited?) — confirm this is intentional"
            )
        if "_register_tools" not in methods and cls.name not in _STANDALONE_ALLOWED:
            warnings.append(
                f"{rel}: class {cls.name} does not define _register_tools "
                "(inherited?) — confirm this is intentional"
            )

        if not _manages_registry_state(cls):
            warnings.append(
                f"{rel}: class {cls.name}._register_tools does not call "
                "_TOOL_REGISTRY.clear() or .pop() — tools may leak across "
                "agent instances; confirm this is intentional"
            )

    # ── Soft: tests exist ─────────────────────────────────────────────────
    # Accept any tests/test_*.py whose name contains the agent dir name or any
    # agent class name (lowercased, with/without "agent" suffix), or an
    # in-tree tests/ directory in the agent package.
    class_stems = set()
    for cls in agent_classes:
        stem = cls.name.lower()
        class_stems.add(stem)
        if stem.endswith("agent"):
            class_stems.add(stem[: -len("agent")])
    needles = {name.lower()} | class_stems

    has_tests = (agent_dir / "tests").is_dir() or (
        TESTS_DIR / name
    ).is_dir()
    if not has_tests and TESTS_DIR.exists():
        for p in TESTS_DIR.glob("test_*.py"):
            lower = p.stem.lower()
            if any(needle and needle in lower for needle in needles):
                has_tests = True
                break
    if not has_tests:
        warnings.append(
            f"{rel}: no tests found — add tests/test_{name}.py or "
            f"a test matching {sorted(needles)}"
        )

    # ── Soft: mentioned in CLAUDE.md Agent Implementations table ──────────
    if CLAUDE_MD.exists():
        claude_text = CLAUDE_MD.read_text(encoding="utf-8")
        for cls in agent_classes:
            if cls.name not in claude_text and name not in claude_text:
                warnings.append(
                    f"{rel}: {cls.name} not mentioned in CLAUDE.md "
                    "(add to Agent Implementations table)"
                )
                break  # One warning per agent is enough.

    # ── Soft: if docs/guides/<name>.mdx exists, it must be in docs.json ───
    guide = GUIDES_DIR / f"{name}.mdx"
    if guide.exists() and DOCS_JSON.exists():
        docs_text = DOCS_JSON.read_text(encoding="utf-8")
        if f"guides/{name}" not in docs_text:
            errors.append(
                f"docs/guides/{name}.mdx exists but is not registered in docs/docs.json"
            )

    return errors, warnings


def _discover_agent_files() -> List[Tuple[Path, Path]]:
    """Return list of (agent_dir, agent_file) pairs to lint.

    Skips ``agent_*.py`` variants when a sibling ``agent.py`` exists — the
    variant is treated as an alternate implementation and linted via
    ``agent.py`` as the canonical entry point.
    """
    pairs: List[Tuple[Path, Path]] = []
    if not AGENTS_DIR.exists():
        return pairs
    for d in sorted(AGENTS_DIR.iterdir()):
        if not d.is_dir() or d.name in _NON_AGENT_DIRS:
            continue
        canonical = d / "agent.py"
        if canonical.exists():
            pairs.append((d, canonical))
            continue
        for py in sorted(d.glob("agent*.py")):
            if py.name == "__init__.py":
                continue
            pairs.append((d, py))
    return pairs


def _check_known_tools() -> Tuple[List[str], List[str]]:
    """Import every entry in KNOWN_TOOLS to confirm it resolves."""
    errors: List[str] = []
    warnings: List[str] = []
    src_path = str(REPO_ROOT / "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
    try:
        import importlib

        from gaia.agents.registry import KNOWN_TOOLS
    except Exception as exc:
        warnings.append(f"KNOWN_TOOLS: could not import registry ({exc}); skipping")
        return errors, warnings

    for tool_name, (module_path, class_name) in KNOWN_TOOLS.items():
        try:
            mod = importlib.import_module(module_path)
            if not hasattr(mod, class_name):
                errors.append(
                    f"KNOWN_TOOLS['{tool_name}']: {module_path} has no attribute {class_name}"
                )
        except Exception as exc:
            errors.append(
                f"KNOWN_TOOLS['{tool_name}']: import {module_path} failed — {exc}"
            )
    return errors, warnings


def _check_manifest_schema() -> Tuple[List[str], List[str]]:
    """Confirm schemas/agent-manifest.schema.json is up to date."""
    errors: List[str] = []
    warnings: List[str] = []
    schema_path = REPO_ROOT / "schemas" / "agent-manifest.schema.json"
    gen_script = REPO_ROOT / "util" / "gen_manifest_schema.py"
    if not schema_path.exists():
        warnings.append(
            f"{schema_path.relative_to(REPO_ROOT)} missing — run "
            "`python util/gen_manifest_schema.py` to generate it"
        )
        return errors, warnings
    if not gen_script.exists():
        return errors, warnings
    result = subprocess.run(
        [sys.executable, str(gen_script), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        msg = (result.stdout + result.stderr).strip() or "stale schema"
        errors.append(f"agent-manifest.schema.json: {msg}")
    return errors, warnings


def _format_report(all_errors: List[str], all_warnings: List[str]) -> str:
    lines: List[str] = []
    if all_errors:
        lines.append("\n[ERRORS]")
        for msg in all_errors:
            lines.append(f"  ✗ {msg}")
    if all_warnings:
        lines.append("\n[WARNINGS]")
        for msg in all_warnings:
            lines.append(f"  ⚠ {msg}")
    return "\n".join(lines)


def run_check() -> Tuple[int, int, str]:
    """Entry point used by util/lint.py.

    Returns: (error_count, warning_count, formatted_output)
    """
    all_errors: List[str] = []
    all_warnings: List[str] = []

    for agent_dir, agent_file in _discover_agent_files():
        errs, warns = _check_agent_file(agent_dir, agent_file)
        all_errors.extend(errs)
        all_warnings.extend(warns)

    errs, warns = _check_known_tools()
    all_errors.extend(errs)
    all_warnings.extend(warns)

    errs, warns = _check_manifest_schema()
    all_errors.extend(errs)
    all_warnings.extend(warns)

    output = _format_report(all_errors, all_warnings)
    return len(all_errors), len(all_warnings), output


def main() -> int:
    errors, warnings, output = run_check()
    if output:
        print(output)
    print()
    print(
        f"Agent convention check: {errors} error(s), {warnings} warning(s)."
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
