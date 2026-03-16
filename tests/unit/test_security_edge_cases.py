# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Edge case tests for the security module (gaia.security).

Covers the following untested scenarios:
1. is_write_blocked with symlink resolution (blocked directory via symlink)
2. _setup_audit_logging: no duplicate handlers on multiple PathValidator instances
3. create_backup: PermissionError from shutil.copy2 returns None
4. _prompt_overwrite: actual input loop with mocked input() - 'y', 'n', invalid
5. is_write_blocked: exception path returns (True, reason) with "unable to validate"
6. validate_write: file deleted between exists check and stat (OSError graceful)
7. _get_blocked_directories: USERPROFILE env var empty/missing on Windows
8. _format_size edge cases: exactly 1 MB, exactly 1 GB boundary values

All tests run without LLM or external services.
"""

import os
import platform
from pathlib import Path
from unittest.mock import patch

import pytest

from gaia.security import (
    BLOCKED_DIRECTORIES,
    PathValidator,
    _format_size,
    _get_blocked_directories,
    audit_logger,
)

# ============================================================================
# 1. is_write_blocked with symlink resolution
# ============================================================================


class TestIsWriteBlockedSymlink:
    """Test that is_write_blocked resolves symlinks before checking blocked dirs."""

    @pytest.fixture
    def validator(self, tmp_path):
        """Create a PathValidator with tmp_path as allowed."""
        return PathValidator(allowed_paths=[str(tmp_path)])

    @pytest.mark.skipif(
        platform.system() == "Windows" and not os.environ.get("CI"),
        reason="Symlinks may require elevated privileges on Windows",
    )
    def test_symlink_to_blocked_directory_is_blocked(self, validator, tmp_path):
        """A symlink pointing into a blocked directory should be blocked."""
        # We cannot create actual symlinks into real system dirs without
        # permissions, so we mock the realpath resolution instead.
        fake_file = tmp_path / "innocent_looking.txt"

        # Pick a known blocked directory
        blocked_dir = next(iter(BLOCKED_DIRECTORIES))

        with patch("os.path.realpath") as mock_realpath:
            # Make os.path.realpath return a path inside the blocked directory
            fake_target = os.path.join(blocked_dir, "evil.txt")
            mock_realpath.return_value = fake_target

            is_blocked, reason = validator.is_write_blocked(str(fake_file))

        assert is_blocked is True
        assert (
            "protected system directory" in reason.lower()
            or "blocked" in reason.lower()
        )

    def test_symlink_to_safe_directory_not_blocked(self, validator, tmp_path):
        """A file (or symlink) resolving to a safe directory is not blocked."""
        safe_file = tmp_path / "safe_file.txt"
        safe_file.write_text("safe")

        is_blocked, reason = validator.is_write_blocked(str(safe_file))
        assert is_blocked is False
        assert reason == ""

    @pytest.mark.skipif(
        not hasattr(os, "symlink"),
        reason="os.symlink not available on this platform",
    )
    def test_real_symlink_to_safe_file_not_blocked(self, validator, tmp_path):
        """A real symlink to a safe file is not blocked."""
        target = tmp_path / "real_target.txt"
        target.write_text("target content")
        link = tmp_path / "link_to_target.txt"
        try:
            os.symlink(str(target), str(link))
        except OSError:
            pytest.skip("Cannot create symlinks (insufficient privileges)")

        is_blocked, reason = validator.is_write_blocked(str(link))
        assert is_blocked is False
        assert reason == ""


# ============================================================================
# 2. _setup_audit_logging: no duplicate handlers
# ============================================================================


class TestSetupAuditLoggingNoDuplicates:
    """Test that creating multiple PathValidators does not duplicate handlers."""

    def test_multiple_validators_no_duplicate_handlers(self, tmp_path):
        """Creating multiple PathValidator instances should not add duplicate handlers."""
        # Record initial handler count
        initial_handler_count = len(audit_logger.handlers)

        # Create multiple PathValidator instances
        v1 = PathValidator(allowed_paths=[str(tmp_path)])
        count_after_first = len(audit_logger.handlers)

        v2 = PathValidator(allowed_paths=[str(tmp_path)])
        count_after_second = len(audit_logger.handlers)

        v3 = PathValidator(allowed_paths=[str(tmp_path)])
        count_after_third = len(audit_logger.handlers)

        # The handler count should not grow after the first validator adds one
        # (if no handler existed initially) or stay the same (if one already existed)
        assert count_after_second == count_after_first
        assert count_after_third == count_after_first

    def test_setup_audit_logging_only_adds_handler_when_none_exist(self, tmp_path):
        """_setup_audit_logging checks if handlers already exist before adding."""
        # If handlers already exist (from prior tests), it should not add more
        existing_count = len(audit_logger.handlers)
        v = PathValidator(allowed_paths=[str(tmp_path)])

        if existing_count == 0:
            # First time: should have added exactly one handler
            assert len(audit_logger.handlers) == 1
        else:
            # Handlers already existed: count should not change
            assert len(audit_logger.handlers) == existing_count


# ============================================================================
# 3. create_backup: PermissionError from shutil.copy2 returns None
# ============================================================================


class TestCreateBackupPermissionError:
    """Test create_backup when shutil.copy2 raises PermissionError."""

    @pytest.fixture
    def validator(self, tmp_path):
        return PathValidator(allowed_paths=[str(tmp_path)])

    def test_permission_error_returns_none(self, validator, tmp_path):
        """create_backup returns None (not crash) when copy2 raises PermissionError."""
        target = tmp_path / "locked_file.txt"
        target.write_text("locked content")

        with patch("shutil.copy2", side_effect=PermissionError("Access denied")):
            result = validator.create_backup(str(target))

        assert result is None

    def test_os_error_returns_none(self, validator, tmp_path):
        """create_backup returns None when copy2 raises OSError."""
        target = tmp_path / "error_file.txt"
        target.write_text("content")

        with patch("shutil.copy2", side_effect=OSError("Disk full")):
            result = validator.create_backup(str(target))

        assert result is None

    def test_nonexistent_file_returns_none(self, validator, tmp_path):
        """create_backup returns None for nonexistent file."""
        ghost = tmp_path / "ghost.txt"
        result = validator.create_backup(str(ghost))
        assert result is None

    def test_generic_exception_returns_none(self, validator, tmp_path):
        """create_backup returns None for any unexpected exception."""
        target = tmp_path / "weird_file.txt"
        target.write_text("data")

        with patch("shutil.copy2", side_effect=RuntimeError("Unexpected")):
            result = validator.create_backup(str(target))

        assert result is None


# ============================================================================
# 4. _prompt_overwrite: test actual input loop with mocked input()
# ============================================================================


class TestPromptOverwrite:
    """Test _prompt_overwrite input loop with mocked input()."""

    @pytest.fixture
    def validator(self, tmp_path):
        return PathValidator(allowed_paths=[str(tmp_path)])

    def test_prompt_overwrite_yes(self, validator, tmp_path):
        """User responding 'y' approves the overwrite."""
        target = tmp_path / "file.txt"
        target.write_text("data")

        with patch("builtins.input", return_value="y"):
            result = validator._prompt_overwrite(target, 100)

        assert result is True

    def test_prompt_overwrite_no(self, validator, tmp_path):
        """User responding 'n' declines the overwrite."""
        target = tmp_path / "file.txt"
        target.write_text("data")

        with patch("builtins.input", return_value="n"):
            result = validator._prompt_overwrite(target, 100)

        assert result is False

    def test_prompt_overwrite_yes_full_word(self, validator, tmp_path):
        """User responding 'yes' approves the overwrite."""
        target = tmp_path / "file.txt"
        target.write_text("data")

        with patch("builtins.input", return_value="yes"):
            result = validator._prompt_overwrite(target, 100)

        assert result is True

    def test_prompt_overwrite_no_full_word(self, validator, tmp_path):
        """User responding 'no' declines the overwrite."""
        target = tmp_path / "file.txt"
        target.write_text("data")

        with patch("builtins.input", return_value="no"):
            result = validator._prompt_overwrite(target, 100)

        assert result is False

    def test_prompt_overwrite_invalid_then_yes(self, validator, tmp_path):
        """Invalid inputs are retried until 'y' is given."""
        target = tmp_path / "file.txt"
        target.write_text("data")

        # Simulate: "maybe" -> "xxx" -> "y"
        with patch("builtins.input", side_effect=["maybe", "xxx", "y"]):
            result = validator._prompt_overwrite(target, 200)

        assert result is True

    def test_prompt_overwrite_invalid_then_no(self, validator, tmp_path):
        """Invalid inputs are retried until 'n' is given."""
        target = tmp_path / "file.txt"
        target.write_text("data")

        # Simulate: "" -> "asdf" -> "n"
        with patch("builtins.input", side_effect=["", "asdf", "n"]):
            result = validator._prompt_overwrite(target, 50)

        assert result is False

    def test_prompt_overwrite_prints_file_info(self, validator, tmp_path):
        """Prompt should print the file path and size info."""
        target = tmp_path / "important.txt"
        target.write_text("important data")

        printed_lines = []

        with patch(
            "builtins.print",
            side_effect=lambda *a, **kw: printed_lines.append(
                " ".join(str(x) for x in a)
            ),
        ):
            with patch("builtins.input", return_value="y"):
                validator._prompt_overwrite(target, 2048)

        printed_output = "\n".join(printed_lines)
        assert str(target) in printed_output
        assert "2.0 KB" in printed_output


# ============================================================================
# 5. is_write_blocked: exception path returns (True, "unable to validate")
# ============================================================================


class TestIsWriteBlockedException:
    """Test is_write_blocked exception handling path."""

    @pytest.fixture
    def validator(self, tmp_path):
        return PathValidator(allowed_paths=[str(tmp_path)])

    def test_exception_during_path_resolution_returns_blocked(self, validator):
        """When os.path.realpath raises, is_write_blocked returns (True, reason)."""
        with patch("os.path.realpath", side_effect=OSError("Permission denied")):
            is_blocked, reason = validator.is_write_blocked("/some/weird/path.txt")

        assert is_blocked is True
        assert "unable to validate" in reason.lower()

    def test_exception_from_path_resolve_returns_blocked(self, validator):
        """When Path.resolve() raises, is_write_blocked returns (True, reason)."""
        with patch("os.path.realpath", return_value="/tmp/test.txt"):
            with patch.object(
                Path, "resolve", side_effect=RuntimeError("Resolve failed")
            ):
                is_blocked, reason = validator.is_write_blocked("/tmp/test.txt")

        assert is_blocked is True
        assert "unable to validate" in reason.lower()

    def test_exception_includes_error_detail(self, validator):
        """The reason string should include the error message."""
        with patch("os.path.realpath", side_effect=ValueError("Bad path chars")):
            is_blocked, reason = validator.is_write_blocked("/invalid\x00path")

        assert is_blocked is True
        assert "Bad path chars" in reason


# ============================================================================
# 6. validate_write: file deleted between exists check and stat (OSError)
# ============================================================================


class TestValidateWriteFileDeletedRace:
    """Test validate_write handling of TOCTOU race where file vanishes."""

    @pytest.fixture
    def validator(self, tmp_path):
        return PathValidator(allowed_paths=[str(tmp_path)])

    def test_file_deleted_between_exists_and_stat(self, validator, tmp_path):
        """validate_write handles OSError when file vanishes after exists check."""
        target = tmp_path / "vanishing.txt"
        target.write_text("now you see me")

        # The code does:
        #   if real_path.exists() and prompt_user:
        #       existing_size = real_path.stat().st_size  <-- OSError here
        # We need exists() to return True, but stat() to raise.
        # Since exists() internally calls stat(), we patch exists() directly
        # to return True, and stat() to raise OSError.
        original_stat = Path.stat
        original_exists = Path.exists
        stat_call_count = [0]

        def patched_exists(self_path, *args, **kwargs):
            # Return True for our target path to simulate "file existed"
            if str(self_path).endswith("vanishing.txt"):
                return True
            return original_exists(self_path, *args, **kwargs)

        def patched_stat(self_path, *args, **kwargs):
            # Raise OSError for our target to simulate "file deleted"
            if str(self_path).endswith("vanishing.txt"):
                stat_call_count[0] += 1
                raise OSError("File was deleted")
            return original_stat(self_path, *args, **kwargs)

        with patch.object(Path, "exists", patched_exists):
            with patch.object(Path, "stat", patched_stat):
                is_allowed, reason = validator.validate_write(
                    str(target), content_size=100, prompt_user=True
                )

        # Should succeed because the OSError is caught with `pass`
        assert is_allowed is True
        assert reason == ""

    def test_file_never_existed_passes(self, validator, tmp_path):
        """validate_write for a new file (does not exist) passes without prompting."""
        new_file = tmp_path / "brand_new_file.txt"
        is_allowed, reason = validator.validate_write(
            str(new_file), content_size=100, prompt_user=True
        )
        assert is_allowed is True
        assert reason == ""


# ============================================================================
# 7. _get_blocked_directories: USERPROFILE env var empty/missing on Windows
# ============================================================================


class TestGetBlockedDirectoriesUserProfile:
    """Test _get_blocked_directories with empty/missing USERPROFILE."""

    @pytest.mark.skipif(platform.system() != "Windows", reason="Windows-specific test")
    def test_userprofile_empty_string(self):
        """Empty USERPROFILE should not produce empty-string blocked dirs."""
        with patch.dict(os.environ, {"USERPROFILE": ""}, clear=False):
            result = _get_blocked_directories()

        # Empty strings and normpath("") should have been discarded
        assert "" not in result
        assert os.path.normpath("") not in result

    @pytest.mark.skipif(platform.system() != "Windows", reason="Windows-specific test")
    def test_userprofile_missing(self):
        """Missing USERPROFILE env var should not crash."""
        env_copy = dict(os.environ)
        env_copy.pop("USERPROFILE", None)

        with patch.dict(os.environ, env_copy, clear=True):
            # os.environ.get("USERPROFILE", "") returns ""
            result = _get_blocked_directories()

        assert isinstance(result, set)
        # Empty string paths should have been cleaned out
        assert "" not in result

    @pytest.mark.skipif(platform.system() != "Windows", reason="Windows-specific test")
    def test_userprofile_valid_produces_ssh_dir(self):
        """Valid USERPROFILE produces .ssh in blocked directories."""
        with patch.dict(os.environ, {"USERPROFILE": r"C:\Users\TestUser"}, clear=False):
            result = _get_blocked_directories()

        expected_ssh = os.path.normpath(r"C:\Users\TestUser\.ssh")
        assert expected_ssh in result

    @pytest.mark.skipif(platform.system() == "Windows", reason="Unix-specific test")
    def test_unix_blocked_dirs_independent_of_userprofile(self):
        """On Unix, USERPROFILE is irrelevant; blocked dirs come from Path.home()."""
        result = _get_blocked_directories()
        home = str(Path.home())
        assert os.path.join(home, ".ssh") in result
        assert "/etc" in result

    def test_blocked_directories_always_returns_set(self):
        """_get_blocked_directories always returns a set regardless of platform."""
        result = _get_blocked_directories()
        assert isinstance(result, set)
        assert len(result) > 0


# ============================================================================
# 8. _format_size edge cases: exactly 1 MB, exactly 1 GB boundary values
# ============================================================================


class TestFormatSizeBoundaries:
    """Test _format_size at exact boundary values."""

    def test_exactly_1_mb(self):
        """Exactly 1 MB (1048576 bytes) should display as MB."""
        result = _format_size(1024 * 1024)
        assert "MB" in result
        assert "1.0" in result

    def test_exactly_1_gb(self):
        """Exactly 1 GB (1073741824 bytes) should display as GB."""
        result = _format_size(1024 * 1024 * 1024)
        assert "GB" in result
        assert "1.0" in result

    def test_one_byte_below_1_kb(self):
        """1023 bytes should display as bytes, not KB."""
        result = _format_size(1023)
        assert "B" in result
        assert "1023" in result
        assert "KB" not in result

    def test_one_byte_below_1_mb(self):
        """1048575 bytes (1 MB - 1) should display as KB."""
        result = _format_size(1024 * 1024 - 1)
        assert "KB" in result
        assert "MB" not in result

    def test_one_byte_below_1_gb(self):
        """1073741823 bytes (1 GB - 1) should display as MB."""
        result = _format_size(1024 * 1024 * 1024 - 1)
        assert "MB" in result
        assert "GB" not in result

    def test_exactly_1_kb(self):
        """Exactly 1 KB (1024 bytes) should display as KB."""
        result = _format_size(1024)
        assert "KB" in result
        assert "1.0" in result

    def test_large_gb_value(self):
        """10 GB should format correctly."""
        result = _format_size(10 * 1024 * 1024 * 1024)
        assert "GB" in result
        assert "10.0" in result

    def test_fractional_kb(self):
        """1536 bytes should display as 1.5 KB."""
        result = _format_size(1536)
        assert "KB" in result
        assert "1.5" in result

    def test_fractional_mb(self):
        """2.5 MB should display correctly."""
        result = _format_size(int(2.5 * 1024 * 1024))
        assert "MB" in result
        assert "2.5" in result

    def test_zero_bytes(self):
        """0 bytes should display as '0 B'."""
        assert _format_size(0) == "0 B"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
