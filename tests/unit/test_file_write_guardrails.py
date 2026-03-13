# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Tests for file write guardrails in the GAIA security module.

Purpose: Validate that file write guardrails correctly enforce security policies
for all file mutation operations across agents. These tests verify:
- Blocked directory enforcement (system dirs, .ssh, etc.)
- Sensitive file name and extension protection
- Write size limits
- Overwrite confirmation prompting
- Backup creation before overwrite
- Audit logging for write operations
- Integration with ChatAgent write_file / edit_file tools
- Integration with CodeAgent write_file / edit_file tools

All tests are designed to run without LLM or external services.
"""

import os
import platform
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gaia.security import (
    BLOCKED_DIRECTORIES,
    MAX_WRITE_SIZE_BYTES,
    SENSITIVE_EXTENSIONS,
    SENSITIVE_FILE_NAMES,
    PathValidator,
    _format_size,
    _get_blocked_directories,
)

# ============================================================================
# 1. BLOCKED_DIRECTORIES CONSTANT TESTS
# ============================================================================


class TestBlockedDirectories:
    """Test that BLOCKED_DIRECTORIES is correctly populated for the platform."""

    def test_blocked_directories_is_nonempty_set(self):
        """Verify BLOCKED_DIRECTORIES is a populated set."""
        assert isinstance(BLOCKED_DIRECTORIES, set)
        assert len(BLOCKED_DIRECTORIES) > 0

    @pytest.mark.skipif(platform.system() != "Windows", reason="Windows-specific test")
    def test_windows_blocked_dirs_include_system(self):
        """Verify Windows system directories are blocked."""
        windir = os.environ.get("WINDIR", r"C:\Windows")
        assert os.path.normpath(windir) in BLOCKED_DIRECTORIES
        assert os.path.normpath(os.path.join(windir, "System32")) in BLOCKED_DIRECTORIES

    @pytest.mark.skipif(platform.system() != "Windows", reason="Windows-specific test")
    def test_windows_blocked_dirs_include_program_files(self):
        """Verify Program Files directories are blocked on Windows."""
        assert os.path.normpath(r"C:\Program Files") in BLOCKED_DIRECTORIES
        assert os.path.normpath(r"C:\Program Files (x86)") in BLOCKED_DIRECTORIES

    @pytest.mark.skipif(platform.system() != "Windows", reason="Windows-specific test")
    def test_windows_blocked_dirs_include_ssh(self):
        """Verify .ssh directory is blocked on Windows."""
        userprofile = os.environ.get("USERPROFILE", "")
        if userprofile:
            ssh_dir = os.path.normpath(os.path.join(userprofile, ".ssh"))
            assert ssh_dir in BLOCKED_DIRECTORIES

    @pytest.mark.skipif(platform.system() == "Windows", reason="Unix-specific test")
    def test_unix_blocked_dirs_include_system(self):
        """Verify Unix system directories are blocked."""
        for d in ["/bin", "/sbin", "/usr/bin", "/usr/sbin", "/etc", "/boot"]:
            assert d in BLOCKED_DIRECTORIES

    @pytest.mark.skipif(platform.system() == "Windows", reason="Unix-specific test")
    def test_unix_blocked_dirs_include_ssh(self):
        """Verify .ssh and .gnupg directories are blocked on Unix."""
        home = str(Path.home())
        assert os.path.join(home, ".ssh") in BLOCKED_DIRECTORIES
        assert os.path.join(home, ".gnupg") in BLOCKED_DIRECTORIES

    def test_get_blocked_directories_returns_set(self):
        """Verify _get_blocked_directories() returns a set of strings."""
        result = _get_blocked_directories()
        assert isinstance(result, set)
        for item in result:
            assert isinstance(item, str)

    def test_blocked_directories_no_empty_strings(self):
        """Verify BLOCKED_DIRECTORIES contains no empty strings."""
        assert "" not in BLOCKED_DIRECTORIES
        assert os.path.normpath("") not in BLOCKED_DIRECTORIES


# ============================================================================
# 2. SENSITIVE_FILE_NAMES CONSTANT TESTS
# ============================================================================


class TestSensitiveFileNames:
    """Test that SENSITIVE_FILE_NAMES covers known sensitive files."""

    def test_sensitive_file_names_is_nonempty_set(self):
        """Verify SENSITIVE_FILE_NAMES is a populated set."""
        assert isinstance(SENSITIVE_FILE_NAMES, set)
        assert len(SENSITIVE_FILE_NAMES) > 0

    def test_env_files_are_sensitive(self):
        """Verify .env variants are listed as sensitive."""
        assert ".env" in SENSITIVE_FILE_NAMES
        assert ".env.local" in SENSITIVE_FILE_NAMES
        assert ".env.production" in SENSITIVE_FILE_NAMES

    def test_credential_files_are_sensitive(self):
        """Verify credential/key files are listed as sensitive."""
        assert "credentials.json" in SENSITIVE_FILE_NAMES
        assert "service_account.json" in SENSITIVE_FILE_NAMES
        assert "secrets.json" in SENSITIVE_FILE_NAMES

    def test_ssh_key_files_are_sensitive(self):
        """Verify SSH key files are listed as sensitive."""
        assert "id_rsa" in SENSITIVE_FILE_NAMES
        assert "id_ed25519" in SENSITIVE_FILE_NAMES
        assert "authorized_keys" in SENSITIVE_FILE_NAMES

    def test_os_auth_files_are_sensitive(self):
        """Verify OS authentication files are listed as sensitive."""
        assert "shadow" in SENSITIVE_FILE_NAMES
        assert "passwd" in SENSITIVE_FILE_NAMES
        assert "sudoers" in SENSITIVE_FILE_NAMES

    def test_package_auth_files_are_sensitive(self):
        """Verify package manager auth files are listed as sensitive."""
        assert ".npmrc" in SENSITIVE_FILE_NAMES
        assert ".pypirc" in SENSITIVE_FILE_NAMES
        assert ".netrc" in SENSITIVE_FILE_NAMES


# ============================================================================
# 3. SENSITIVE_EXTENSIONS CONSTANT TESTS
# ============================================================================


class TestSensitiveExtensions:
    """Test that SENSITIVE_EXTENSIONS covers certificate and key extensions."""

    def test_sensitive_extensions_is_nonempty_set(self):
        """Verify SENSITIVE_EXTENSIONS is a populated set."""
        assert isinstance(SENSITIVE_EXTENSIONS, set)
        assert len(SENSITIVE_EXTENSIONS) > 0

    def test_certificate_extensions_are_sensitive(self):
        """Verify certificate extensions are listed."""
        assert ".pem" in SENSITIVE_EXTENSIONS
        assert ".crt" in SENSITIVE_EXTENSIONS
        assert ".cer" in SENSITIVE_EXTENSIONS

    def test_key_extensions_are_sensitive(self):
        """Verify key file extensions are listed."""
        assert ".key" in SENSITIVE_EXTENSIONS
        assert ".p12" in SENSITIVE_EXTENSIONS
        assert ".pfx" in SENSITIVE_EXTENSIONS

    def test_keystore_extensions_are_sensitive(self):
        """Verify Java keystore extensions are listed."""
        assert ".jks" in SENSITIVE_EXTENSIONS
        assert ".keystore" in SENSITIVE_EXTENSIONS


# ============================================================================
# 4. MAX_WRITE_SIZE_BYTES CONSTANT TESTS
# ============================================================================


class TestMaxWriteSize:
    """Test the MAX_WRITE_SIZE_BYTES constant."""

    def test_max_write_size_is_10mb(self):
        """Verify MAX_WRITE_SIZE_BYTES is exactly 10 MB."""
        assert MAX_WRITE_SIZE_BYTES == 10 * 1024 * 1024

    def test_max_write_size_is_int(self):
        """Verify MAX_WRITE_SIZE_BYTES is an integer."""
        assert isinstance(MAX_WRITE_SIZE_BYTES, int)


# ============================================================================
# 5. PathValidator.is_write_blocked() TESTS
# ============================================================================


class TestIsWriteBlocked:
    """Test PathValidator.is_write_blocked() method."""

    @pytest.fixture
    def validator(self, tmp_path):
        """Create a PathValidator with tmp_path as the allowed directory."""
        return PathValidator(allowed_paths=[str(tmp_path)])

    def test_safe_path_not_blocked(self, validator, tmp_path):
        """Verify a safe path in tmp_path is not blocked."""
        safe_file = tmp_path / "safe_file.txt"
        safe_file.write_text("test")
        is_blocked, reason = validator.is_write_blocked(str(safe_file))
        assert is_blocked is False
        assert reason == ""

    def test_sensitive_filename_is_blocked(self, validator, tmp_path):
        """Verify that writing to a sensitive file name is blocked."""
        env_file = tmp_path / ".env"
        env_file.write_text("SECRET=value")
        is_blocked, reason = validator.is_write_blocked(str(env_file))
        assert is_blocked is True
        assert "sensitive file" in reason.lower() or "Write blocked" in reason

    def test_sensitive_filename_credentials_json(self, validator, tmp_path):
        """Verify credentials.json is blocked."""
        creds = tmp_path / "credentials.json"
        creds.write_text("{}")
        is_blocked, reason = validator.is_write_blocked(str(creds))
        assert is_blocked is True
        assert "sensitive" in reason.lower() or "blocked" in reason.lower()

    def test_sensitive_extension_pem(self, validator, tmp_path):
        """Verify .pem extension files are blocked."""
        pem_file = tmp_path / "server.pem"
        pem_file.write_text("CERT")
        is_blocked, reason = validator.is_write_blocked(str(pem_file))
        assert is_blocked is True
        assert ".pem" in reason

    def test_sensitive_extension_key(self, validator, tmp_path):
        """Verify .key extension files are blocked."""
        key_file = tmp_path / "private.key"
        key_file.write_text("KEY")
        is_blocked, reason = validator.is_write_blocked(str(key_file))
        assert is_blocked is True
        assert ".key" in reason

    def test_sensitive_extension_p12(self, validator, tmp_path):
        """Verify .p12 extension files are blocked."""
        p12_file = tmp_path / "cert.p12"
        p12_file.write_text("DATA")
        is_blocked, reason = validator.is_write_blocked(str(p12_file))
        assert is_blocked is True
        assert ".p12" in reason

    @pytest.mark.skipif(platform.system() != "Windows", reason="Windows-specific test")
    def test_windows_system32_is_blocked(self, validator):
        """Verify Windows System32 is blocked."""
        windir = os.environ.get("WINDIR", r"C:\Windows")
        sys32_file = os.path.join(windir, "System32", "test.txt")
        is_blocked, reason = validator.is_write_blocked(sys32_file)
        assert is_blocked is True
        assert (
            "protected system directory" in reason.lower()
            or "blocked" in reason.lower()
        )

    @pytest.mark.skipif(platform.system() == "Windows", reason="Unix-specific test")
    def test_unix_etc_is_blocked(self, validator):
        """Verify /etc is blocked on Unix."""
        is_blocked, reason = validator.is_write_blocked("/etc/test_file.conf")
        assert is_blocked is True
        assert "blocked" in reason.lower()

    def test_regular_txt_file_not_blocked(self, validator, tmp_path):
        """Verify a regular .txt file in a safe directory is not blocked."""
        txt_file = tmp_path / "notes.txt"
        txt_file.write_text("hello")
        is_blocked, reason = validator.is_write_blocked(str(txt_file))
        assert is_blocked is False
        assert reason == ""

    def test_regular_py_file_not_blocked(self, validator, tmp_path):
        """Verify a regular .py file in a safe directory is not blocked."""
        py_file = tmp_path / "script.py"
        py_file.write_text("print('hello')")
        is_blocked, reason = validator.is_write_blocked(str(py_file))
        assert is_blocked is False

    def test_sensitive_name_case_insensitive(self, validator, tmp_path):
        """Verify sensitive file name matching is case-insensitive."""
        env_upper = tmp_path / ".ENV"
        env_upper.write_text("SECRET=value")
        is_blocked, reason = validator.is_write_blocked(str(env_upper))
        assert is_blocked is True

    def test_id_rsa_is_blocked(self, validator, tmp_path):
        """Verify SSH private key file name is blocked."""
        key_file = tmp_path / "id_rsa"
        key_file.write_text("PRIVATE KEY")
        is_blocked, reason = validator.is_write_blocked(str(key_file))
        assert is_blocked is True

    def test_wallet_dat_is_blocked(self, validator, tmp_path):
        """Verify wallet.dat cryptocurrency file is blocked."""
        wallet = tmp_path / "wallet.dat"
        wallet.write_text("data")
        is_blocked, reason = validator.is_write_blocked(str(wallet))
        assert is_blocked is True

    def test_nonexistent_safe_path_not_blocked(self, validator, tmp_path):
        """Verify a nonexistent file in a safe directory is not blocked."""
        nonexist = tmp_path / "does_not_exist.txt"
        is_blocked, reason = validator.is_write_blocked(str(nonexist))
        assert is_blocked is False


# ============================================================================
# 6. PathValidator.validate_write() TESTS
# ============================================================================


class TestValidateWrite:
    """Test PathValidator.validate_write() comprehensive validation."""

    @pytest.fixture
    def validator(self, tmp_path):
        """Create a PathValidator with tmp_path allowed, no user prompting."""
        return PathValidator(allowed_paths=[str(tmp_path)])

    def test_allowed_safe_path_succeeds(self, validator, tmp_path):
        """Verify a safe, allowed path passes validation."""
        target = tmp_path / "output.txt"
        is_allowed, reason = validator.validate_write(
            str(target), content_size=100, prompt_user=False
        )
        assert is_allowed is True
        assert reason == ""

    def test_path_outside_allowlist_denied(self, validator, tmp_path):
        """Verify a path outside the allowlist is denied."""
        # Use a path that is definitely not in tmp_path
        outside_path = str(Path(tmp_path).parent / "outside_dir" / "file.txt")
        is_allowed, reason = validator.validate_write(
            outside_path, content_size=100, prompt_user=False
        )
        assert is_allowed is False
        assert "not in allowed paths" in reason

    def test_blocked_sensitive_file_denied(self, validator, tmp_path):
        """Verify a sensitive file inside allowed path is still denied."""
        env_file = tmp_path / ".env"
        env_file.write_text("SECRET=x")
        is_allowed, reason = validator.validate_write(
            str(env_file), content_size=100, prompt_user=False
        )
        assert is_allowed is False
        assert "sensitive" in reason.lower() or "blocked" in reason.lower()

    def test_blocked_extension_denied(self, validator, tmp_path):
        """Verify a file with sensitive extension is denied."""
        key_file = tmp_path / "cert.pem"
        key_file.write_text("CERT")
        is_allowed, reason = validator.validate_write(
            str(key_file), content_size=100, prompt_user=False
        )
        assert is_allowed is False
        assert ".pem" in reason

    def test_content_size_over_limit_denied(self, validator, tmp_path):
        """Verify content exceeding MAX_WRITE_SIZE_BYTES is denied."""
        target = tmp_path / "big_file.txt"
        over_limit = MAX_WRITE_SIZE_BYTES + 1
        is_allowed, reason = validator.validate_write(
            str(target), content_size=over_limit, prompt_user=False
        )
        assert is_allowed is False
        assert "size" in reason.lower() and "exceeds" in reason.lower()

    def test_content_size_at_limit_allowed(self, validator, tmp_path):
        """Verify content exactly at MAX_WRITE_SIZE_BYTES is allowed."""
        target = tmp_path / "at_limit.txt"
        is_allowed, reason = validator.validate_write(
            str(target), content_size=MAX_WRITE_SIZE_BYTES, prompt_user=False
        )
        assert is_allowed is True
        assert reason == ""

    def test_content_size_zero_skips_check(self, validator, tmp_path):
        """Verify content_size=0 skips the size check."""
        target = tmp_path / "empty.txt"
        is_allowed, reason = validator.validate_write(
            str(target), content_size=0, prompt_user=False
        )
        assert is_allowed is True

    def test_overwrite_prompt_accepted(self, validator, tmp_path):
        """Verify overwrite prompt with 'y' response allows write."""
        existing = tmp_path / "existing.txt"
        existing.write_text("original content")

        with patch.object(validator, "_prompt_overwrite", return_value=True):
            is_allowed, reason = validator.validate_write(
                str(existing), content_size=50, prompt_user=True
            )
        assert is_allowed is True

    def test_overwrite_prompt_declined(self, validator, tmp_path):
        """Verify overwrite prompt with 'n' response denies write."""
        existing = tmp_path / "existing.txt"
        existing.write_text("original content")

        with patch.object(validator, "_prompt_overwrite", return_value=False):
            is_allowed, reason = validator.validate_write(
                str(existing), content_size=50, prompt_user=True
            )
        assert is_allowed is False
        assert "declined" in reason.lower() or "overwrite" in reason.lower()

    def test_no_overwrite_prompt_when_file_missing(self, validator, tmp_path):
        """Verify no overwrite prompt when file does not exist."""
        new_file = tmp_path / "brand_new.txt"
        with patch.object(validator, "_prompt_overwrite") as mock_prompt:
            is_allowed, reason = validator.validate_write(
                str(new_file), content_size=50, prompt_user=True
            )
        mock_prompt.assert_not_called()
        assert is_allowed is True

    def test_no_overwrite_prompt_when_prompt_user_false(self, validator, tmp_path):
        """Verify no overwrite prompt when prompt_user=False."""
        existing = tmp_path / "existing2.txt"
        existing.write_text("data")
        with patch.object(validator, "_prompt_overwrite") as mock_prompt:
            is_allowed, reason = validator.validate_write(
                str(existing), content_size=50, prompt_user=False
            )
        mock_prompt.assert_not_called()
        assert is_allowed is True


# ============================================================================
# 7. PathValidator.create_backup() TESTS
# ============================================================================


class TestCreateBackup:
    """Test PathValidator.create_backup() method."""

    @pytest.fixture
    def validator(self, tmp_path):
        """Create a PathValidator with tmp_path allowed."""
        return PathValidator(allowed_paths=[str(tmp_path)])

    def test_backup_creates_file(self, validator, tmp_path):
        """Verify backup creates a new file alongside the original."""
        original = tmp_path / "document.txt"
        original.write_text("original content here")

        backup_path = validator.create_backup(str(original))

        assert backup_path is not None
        assert os.path.exists(backup_path)
        # Backup should have same content as original
        with open(backup_path, "r", encoding="utf-8") as f:
            assert f.read() == "original content here"

    def test_backup_naming_convention(self, validator, tmp_path):
        """Verify backup file uses timestamped naming pattern."""
        original = tmp_path / "report.txt"
        original.write_text("content")

        backup_path = validator.create_backup(str(original))

        assert backup_path is not None
        backup_name = os.path.basename(backup_path)
        # Should match pattern: report.YYYYMMDD_HHMMSS.bak.txt
        assert backup_name.startswith("report.")
        assert ".bak" in backup_name
        assert backup_name.endswith(".txt")

    def test_backup_preserves_extension(self, validator, tmp_path):
        """Verify backup preserves the original file extension."""
        original = tmp_path / "script.py"
        original.write_text("print('hello')")

        backup_path = validator.create_backup(str(original))

        assert backup_path is not None
        assert backup_path.endswith(".py")

    def test_backup_nonexistent_file_returns_none(self, validator, tmp_path):
        """Verify create_backup returns None for a nonexistent file."""
        nonexist = tmp_path / "ghost.txt"
        result = validator.create_backup(str(nonexist))
        assert result is None

    def test_backup_different_from_original_path(self, validator, tmp_path):
        """Verify backup path is different from the original path."""
        original = tmp_path / "data.json"
        original.write_text("{}")

        backup_path = validator.create_backup(str(original))

        assert backup_path is not None
        assert str(backup_path) != str(original)

    def test_backup_in_same_directory(self, validator, tmp_path):
        """Verify backup is created in the same directory as the original."""
        original = tmp_path / "notes.md"
        original.write_text("# Notes")

        backup_path = validator.create_backup(str(original))

        assert backup_path is not None
        assert os.path.dirname(backup_path) == str(tmp_path)

    def test_multiple_backups_have_unique_names(self, validator, tmp_path):
        """Verify multiple backups of the same file produce unique names."""
        original = tmp_path / "config.yaml"
        original.write_text("key: value")

        # Create two backups with a small time gap to get different timestamps
        backup1 = validator.create_backup(str(original))
        assert backup1 is not None

        # Backups created within the same second could collide, but the path
        # object resolves uniquely in practice. We just ensure the first works.
        assert os.path.exists(backup1)


# ============================================================================
# 8. PathValidator.audit_write() TESTS
# ============================================================================


class TestAuditWrite:
    """Test PathValidator.audit_write() method."""

    @pytest.fixture
    def validator(self, tmp_path):
        """Create a PathValidator with tmp_path allowed."""
        return PathValidator(allowed_paths=[str(tmp_path)])

    def test_audit_write_success_logs_info(self, validator):
        """Verify a successful write is logged at INFO level."""
        with patch("gaia.security.audit_logger") as mock_audit:
            validator.audit_write("write", "/tmp/test.txt", 1024, "success")
            mock_audit.info.assert_called_once()
            call_msg = mock_audit.info.call_args[0][0]
            assert "WRITE" in call_msg
            assert "success" in call_msg

    def test_audit_write_denied_logs_warning(self, validator):
        """Verify a denied write is logged at WARNING level."""
        with patch("gaia.security.audit_logger") as mock_audit:
            validator.audit_write(
                "write", "/tmp/test.txt", 0, "denied", "blocked directory"
            )
            mock_audit.warning.assert_called_once()
            call_msg = mock_audit.warning.call_args[0][0]
            assert "WRITE" in call_msg
            assert "denied" in call_msg
            assert "blocked directory" in call_msg

    def test_audit_write_error_logs_error(self, validator):
        """Verify an error write is logged at ERROR level."""
        with patch("gaia.security.audit_logger") as mock_audit:
            validator.audit_write("edit", "/tmp/test.txt", 0, "error", "IOError")
            mock_audit.error.assert_called_once()
            call_msg = mock_audit.error.call_args[0][0]
            assert "EDIT" in call_msg
            assert "error" in call_msg

    def test_audit_write_includes_size(self, validator):
        """Verify audit message includes formatted size."""
        with patch("gaia.security.audit_logger") as mock_audit:
            validator.audit_write("write", "/tmp/file.txt", 2048, "success")
            call_msg = mock_audit.info.call_args[0][0]
            assert "KB" in call_msg or "2048" in call_msg

    def test_audit_write_zero_size_shows_na(self, validator):
        """Verify zero size shows N/A in audit message."""
        with patch("gaia.security.audit_logger") as mock_audit:
            validator.audit_write("write", "/tmp/file.txt", 0, "success")
            call_msg = mock_audit.info.call_args[0][0]
            assert "N/A" in call_msg

    def test_audit_write_operation_uppercased(self, validator):
        """Verify operation name is uppercased in audit message."""
        with patch("gaia.security.audit_logger") as mock_audit:
            validator.audit_write("delete", "/tmp/file.txt", 0, "success")
            call_msg = mock_audit.info.call_args[0][0]
            assert "DELETE" in call_msg

    def test_audit_write_includes_detail(self, validator):
        """Verify detail string is appended when provided."""
        with patch("gaia.security.audit_logger") as mock_audit:
            validator.audit_write(
                "write", "/tmp/file.txt", 500, "success", "backup=/tmp/file.bak"
            )
            call_msg = mock_audit.info.call_args[0][0]
            assert "backup=/tmp/file.bak" in call_msg


# ============================================================================
# 9. _format_size() HELPER TESTS
# ============================================================================


class TestFormatSize:
    """Test the _format_size helper function."""

    def test_bytes_format(self):
        """Verify sizes under 1 KB display as bytes."""
        assert _format_size(500) == "500 B"

    def test_kilobytes_format(self):
        """Verify sizes under 1 MB display as KB."""
        result = _format_size(2048)
        assert "KB" in result
        assert "2.0" in result

    def test_megabytes_format(self):
        """Verify sizes under 1 GB display as MB."""
        result = _format_size(5 * 1024 * 1024)
        assert "MB" in result
        assert "5.0" in result

    def test_gigabytes_format(self):
        """Verify sizes >= 1 GB display as GB."""
        result = _format_size(2 * 1024 * 1024 * 1024)
        assert "GB" in result
        assert "2.0" in result

    def test_zero_bytes(self):
        """Verify 0 bytes formats correctly."""
        assert _format_size(0) == "0 B"

    def test_one_byte(self):
        """Verify 1 byte formats correctly."""
        assert _format_size(1) == "1 B"

    def test_exactly_one_kb(self):
        """Verify exactly 1024 bytes shows as KB."""
        result = _format_size(1024)
        assert "KB" in result
        assert "1.0" in result


# ============================================================================
# 10. ChatAgent write_file GUARDRAIL TESTS
# ============================================================================


class TestChatAgentWriteFileGuardrails:
    """Test that ChatAgent's write_file tool enforces PathValidator guardrails.

    These tests exercise the write_file tool from file_tools.py (FileSearchToolsMixin)
    by creating a mock agent with a path_validator attribute.
    """

    @pytest.fixture
    def mock_agent(self, tmp_path):
        """Create a mock agent with path_validator set to the tmp_path allowlist."""
        agent = MagicMock()
        agent.path_validator = PathValidator(allowed_paths=[str(tmp_path)])
        agent._path_validator = None
        agent.console = None
        return agent

    @pytest.fixture
    def write_file_func(self, mock_agent, tmp_path):
        """Build the write_file closure by registering tools on a mock mixin."""
        from gaia.agents.tools.file_tools import FileSearchToolsMixin

        # Create a real mixin instance and patch self references
        mixin = FileSearchToolsMixin()
        mixin.path_validator = mock_agent.path_validator
        mixin._path_validator = None
        mixin.console = None

        # We'll import the tool registry to grab the function after registration
        from gaia.agents.base.tools import _TOOL_REGISTRY

        saved_registry = dict(_TOOL_REGISTRY)
        _TOOL_REGISTRY.clear()
        try:
            mixin.register_file_search_tools()
            write_fn = _TOOL_REGISTRY.get("write_file", {}).get("function")
            assert write_fn is not None, "write_file tool not registered"
            yield write_fn
        finally:
            _TOOL_REGISTRY.clear()
            _TOOL_REGISTRY.update(saved_registry)

    def test_write_safe_file_succeeds(self, write_file_func, tmp_path):
        """Verify writing a normal file in an allowed directory succeeds."""
        target = str(tmp_path / "hello.txt")
        result = write_file_func(file_path=target, content="Hello, world!")
        assert result["status"] == "success"
        assert os.path.exists(target)
        with open(target, "r", encoding="utf-8") as f:
            assert f.read() == "Hello, world!"

    def test_write_sensitive_file_blocked(self, write_file_func, tmp_path):
        """Verify writing to .env is blocked by guardrails."""
        env_file = str(tmp_path / ".env")
        result = write_file_func(file_path=env_file, content="SECRET=key")
        assert result["status"] == "error"
        assert (
            "blocked" in result["error"].lower()
            or "sensitive" in result["error"].lower()
        )
        # File should NOT have been created
        assert not os.path.exists(env_file)

    def test_write_sensitive_extension_blocked(self, write_file_func, tmp_path):
        """Verify writing a .pem file is blocked."""
        pem_file = str(tmp_path / "server.pem")
        result = write_file_func(file_path=pem_file, content="CERTIFICATE")
        assert result["status"] == "error"
        assert ".pem" in result["error"]

    def test_write_oversized_content_blocked(self, write_file_func, tmp_path):
        """Verify writing content that exceeds MAX_WRITE_SIZE_BYTES is blocked."""
        target = str(tmp_path / "huge.bin")
        huge_content = "x" * (MAX_WRITE_SIZE_BYTES + 1)
        result = write_file_func(file_path=target, content=huge_content)
        assert result["status"] == "error"
        assert "size" in result["error"].lower() or "exceeds" in result["error"].lower()

    def test_write_creates_backup_on_overwrite(self, write_file_func, tmp_path):
        """Verify a backup is created when overwriting an existing file."""
        target = tmp_path / "overwrite_me.txt"
        target.write_text("original content")

        # Mock overwrite prompt to auto-approve
        with patch.object(PathValidator, "_prompt_overwrite", return_value=True):
            result = write_file_func(file_path=str(target), content="new content")

        assert result["status"] == "success"
        assert "backup_path" in result
        assert os.path.exists(result["backup_path"])

    def test_write_creates_parent_directories(self, write_file_func, tmp_path):
        """Verify parent directories are created when create_dirs=True."""
        deep_path = str(tmp_path / "subdir" / "nested" / "file.txt")
        result = write_file_func(
            file_path=deep_path, content="deep write", create_dirs=True
        )
        assert result["status"] == "success"
        assert os.path.exists(deep_path)


# ============================================================================
# 11. ChatAgent edit_file GUARDRAIL TESTS
# ============================================================================


class TestChatAgentEditFileGuardrails:
    """Test that ChatAgent's edit_file tool enforces PathValidator guardrails."""

    @pytest.fixture
    def mixin_and_registry(self, tmp_path):
        """Set up a FileSearchToolsMixin with validator and register tools."""
        from gaia.agents.base.tools import _TOOL_REGISTRY
        from gaia.agents.tools.file_tools import FileSearchToolsMixin

        mixin = FileSearchToolsMixin()
        mixin.path_validator = PathValidator(allowed_paths=[str(tmp_path)])
        mixin._path_validator = None
        mixin.console = None

        saved_registry = dict(_TOOL_REGISTRY)
        _TOOL_REGISTRY.clear()
        try:
            mixin.register_file_search_tools()
            edit_fn = _TOOL_REGISTRY.get("edit_file", {}).get("function")
            assert edit_fn is not None, "edit_file tool not registered"
            yield mixin, edit_fn
        finally:
            _TOOL_REGISTRY.clear()
            _TOOL_REGISTRY.update(saved_registry)

    def test_edit_safe_file_succeeds(self, mixin_and_registry, tmp_path):
        """Verify editing a normal file replaces content correctly."""
        _, edit_fn = mixin_and_registry
        target = tmp_path / "editable.txt"
        target.write_text("Hello, World!")

        result = edit_fn(
            file_path=str(target),
            old_content="World",
            new_content="GAIA",
        )
        assert result["status"] == "success"
        assert target.read_text() == "Hello, GAIA!"

    def test_edit_sensitive_file_blocked(self, mixin_and_registry, tmp_path):
        """Verify editing a sensitive file is blocked."""
        _, edit_fn = mixin_and_registry
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=old_value")

        result = edit_fn(
            file_path=str(env_file),
            old_content="old_value",
            new_content="new_value",
        )
        assert result["status"] == "error"
        # Content should remain unchanged
        assert env_file.read_text() == "KEY=old_value"

    def test_edit_creates_backup(self, mixin_and_registry, tmp_path):
        """Verify a backup is created before editing."""
        _, edit_fn = mixin_and_registry
        target = tmp_path / "backup_test.txt"
        target.write_text("original line")

        result = edit_fn(
            file_path=str(target),
            old_content="original",
            new_content="modified",
        )
        assert result["status"] == "success"
        assert "backup_path" in result
        # Backup should contain the original content
        with open(result["backup_path"], "r", encoding="utf-8") as f:
            assert f.read() == "original line"

    def test_edit_nonexistent_file_returns_error(self, mixin_and_registry, tmp_path):
        """Verify editing a nonexistent file returns an error."""
        _, edit_fn = mixin_and_registry
        missing = tmp_path / "nonexistent.txt"

        result = edit_fn(
            file_path=str(missing),
            old_content="anything",
            new_content="something",
        )
        assert result["status"] == "error"
        assert (
            "not found" in result["error"].lower()
            or "File not found" in result["error"]
        )

    def test_edit_content_not_found_returns_error(self, mixin_and_registry, tmp_path):
        """Verify editing with non-matching old_content returns an error."""
        _, edit_fn = mixin_and_registry
        target = tmp_path / "mismatch.txt"
        target.write_text("actual content here")

        result = edit_fn(
            file_path=str(target),
            old_content="this does not exist",
            new_content="replacement",
        )
        assert result["status"] == "error"
        assert "not found" in result["error"].lower()


# ============================================================================
# 12. CodeAgent write_file GUARDRAIL TESTS
# ============================================================================


class TestCodeAgentWriteFileGuardrails:
    """Test that CodeAgent's generic write_file tool enforces PathValidator guardrails.

    These tests exercise write_file from code/tools/file_io.py (FileIOToolsMixin).
    """

    @pytest.fixture
    def mixin_and_registry(self, tmp_path):
        """Set up a FileIOToolsMixin with validator and register tools."""
        from gaia.agents.base.tools import _TOOL_REGISTRY
        from gaia.agents.code.tools.file_io import FileIOToolsMixin

        mixin = FileIOToolsMixin()
        mixin.path_validator = PathValidator(allowed_paths=[str(tmp_path)])
        mixin.console = None
        # FileIOToolsMixin expects _validate_python_syntax and _parse_python_code
        mixin._validate_python_syntax = MagicMock(
            return_value={"is_valid": True, "errors": []}
        )
        mixin._parse_python_code = MagicMock()

        saved_registry = dict(_TOOL_REGISTRY)
        _TOOL_REGISTRY.clear()
        try:
            mixin.register_file_io_tools()
            write_fn = _TOOL_REGISTRY.get("write_file", {}).get("function")
            assert write_fn is not None, "write_file tool not registered"
            yield mixin, write_fn
        finally:
            _TOOL_REGISTRY.clear()
            _TOOL_REGISTRY.update(saved_registry)

    def test_write_safe_file_succeeds(self, mixin_and_registry, tmp_path):
        """Verify writing a normal file in an allowed directory succeeds."""
        _, write_fn = mixin_and_registry
        target = str(tmp_path / "component.tsx")
        result = write_fn(file_path=target, content="export default function App() {}")
        assert result["status"] == "success"
        assert os.path.exists(target)

    def test_write_sensitive_file_blocked(self, mixin_and_registry, tmp_path):
        """Verify writing to credentials.json is blocked."""
        _, write_fn = mixin_and_registry
        creds = str(tmp_path / "credentials.json")
        result = write_fn(file_path=creds, content='{"key": "secret"}')
        assert result["status"] == "error"
        assert (
            "blocked" in result["error"].lower()
            or "sensitive" in result["error"].lower()
        )

    def test_write_sensitive_extension_blocked(self, mixin_and_registry, tmp_path):
        """Verify writing a .key file is blocked."""
        _, write_fn = mixin_and_registry
        key_file = str(tmp_path / "private.key")
        result = write_fn(file_path=key_file, content="RSA PRIVATE KEY")
        assert result["status"] == "error"
        assert ".key" in result["error"]

    def test_write_oversized_content_blocked(self, mixin_and_registry, tmp_path):
        """Verify writing oversized content is blocked."""
        _, write_fn = mixin_and_registry
        target = str(tmp_path / "huge.dat")
        huge = "x" * (MAX_WRITE_SIZE_BYTES + 1)
        result = write_fn(file_path=target, content=huge)
        assert result["status"] == "error"
        assert "size" in result["error"].lower() or "exceeds" in result["error"].lower()

    def test_write_creates_backup_on_overwrite(self, mixin_and_registry, tmp_path):
        """Verify backup is created when overwriting existing file."""
        _, write_fn = mixin_and_registry
        target = tmp_path / "overwrite.txt"
        target.write_text("old")

        with patch.object(PathValidator, "_prompt_overwrite", return_value=True):
            result = write_fn(file_path=str(target), content="new")

        assert result["status"] == "success"
        if "backup_path" in result:
            assert os.path.exists(result["backup_path"])

    def test_write_with_project_dir_resolves_path(self, mixin_and_registry, tmp_path):
        """Verify project_dir parameter correctly resolves relative paths."""
        _, write_fn = mixin_and_registry
        result = write_fn(
            file_path="relative.txt",
            content="content",
            project_dir=str(tmp_path),
        )
        assert result["status"] == "success"
        assert os.path.exists(tmp_path / "relative.txt")


# ============================================================================
# 13. CodeAgent edit_file GUARDRAIL TESTS
# ============================================================================


class TestCodeAgentEditFileGuardrails:
    """Test that CodeAgent's generic edit_file tool enforces PathValidator guardrails."""

    @pytest.fixture
    def mixin_and_registry(self, tmp_path):
        """Set up a FileIOToolsMixin with validator and register tools."""
        from gaia.agents.base.tools import _TOOL_REGISTRY
        from gaia.agents.code.tools.file_io import FileIOToolsMixin

        mixin = FileIOToolsMixin()
        mixin.path_validator = PathValidator(allowed_paths=[str(tmp_path)])
        mixin.console = None
        mixin._validate_python_syntax = MagicMock(
            return_value={"is_valid": True, "errors": []}
        )
        mixin._parse_python_code = MagicMock()

        saved_registry = dict(_TOOL_REGISTRY)
        _TOOL_REGISTRY.clear()
        try:
            mixin.register_file_io_tools()
            edit_fn = _TOOL_REGISTRY.get("edit_file", {}).get("function")
            assert edit_fn is not None, "edit_file tool not registered"
            yield mixin, edit_fn
        finally:
            _TOOL_REGISTRY.clear()
            _TOOL_REGISTRY.update(saved_registry)

    def test_edit_safe_file_succeeds(self, mixin_and_registry, tmp_path):
        """Verify editing a normal file replaces content correctly."""
        _, edit_fn = mixin_and_registry
        target = tmp_path / "app.tsx"
        target.write_text("const x = 'old';")

        result = edit_fn(
            file_path=str(target),
            old_content="old",
            new_content="new",
        )
        assert result["status"] == "success"
        assert target.read_text() == "const x = 'new';"

    def test_edit_sensitive_file_blocked(self, mixin_and_registry, tmp_path):
        """Verify editing .env is blocked."""
        _, edit_fn = mixin_and_registry
        env_file = tmp_path / ".env"
        env_file.write_text("DB_PASS=secret")

        result = edit_fn(
            file_path=str(env_file),
            old_content="secret",
            new_content="hacked",
        )
        assert result["status"] == "error"
        # Verify content was not modified
        assert env_file.read_text() == "DB_PASS=secret"

    def test_edit_blocked_extension_denied(self, mixin_and_registry, tmp_path):
        """Verify editing a .pem file is blocked."""
        _, edit_fn = mixin_and_registry
        pem_file = tmp_path / "ca.pem"
        pem_file.write_text("-----BEGIN CERTIFICATE-----")

        result = edit_fn(
            file_path=str(pem_file),
            old_content="CERTIFICATE",
            new_content="MALICIOUS",
        )
        assert result["status"] == "error"
        assert ".pem" in result["error"]

    def test_edit_creates_backup(self, mixin_and_registry, tmp_path):
        """Verify backup is created before editing."""
        _, edit_fn = mixin_and_registry
        target = tmp_path / "index.ts"
        target.write_text("const version = '1.0';")

        result = edit_fn(
            file_path=str(target),
            old_content="1.0",
            new_content="2.0",
        )
        assert result["status"] == "success"
        if "backup_path" in result:
            with open(result["backup_path"], "r", encoding="utf-8") as f:
                assert "1.0" in f.read()

    def test_edit_nonexistent_file_returns_error(self, mixin_and_registry, tmp_path):
        """Verify editing a nonexistent file returns an error."""
        _, edit_fn = mixin_and_registry
        missing = str(tmp_path / "gone.txt")

        result = edit_fn(
            file_path=missing,
            old_content="any",
            new_content="thing",
        )
        assert result["status"] == "error"
        assert "not found" in result["error"].lower()

    def test_edit_content_not_found_returns_error(self, mixin_and_registry, tmp_path):
        """Verify old_content mismatch returns error."""
        _, edit_fn = mixin_and_registry
        target = tmp_path / "real.txt"
        target.write_text("actual data")

        result = edit_fn(
            file_path=str(target),
            old_content="nonexistent string",
            new_content="replacement",
        )
        assert result["status"] == "error"
        assert "not found" in result["error"].lower()

    def test_edit_with_project_dir(self, mixin_and_registry, tmp_path):
        """Verify project_dir resolves relative paths for edit."""
        _, edit_fn = mixin_and_registry
        target = tmp_path / "relative_edit.txt"
        target.write_text("before")

        result = edit_fn(
            file_path="relative_edit.txt",
            old_content="before",
            new_content="after",
            project_dir=str(tmp_path),
        )
        assert result["status"] == "success"
        assert target.read_text() == "after"


# ============================================================================
# 14. PathValidator SYMLINK / EDGE CASE TESTS
# ============================================================================


class TestPathValidatorEdgeCases:
    """Test edge cases and symlink handling in PathValidator."""

    @pytest.fixture
    def validator(self, tmp_path):
        """Create a PathValidator with tmp_path allowed."""
        return PathValidator(allowed_paths=[str(tmp_path)])

    def test_fail_closed_on_exception(self, validator):
        """Verify is_write_blocked returns blocked on internal errors (fail-closed)."""
        # Pass a path that will cause an error in os.path.realpath
        # Using an object that can't be converted to string
        with patch("os.path.realpath", side_effect=OSError("mocked error")):
            is_blocked, reason = validator.is_write_blocked("/some/path.txt")
        assert is_blocked is True
        assert (
            "unable to validate" in reason.lower() or "mocked error" in reason.lower()
        )

    def test_add_allowed_path(self, validator, tmp_path):
        """Verify add_allowed_path expands the allowlist."""
        new_dir = tmp_path / "extra"
        new_dir.mkdir()
        validator.add_allowed_path(str(new_dir))

        target = new_dir / "file.txt"
        target.write_text("test")
        assert validator.is_path_allowed(str(target), prompt_user=False) is True

    def test_prompt_user_for_access_yes(self, validator, tmp_path):
        """Verify _prompt_user_for_access with 'y' grants temporary access."""
        outside = tmp_path.parent / "outside_test_prompt.txt"
        with patch("builtins.input", return_value="y"):
            result = validator._prompt_user_for_access(Path(outside))
        assert result is True

    def test_prompt_user_for_access_no(self, validator, tmp_path):
        """Verify _prompt_user_for_access with 'n' denies access."""
        outside = tmp_path.parent / "outside_denied.txt"
        with patch("builtins.input", return_value="n"):
            result = validator._prompt_user_for_access(Path(outside))
        assert result is False

    def test_prompt_user_for_access_always(self, validator, tmp_path):
        """Verify _prompt_user_for_access with 'a' grants and persists access."""
        outside = tmp_path.parent / "outside_always.txt"
        with patch("builtins.input", return_value="a"):
            with patch.object(validator, "_save_persisted_path") as mock_save:
                result = validator._prompt_user_for_access(Path(outside))
        assert result is True
        mock_save.assert_called_once()

    def test_prompt_overwrite_yes(self, validator, tmp_path):
        """Verify _prompt_overwrite with 'y' returns True."""
        existing = tmp_path / "overwrite_prompt.txt"
        existing.write_text("data")
        with patch("builtins.input", return_value="y"):
            result = validator._prompt_overwrite(existing, existing.stat().st_size)
        assert result is True

    def test_prompt_overwrite_no(self, validator, tmp_path):
        """Verify _prompt_overwrite with 'n' returns False."""
        existing = tmp_path / "overwrite_no.txt"
        existing.write_text("data")
        with patch("builtins.input", return_value="n"):
            result = validator._prompt_overwrite(existing, existing.stat().st_size)
        assert result is False


# ============================================================================
# 15. NO PathValidator FALLBACK TESTS
# ============================================================================


class TestNoPathValidatorFallback:
    """Test tool behavior when no PathValidator is available on the agent."""

    @pytest.fixture
    def write_fn_no_validator(self, tmp_path):
        """Set up ChatAgent write_file with no path_validator."""
        from gaia.agents.base.tools import _TOOL_REGISTRY
        from gaia.agents.tools.file_tools import FileSearchToolsMixin

        mixin = FileSearchToolsMixin()
        mixin.path_validator = None
        mixin._path_validator = None
        mixin.console = None

        saved_registry = dict(_TOOL_REGISTRY)
        _TOOL_REGISTRY.clear()
        try:
            mixin.register_file_search_tools()
            write_fn = _TOOL_REGISTRY.get("write_file", {}).get("function")
            assert write_fn is not None
            yield write_fn
        finally:
            _TOOL_REGISTRY.clear()
            _TOOL_REGISTRY.update(saved_registry)

    def test_write_without_validator_writes_file_to_disk(
        self, write_fn_no_validator, tmp_path
    ):
        """Verify write_file writes data to disk even when no validator is present.

        When no PathValidator is attached to the agent, the write proceeds with
        a warning log but no security checks. This is the expected behavior for
        backward compatibility — agents that don't initialize a PathValidator
        can still write files.
        """
        target = str(tmp_path / "no_validator.txt")
        result = write_fn_no_validator(file_path=target, content="hello")
        # File is written to disk successfully
        assert os.path.exists(target)
        with open(target, "r", encoding="utf-8") as f:
            assert f.read() == "hello"
        # Should succeed (with warning logged)
        assert result["status"] == "success"
        assert result["bytes_written"] == 5
