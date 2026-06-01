#!/usr/bin/env python
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for write guardrails on file I/O tools.

Validates that write_python_file, edit_python_file, write_markdown_file,
and replace_function enforce the same security guardrails (validate_write /
is_write_blocked + audit + backup) as write_file and edit_file.

See: https://github.com/amd/gaia/issues/955
"""

import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gaia.agents.base.tools import _TOOL_REGISTRY


def _make_agent_with_mocked_validator():
    """Create a CodeAgent with a mocked path_validator for guardrail testing."""
    from gaia.agents.code.agent import CodeAgent

    agent = CodeAgent(silent_mode=True, max_steps=5)
    agent._register_tools()

    # Build a mock PathValidator with all required methods
    mock_pv = MagicMock()
    mock_pv.is_path_allowed.return_value = True
    mock_pv.is_write_blocked.return_value = (False, "")
    mock_pv.validate_write.return_value = (True, "")
    mock_pv.create_backup.return_value = "/tmp/backup"
    mock_pv.audit_write = MagicMock()

    agent.path_validator = mock_pv
    return agent, mock_pv


class TestWritePythonFileGuardrails(unittest.TestCase):
    """write_python_file must use validate_write + audit + backup."""

    def setUp(self):
        self.agent, self.mock_pv = _make_agent_with_mocked_validator()
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)
        _TOOL_REGISTRY.clear()

    def _get_tool(self):
        return _TOOL_REGISTRY["write_python_file"]

    def test_rejects_when_validate_write_denies(self):
        """write_python_file should reject writes denied by validate_write."""
        self.mock_pv.validate_write.return_value = (
            False,
            "Write blocked: /etc/passwd is in a blocked directory",
        )

        tool_fn = self._get_tool()
        result = tool_fn(
            file_path="/etc/passwd",
            content="print('hello')",
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("blocked", result["error"])
        self.mock_pv.audit_write.assert_called()
        # Verify the audit recorded a denial
        audit_calls = [
            c for c in self.mock_pv.audit_write.call_args_list if c[0][3] == "denied"
        ]
        self.assertGreater(len(audit_calls), 0)

    def test_audits_successful_write(self):
        """write_python_file should audit successful writes."""
        path = os.path.join(self.test_dir, "test.py")
        self.mock_pv.validate_write.return_value = (True, "")

        tool_fn = self._get_tool()
        result = tool_fn(file_path=path, content="x = 1\n")

        self.assertEqual(result["status"], "success")
        # Should have audited the success
        audit_calls = [
            c for c in self.mock_pv.audit_write.call_args_list if c[0][3] == "success"
        ]
        self.assertGreater(len(audit_calls), 0)

    def test_creates_backup_on_overwrite(self):
        """write_python_file should create backup when overwriting existing file."""
        path = os.path.join(self.test_dir, "existing.py")
        with open(path, "w") as f:
            f.write("old = True\n")

        self.mock_pv.validate_write.return_value = (True, "")
        self.mock_pv.create_backup.return_value = path + ".bak"

        tool_fn = self._get_tool()
        result = tool_fn(file_path=path, content="new = True\n")

        self.assertEqual(result["status"], "success")
        self.mock_pv.create_backup.assert_called_once_with(path)


class TestEditPythonFileGuardrails(unittest.TestCase):
    """edit_python_file must use is_write_blocked + size check + audit."""

    def setUp(self):
        self.agent, self.mock_pv = _make_agent_with_mocked_validator()
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)
        _TOOL_REGISTRY.clear()

    def _get_tool(self):
        return _TOOL_REGISTRY["edit_python_file"]

    def test_rejects_blocked_path(self):
        """edit_python_file should reject writes to blocked paths."""
        self.mock_pv.is_write_blocked.return_value = (
            True,
            "Write blocked: path is in a blocked directory",
        )

        tool_fn = self._get_tool()
        result = tool_fn(
            file_path="/etc/shadow",
            old_content="old",
            new_content="new",
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("blocked", result["error"].lower())
        audit_calls = [
            c for c in self.mock_pv.audit_write.call_args_list if c[0][3] == "denied"
        ]
        self.assertGreater(len(audit_calls), 0)

    def test_rejects_disallowed_path(self):
        """edit_python_file should reject paths not in allowlist."""
        self.mock_pv.is_write_blocked.return_value = (False, "")
        self.mock_pv.is_path_allowed.return_value = False

        tool_fn = self._get_tool()
        result = tool_fn(
            file_path="/some/random/path.py",
            old_content="old",
            new_content="new",
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("Access denied", result["error"])

    @patch("gaia.security.MAX_WRITE_SIZE_BYTES", 10)
    def test_rejects_oversized_content(self):
        """edit_python_file should reject replacement content exceeding size limit."""
        self.mock_pv.is_write_blocked.return_value = (False, "")
        self.mock_pv.is_path_allowed.return_value = True

        tool_fn = self._get_tool()
        result = tool_fn(
            file_path="/tmp/test.py",
            old_content="x = 1",
            new_content="x" * 100,  # exceeds 10-byte limit
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("exceeds", result["error"].lower())

    def test_audits_successful_edit(self):
        """edit_python_file should audit successful edits."""
        path = os.path.join(self.test_dir, "test.py")
        with open(path, "w") as f:
            f.write("x = 1\n")

        self.mock_pv.is_write_blocked.return_value = (False, "")
        self.mock_pv.is_path_allowed.return_value = True

        tool_fn = self._get_tool()
        result = tool_fn(
            file_path=path,
            old_content="x = 1",
            new_content="x = 2",
        )

        self.assertEqual(result["status"], "success")
        audit_calls = [
            c for c in self.mock_pv.audit_write.call_args_list if c[0][3] == "success"
        ]
        self.assertGreater(len(audit_calls), 0)


class TestWriteMarkdownFileGuardrails(unittest.TestCase):
    """write_markdown_file must use validate_write + audit + backup."""

    def setUp(self):
        self.agent, self.mock_pv = _make_agent_with_mocked_validator()
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)
        _TOOL_REGISTRY.clear()

    def _get_tool(self):
        return _TOOL_REGISTRY["write_markdown_file"]

    def test_rejects_when_validate_write_denies(self):
        """write_markdown_file should reject writes denied by validate_write."""
        self.mock_pv.validate_write.return_value = (
            False,
            "Write blocked: sensitive file",
        )

        tool_fn = self._get_tool()
        result = tool_fn(
            file_path="/etc/cron.d/evil.md",
            content="# Evil markdown",
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("blocked", result["error"].lower())
        audit_calls = [
            c for c in self.mock_pv.audit_write.call_args_list if c[0][3] == "denied"
        ]
        self.assertGreater(len(audit_calls), 0)

    def test_audits_successful_write(self):
        """write_markdown_file should audit successful writes."""
        path = os.path.join(self.test_dir, "readme.md")
        self.mock_pv.validate_write.return_value = (True, "")

        tool_fn = self._get_tool()
        result = tool_fn(file_path=path, content="# Hello\n")

        self.assertEqual(result["status"], "success")
        audit_calls = [
            c for c in self.mock_pv.audit_write.call_args_list if c[0][3] == "success"
        ]
        self.assertGreater(len(audit_calls), 0)

    def test_creates_backup_on_overwrite(self):
        """write_markdown_file should create backup when overwriting."""
        path = os.path.join(self.test_dir, "existing.md")
        with open(path, "w") as f:
            f.write("# Old\n")

        self.mock_pv.validate_write.return_value = (True, "")
        self.mock_pv.create_backup.return_value = path + ".bak"

        tool_fn = self._get_tool()
        result = tool_fn(file_path=path, content="# New\n")

        self.assertEqual(result["status"], "success")
        self.mock_pv.create_backup.assert_called_once_with(path)


class TestReplaceFunctionGuardrails(unittest.TestCase):
    """replace_function must use is_write_blocked + size check + audit."""

    def setUp(self):
        self.agent, self.mock_pv = _make_agent_with_mocked_validator()
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)
        _TOOL_REGISTRY.clear()

    def _get_tool(self):
        return _TOOL_REGISTRY["replace_function"]

    def test_rejects_blocked_path(self):
        """replace_function should reject writes to blocked paths."""
        self.mock_pv.is_write_blocked.return_value = (
            True,
            "Write blocked: path is in a blocked directory",
        )

        tool_fn = self._get_tool()
        result = tool_fn(
            file_path="/etc/some_config.py",
            function_name="dangerous",
            new_implementation="def dangerous(): pass",
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("blocked", result["error"].lower())
        audit_calls = [
            c for c in self.mock_pv.audit_write.call_args_list if c[0][3] == "denied"
        ]
        self.assertGreater(len(audit_calls), 0)

    def test_rejects_disallowed_path(self):
        """replace_function should reject paths not in allowlist."""
        self.mock_pv.is_write_blocked.return_value = (False, "")
        self.mock_pv.is_path_allowed.return_value = False

        tool_fn = self._get_tool()
        result = tool_fn(
            file_path="/some/random/file.py",
            function_name="foo",
            new_implementation="def foo(): pass",
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("Access denied", result["error"])

    @patch("gaia.security.MAX_WRITE_SIZE_BYTES", 10)
    def test_rejects_oversized_implementation(self):
        """replace_function should reject oversized new_implementation."""
        self.mock_pv.is_write_blocked.return_value = (False, "")
        self.mock_pv.is_path_allowed.return_value = True

        tool_fn = self._get_tool()
        result = tool_fn(
            file_path="/tmp/test.py",
            function_name="foo",
            new_implementation="def foo():\n    " + "x" * 100,
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("exceeds", result["error"].lower())

    def test_audits_successful_replacement(self):
        """replace_function should audit successful function replacements."""
        path = os.path.join(self.test_dir, "module.py")
        with open(path, "w") as f:
            f.write("def greet():\n    return 'hello'\n")

        self.mock_pv.is_write_blocked.return_value = (False, "")
        self.mock_pv.is_path_allowed.return_value = True

        tool_fn = self._get_tool()
        result = tool_fn(
            file_path=path,
            function_name="greet",
            new_implementation="def greet():\n    return 'hi'",
        )

        self.assertEqual(result["status"], "success")
        audit_calls = [
            c for c in self.mock_pv.audit_write.call_args_list if c[0][3] == "success"
        ]
        self.assertGreater(len(audit_calls), 0)


if __name__ == "__main__":
    unittest.main()
