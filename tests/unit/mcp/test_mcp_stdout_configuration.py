"""Regression tests for Windows MCP test-module stdout handling."""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

MCP_TEST_MODULES = (
    "tests/mcp/test_mcp_simple.py",
    "tests/mcp/test_mcp_integration.py",
    "tests/mcp/test_mcp_http_validation.py",
)


def _module_tree(relative_path: str) -> ast.Module:
    path = REPO_ROOT / relative_path
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_mcp_modules_do_not_replace_stdout_during_import():
    """Import-time stdout replacement must not break pytest's capture stream."""
    for relative_path in MCP_TEST_MODULES:
        tree = _module_tree(relative_path)
        for node in tree.body:
            for child in ast.walk(node):
                if isinstance(child, ast.Assign):
                    assert not any(
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "sys"
                        and target.attr == "stdout"
                        for target in child.targets
                    ), f"{relative_path} replaces sys.stdout during import"
                if isinstance(child, ast.AnnAssign):
                    target = child.target
                    assert not (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "sys"
                        and target.attr == "stdout"
                    ), f"{relative_path} replaces sys.stdout during import"


def test_mcp_modules_keep_windows_encoding_setup_in_main_guard():
    """Direct-script encoding setup should remain outside pytest collection."""
    for relative_path in MCP_TEST_MODULES:
        tree = _module_tree(relative_path)
        main_guards = [
            node
            for node in tree.body
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "__name__"
        ]
        assert main_guards, f"{relative_path} has no __main__ guard"
        assert any(
            any(
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "_configure_windows_stdout"
                for call in ast.walk(guard)
            )
            for guard in main_guards
        ), f"{relative_path} configures Windows stdout outside __main__"


def test_importing_mcp_modules_as_windows_keeps_the_same_stdout(monkeypatch):
    """Behavioural check: catches setattr() or helper calls the AST scan misses."""
    monkeypatch.setattr(sys, "platform", "win32")
    for relative_path in MCP_TEST_MODULES:
        original = sys.stdout
        monkeypatch.setattr(sys, "stdout", original)
        path = REPO_ROOT / relative_path
        spec = importlib.util.spec_from_file_location(
            f"_stdout_probe_{path.stem}", path
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert sys.stdout is original, f"{relative_path} replaces sys.stdout on import"
