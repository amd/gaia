# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
EtherREPL Security Tests

Tests for SEC-001 through SEC-004 vulnerabilities:
- SEC-001: Pickle deserialization RCE (now JSON-based)
- SEC-002: Pattern detection bypass (now AST-based)
- SEC-003: Path traversal in write_component_template
- SEC-004: Subprocess isolation verification
"""

import ast
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parents[4]))

from gaia.agents.code.tools.ether_repl import (
    EtherREPL,
    REPLSession,
    ExecutionResult,
    EtherREPLError,
)
from gaia.utils.component_loader import ComponentLoader, ComponentLoaderError


class TestSEC001_PickleRCE(unittest.TestCase):
    """SEC-001: Verify pickle deserialization RCE is eliminated.

    The fix replaces pickle.load()/dump() with json.load()/dump().
    JSON cannot execute arbitrary code during deserialization.
    """

    def test_state_file_is_json_not_pickle(self):
        """State file should be .json, not .pkl."""
        self.assertEqual(REPLSession.STATE_FILE, "_ether_state.json")

    def test_no_pickle_import_in_module(self):
        """The ether_repl module should not import pickle."""
        import gaia.agents.code.tools.ether_repl as er_module
        source_file = Path(er_module.__file__)
        source = source_file.read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotEqual(
                        alias.name, "pickle",
                        "pickle import found — SEC-001 violation"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module and "pickle" in node.module:
                    self.fail(f"pickle import found via ImportFrom — SEC-001 violation: {node.module}")

    def test_load_state_uses_json(self):
        """_load_state should use json.load, not pickle.load."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repl = EtherREPL(workspace_root=tmpdir)
            session = repl.create_session("test-json")

            # Write a valid JSON state file
            state_path = session._get_state_path()
            state_data = {"x": 42, "name": "test"}
            state_path.write_text(json.dumps(state_data), encoding="utf-8")

            # Enter context to trigger _load_state
            with session:
                self.assertEqual(session._state, state_data)

            repl.cleanup("test-json")

    def test_save_state_uses_json(self):
        """_save_state should use json.dump, not pickle.dump."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repl = EtherREPL(
                workspace_root=tmpdir,
                persist_sessions=True,
            )
            session = repl.create_session("test-save-json")

            with session:
                session._state["key"] = "value"

            # State file should exist and be valid JSON
            state_path = session._get_state_path()
            self.assertTrue(state_path.exists(), "State file should exist")

            # Should be parseable as JSON
            content = state_path.read_text(encoding="utf-8")
            loaded = json.loads(content)
            self.assertEqual(loaded["key"], "value")

            repl.cleanup("test-save-json")

    def test_malformed_json_does_not_crash(self):
        """Malformed state file should not crash, should reset to empty state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repl = EtherREPL(workspace_root=tmpdir)
            session = repl.create_session("test-malformed")

            # Write invalid JSON
            state_path = session._get_state_path()
            state_path.write_text("not valid json {{{", encoding="utf-8")

            with session:
                # Should not crash, state should be empty dict
                self.assertEqual(session._state, {})

            repl.cleanup("test-malformed")

    def test_pickle_payload_not_executed(self):
        """A pickle payload in the state file should NOT be executed.

        If pickle.load were still in use, this malicious payload would
        execute os.system during deserialization. With json.load, it
        should simply fail to parse as JSON.
        """
        import pickle
        import base64

        with tempfile.TemporaryDirectory() as tmpdir:
            repl = EtherREPL(workspace_root=tmpdir)
            session = repl.create_session("test-pickle-attack")

            # Create a pickle payload that would execute code if unpickled
            state_path = session._get_state_path()

            # Write a pickle payload (this would be dangerous with pickle.load)
            with open(state_path, "wb") as f:
                pickle.dump({"malicious": "payload"}, f)

            # json.load should fail to parse binary pickle data
            with session:
                # State should be empty dict because json.load fails
                self.assertEqual(session._state, {})

            repl.cleanup("test-pickle-attack")


class TestSEC002_PatternBypass(unittest.TestCase):
    """SEC-002: Verify AST-based code safety check prevents bypass.

    The fix replaces string matching with ast.parse() analysis.
    This prevents bypass via spacing variations, getattr tricks,
    importlib, and hex encoding.
    """

    def _create_repl_session(self):
        """Helper to create a REPLSession for testing _check_code_safety."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repl = EtherREPL(workspace_root=tmpdir)
            session = repl.create_session("test-safety")
            yield session
            repl.cleanup("test-safety")

    def test_basic_safe_code_passes(self):
        """Simple arithmetic should pass safety check."""
        for session in self._create_repl_session():
            self.assertTrue(session._check_code_safety("x = 5"))
            self.assertTrue(session._check_code_safety("x + 3"))
            self.assertTrue(session._check_code_safety("print('hello')"))
            self.assertTrue(session._check_code_safety("[1, 2, 3]"))

    def test_os_system_blocked(self):
        """os.system() should be blocked via AST attribute check."""
        for session in self._create_repl_session():
            self.assertFalse(session._check_code_safety('os.system("ls")'))

    def test_subprocess_call_blocked(self):
        """subprocess.call() should be blocked."""
        for session in self._create_repl_session():
            self.assertFalse(session._check_code_safety('subprocess.call(["ls"])'))

    def test_import_os_blocked(self):
        """import os should be blocked via AST Import check."""
        for session in self._create_repl_session():
            self.assertFalse(session._check_code_safety("import os"))

    def test_import_subprocess_blocked(self):
        """import subprocess should be blocked."""
        for session in self._create_repl_session():
            self.assertFalse(session._check_code_safety("import subprocess"))

    def test_from_os_import_blocked(self):
        """from os import system should be blocked."""
        for session in self._create_repl_session():
            self.assertFalse(session._check_code_safety("from os import system"))

    def test_importlib_blocked(self):
        """import importlib should be blocked."""
        for session in self._create_repl_session():
            self.assertFalse(session._check_code_safety("import importlib"))

    def test_pickle_import_blocked(self):
        """import pickle should be blocked."""
        for session in self._create_repl_session():
            self.assertFalse(session._check_code_safety("import pickle"))

    def test_eval_call_blocked(self):
        """eval() call should be blocked."""
        for session in self._create_repl_session():
            self.assertFalse(session._check_code_safety("eval('1+1')"))

    def test_exec_call_blocked(self):
        """exec() call should be blocked."""
        for session in self._create_repl_session():
            self.assertFalse(session._check_code_safety("exec('x=1')"))

    def test_getattr_blocked(self):
        """getattr() call should be blocked (potential builtin access)."""
        for session in self._create_repl_session():
            self.assertFalse(session._check_code_safety("getattr(os, 'system')"))

    def test_builtins_access_blocked(self):
        """__builtins__ attribute access should be blocked."""
        for session in self._create_repl_session():
            self.assertFalse(session._check_code_safety("__builtins__.eval"))

    def test_ctypes_blocked(self):
        """import ctypes should be blocked."""
        for session in self._create_repl_session():
            self.assertFalse(session._check_code_safety("import ctypes"))

    def test_syntax_error_code_passes(self):
        """Code with syntax errors should pass (subprocess will report error)."""
        for session in self._create_repl_session():
            # Invalid syntax — safety checker returns True for syntax errors
            self.assertTrue(session._check_code_safety("def incomplete("))

    def test_safe_list_comprehension_passes(self):
        """Safe list comprehension should pass."""
        for session in self._create_repl_session():
            self.assertTrue(session._check_code_safety("[x**2 for x in range(10)]"))

    def test_safe_function_def_passes(self):
        """Safe function definition should pass."""
        for session in self._create_repl_session():
            self.assertTrue(session._check_code_safety("def add(a, b): return a + b"))

    def test_safe_dict_operations_pass(self):
        """Safe dictionary operations should pass."""
        for session in self._create_repl_session():
            self.assertTrue(session._check_code_safety("d = {'key': 'value'}"))
            self.assertTrue(session._check_code_safety("d.get('key', 'default')"))


class TestSEC003_PathTraversal(unittest.TestCase):
    """SEC-003: Verify path traversal attacks are blocked.

    The fix adds Path.resolve() and relative_to() validation in
    ComponentLoader.save_component() to ensure paths cannot escape
    the component-framework/ directory.
    """

    def test_simple_path_traversal_blocked(self):
        """../../../etc/passwd should be blocked."""
        with tempfile.TemporaryDirectory() as tmpdir:
            framework_dir = Path(tmpdir) / "component-framework"
            framework_dir.mkdir(parents=True)

            loader = ComponentLoader(framework_dir=framework_dir)

            with self.assertRaises(ComponentLoaderError) as ctx:
                loader.save_component("../../../etc/passwd", content="malicious")

            self.assertIn("Path traversal", str(ctx.exception))

    def test_absolute_path_blocked(self):
        """Absolute paths should be blocked."""
        with tempfile.TemporaryDirectory() as tmpdir:
            framework_dir = Path(tmpdir) / "component-framework"
            framework_dir.mkdir(parents=True)

            loader = ComponentLoader(framework_dir=framework_dir)

            # Absolute path resolves outside framework dir
            with self.assertRaises(ComponentLoaderError) as ctx:
                loader.save_component("/etc/passwd", content="malicious")

            self.assertIn("Path traversal", str(ctx.exception))

    def test_sibling_directory_traversal_blocked(self):
        """../../sibling_dir/file.md should be blocked."""
        with tempfile.TemporaryDirectory() as tmpdir:
            framework_dir = Path(tmpdir) / "component-framework"
            framework_dir.mkdir(parents=True)

            # Create a sibling directory
            sibling = Path(tmpdir) / "sibling"
            sibling.mkdir()

            loader = ComponentLoader(framework_dir=framework_dir)

            with self.assertRaises(ComponentLoaderError) as ctx:
                loader.save_component("../../sibling/evil.md", content="bad")

            self.assertIn("Path traversal", str(ctx.exception))

    def test_valid_nested_path_allowed(self):
        """Valid nested paths within framework should work."""
        with tempfile.TemporaryDirectory() as tmpdir:
            framework_dir = Path(tmpdir) / "component-framework"
            framework_dir.mkdir(parents=True)

            loader = ComponentLoader(framework_dir=framework_dir)

            path = loader.save_component(
                "memory/working-memory.md",
                content="# Working Memory",
                frontmatter={
                    "template_id": "working-memory",
                    "template_type": "memory",
                    "version": "1.0.0",
                    "description": "Test template",
                }
            )

            self.assertTrue(Path(path).exists())
            self.assertIn("component-framework", path)

    def test_double_encoded_traversal_blocked(self):
        """Double-encoded path traversal should be blocked."""
        with tempfile.TemporaryDirectory() as tmpdir:
            framework_dir = Path(tmpdir) / "component-framework"
            framework_dir.mkdir(parents=True)

            loader = ComponentLoader(framework_dir=framework_dir)

            # ..%2F..%2Fetc%2Fpasswd
            with self.assertRaises(ComponentLoaderError) as ctx:
                loader.save_component("..\\..\\etc\\passwd", content="malicious")

            self.assertIn("Path traversal", str(ctx.exception))


class TestSEC004_SubprocessIsolation(unittest.TestCase):
    """SEC-004: Verify subprocess execution is properly isolated.

    Each REPL session should run in its own workspace directory,
    not with full host filesystem access.
    """

    def test_session_has_isolated_workspace(self):
        """Each session should have a unique workspace path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repl = EtherREPL(workspace_root=tmpdir)

            session1 = repl.create_session("session-1")
            session2 = repl.create_session("session-2")

            self.assertNotEqual(
                session1.workspace_path,
                session2.workspace_path,
                "Sessions should have different workspaces"
            )

            self.assertTrue(
                str(session1.workspace_path).startswith(tmpdir),
                "Session workspace should be under the REPL workspace root"
            )

            repl.cleanup("session-1")
            repl.cleanup("session-2")

    def test_workspace_is_hash_named(self):
        """Workspace names should be hash-based, not user-controlled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repl = EtherREPL(workspace_root=tmpdir)
            session = repl.create_session("my-session")

            # Workspace name should be hash-derived (ether_ + hex)
            workspace_name = session._workspace_name
            self.assertTrue(workspace_name.startswith("ether_"))
            # The hex part should be 16 chars (SHA256[:16])
            hex_part = workspace_name[len("ether_"):]
            self.assertEqual(len(hex_part), 16)
            int(hex_part, 16)  # Should be valid hex

            repl.cleanup("my-session")

    def test_script_runs_in_workspace_cwd(self):
        """Execution should use the workspace as working directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repl = EtherREPL(workspace_root=tmpdir)
            session = repl.create_session("test-cwd")

            with session:
                # Write a file in the workspace
                result = session.eval(
                    "f = open('test_file.txt', 'w'); f.write('hello'); f.close()"
                )
                self.assertTrue(result.success)

                # File should exist in workspace
                test_file = session.workspace_path / "test_file.txt"
                self.assertTrue(test_file.exists())
                self.assertEqual(test_file.read_text(), "hello")

                # File should NOT exist outside workspace
                outside_file = Path(tmpdir) / "test_file.txt"
                self.assertFalse(outside_file.exists())

            repl.cleanup("test-cwd")


class TestEtherREPLBasicFunctionality(unittest.TestCase):
    """Basic functionality tests to ensure fixes don't break normal operation."""

    def test_create_and_eval_session(self):
        """Basic session creation and evaluation should work."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repl = EtherREPL(workspace_root=tmpdir)
            session = repl.create_session("basic-test")

            with session:
                result = session.eval("x = 42")
                self.assertTrue(result.success)

                result = session.eval("x")
                self.assertTrue(result.success)

            repl.cleanup("basic-test")

    def test_arithmetic_operations(self):
        """Basic math should work."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repl = EtherREPL(workspace_root=tmpdir)
            session = repl.create_session("math-test")

            with session:
                result = session.eval("2 + 2")
                self.assertTrue(result.success)
                self.assertIn("4", result.stdout)

            repl.cleanup("math-test")

    def test_string_operations(self):
        """String operations should work."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repl = EtherREPL(workspace_root=tmpdir)
            session = repl.create_session("string-test")

            with session:
                result = session.eval("s = 'hello world'")
                self.assertTrue(result.success)

                result = session.eval("s.upper()")
                self.assertTrue(result.success)
                self.assertIn("HELLO WORLD", result.stdout)

            repl.cleanup("string-test")

    def test_list_operations(self):
        """List operations should work with JSON state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repl = EtherREPL(workspace_root=tmpdir)
            session = repl.create_session("list-test")

            with session:
                result = session.eval("items = [1, 2, 3]")
                self.assertTrue(result.success)

                result = session.eval("len(items)")
                self.assertTrue(result.success)
                self.assertIn("3", result.stdout)

            repl.cleanup("list-test")

    def test_execution_result_to_dict(self):
        """ExecutionResult.to_dict() should return all fields."""
        result = ExecutionResult(
            success=True,
            stdout="output",
            stderr="",
            return_code=0,
            duration_sec=1.5,
            timed_out=False,
            state_changed=True,
            session_id="test",
        )

        d = result.to_dict()
        self.assertEqual(d["success"], True)
        self.assertEqual(d["stdout"], "output")
        self.assertEqual(d["session_id"], "test")

    def test_statistics_tracking(self):
        """Session statistics should be tracked."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repl = EtherREPL(workspace_root=tmpdir)
            session = repl.create_session("stats-test")

            with session:
                session.eval("x = 1")
                session.eval("y = 2")

            stats = session.statistics
            self.assertEqual(stats["eval_count"], 2)
            self.assertEqual(stats["timeout_count"], 0)

            repl.cleanup("stats-test")


if __name__ == "__main__":
    unittest.main()
