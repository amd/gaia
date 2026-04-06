# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Security utilities for GAIA.
Handles path validation, user prompting, persistent allow-lists,
blocked path enforcement, write guardrails, and audit logging.
"""

import datetime
import json
import logging
import os
import platform
import shutil
from pathlib import Path
from typing import List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Audit logger — separate from main logger for file operation tracking
audit_logger = logging.getLogger("gaia.security.audit")

# Maximum file size the agent is allowed to write (10 MB)
MAX_WRITE_SIZE_BYTES = 10 * 1024 * 1024

# Sensitive file names that should never be written to by the agent
SENSITIVE_FILE_NAMES: Set[str] = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    "credentials.json",
    "service_account.json",
    "secrets.json",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "id_dsa",
    "authorized_keys",
    "known_hosts",
    "shadow",
    "passwd",
    "sudoers",
    "htpasswd",
    ".netrc",
    ".pgpass",
    ".my.cnf",
    "wallet.dat",
    "keystore.jks",
    ".npmrc",
    ".pypirc",
}

# Sensitive file extensions
SENSITIVE_EXTENSIONS: Set[str] = {
    ".pem",
    ".key",
    ".crt",
    ".cer",
    ".p12",
    ".pfx",
    ".jks",
    ".keystore",
}


def _get_blocked_directories() -> Set[str]:
    """Get platform-specific directories that should never be written to.

    Returns:
        Set of normalized directory path strings that are blocked for writes.
    """
    blocked = set()

    if platform.system() == "Windows":
        # Windows system directories
        windir = os.environ.get("WINDIR", r"C:\Windows")
        blocked.update(
            [
                os.path.normpath(windir),
                os.path.normpath(os.path.join(windir, "System32")),
                os.path.normpath(os.path.join(windir, "SysWOW64")),
                os.path.normpath(r"C:\Program Files"),
                os.path.normpath(r"C:\Program Files (x86)"),
                os.path.normpath(r"C:\ProgramData\Microsoft"),
                os.path.normpath(
                    os.path.join(os.environ.get("USERPROFILE", ""), ".ssh")
                ),
                os.path.normpath(
                    os.path.join(
                        os.environ.get("USERPROFILE", ""),
                        "AppData",
                        "Roaming",
                        "Microsoft",
                        "Windows",
                        "Start Menu",
                        "Programs",
                        "Startup",
                    )
                ),
            ]
        )
    else:
        # Unix/macOS system directories
        home = str(Path.home())
        blocked.update(
            [
                "/bin",
                "/sbin",
                "/usr/bin",
                "/usr/sbin",
                "/usr/lib",
                "/usr/local/bin",
                "/usr/local/sbin",
                "/etc",
                "/boot",
                "/sys",
                "/proc",
                "/dev",
                "/var/run",
                os.path.join(home, ".ssh"),
                os.path.join(home, ".gnupg"),
                "/Library/LaunchDaemons",
                "/Library/LaunchAgents",
                os.path.join(home, "Library", "LaunchAgents"),
            ]
        )

    # Remove empty strings from env var failures
    blocked.discard("")
    blocked.discard(os.path.normpath(""))

    return blocked


# Pre-compute once at module load
BLOCKED_DIRECTORIES: Set[str] = _get_blocked_directories()


class PathValidator:
    """
    Validates file paths against an allowed list, with user prompting for exceptions.
    Persists allowed paths to ~/.gaia/cache/allowed_paths.json.

    Security features:
    - Allowlist-based path access control
    - Blocked directory enforcement for writes (system dirs, .ssh, etc.)
    - Sensitive file protection (.env, credentials, keys)
    - Write size limits
    - Overwrite confirmation prompting
    - Audit logging for all file mutations
    - Symlink resolution (TOCTOU prevention)
    """

    def __init__(self, allowed_paths: Optional[List[str]] = None):
        """
        Initialize PathValidator.

        Args:
            allowed_paths: Initial list of allowed paths. Defaults to [CWD].
        """
        self.allowed_paths: Set[Path] = set()

        # Add default allowed paths
        if allowed_paths:
            for p in allowed_paths:
                self.allowed_paths.add(Path(p).resolve())
        else:
            self.allowed_paths.add(Path.cwd().resolve())

        # Setup cache directory
        self.cache_dir = Path.home() / ".gaia" / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.cache_dir / "allowed_paths.json"

        # Audit log file
        self._setup_audit_logging()

        # Load persisted paths
        self._load_persisted_paths()

    def _setup_audit_logging(self):
        """Configure audit logging to file for write operations."""
        audit_log_file = self.cache_dir / "file_audit.log"
        if not audit_logger.handlers:
            handler = logging.FileHandler(str(audit_log_file), encoding="utf-8")
            handler.setFormatter(
                logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
            )
            audit_logger.addHandler(handler)
            audit_logger.setLevel(logging.INFO)

    def _load_persisted_paths(self):
        """Load allowed paths from cache file."""
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for p in data.get("paths", []):
                        try:
                            path_obj = Path(p).resolve()
                            if path_obj.exists():
                                self.allowed_paths.add(path_obj)
                        except Exception as e:
                            logger.warning(f"Invalid path in cache {p}: {e}")
            except Exception as e:
                logger.error(
                    f"Failed to load allowed paths from {self.config_file}: {e}"
                )

    def _save_persisted_path(self, path: Path):
        """Save a new allowed path to cache file."""
        try:
            data = {"paths": []}
            if self.config_file.exists():
                try:
                    with open(self.config_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    pass  # Start fresh if corrupt

            str_path = str(path)
            if str_path not in data["paths"]:
                data["paths"].append(str_path)

                with open(self.config_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)

                logger.info(f"Persisted new allowed path: {path}")
        except Exception as e:
            logger.error(f"Failed to save allowed path to {self.config_file}: {e}")

    def add_allowed_path(self, path: str) -> None:
        """
        Add a path to the allowed paths set.

        Args:
            path: Path to add to allowed paths
        """
        self.allowed_paths.add(Path(path).resolve())
        logger.debug(f"Added allowed path: {path}")

    def is_path_allowed(self, path: str, prompt_user: bool = True) -> bool:
        """
        Check if a path is allowed. If not, optionally prompt the user.

        Args:
            path: Path to check
            prompt_user: Whether to ask user for permission if path is not allowed

        Returns:
            True if allowed, False otherwise
        """
        try:
            # Resolve path using os.path.realpath to follow symlinks
            # This prevents TOCTOU attacks by resolving at check time
            real_path = Path(os.path.realpath(path)).resolve()
            real_path_str = str(real_path)

            # macOS /var symlink handling: normalize by removing /private prefix
            def normalize_macos(p: str) -> str:
                if p.startswith("/private/"):
                    return p[len("/private") :]
                return p

            norm_real_path = normalize_macos(real_path_str)

            # Check if real path is within any allowed directory
            for allowed_path in list(self.allowed_paths):
                try:
                    # Ensure allowed_path is also resolved to handle symlinks correctly
                    # IMPORTANT: Use str(allowed_path) as allowed_path might already be a Path object
                    allowed_path_str_raw = str(allowed_path)
                    res_allowed = Path(os.path.realpath(allowed_path_str_raw)).resolve()
                    allowed_path_str = str(res_allowed)
                    norm_allowed_path = normalize_macos(allowed_path_str)

                    # Robust check using string prefix on normalized paths.
                    # Append os.sep to prevent prefix attacks where
                    # /home/user/project matches /home/user/project-secrets
                    norm_allowed_with_sep = (
                        norm_allowed_path
                        if norm_allowed_path.endswith(os.sep)
                        else norm_allowed_path + os.sep
                    )
                    if (
                        norm_real_path == norm_allowed_path
                        or norm_real_path.startswith(norm_allowed_with_sep)
                    ):
                        return True

                    # Fallback to relative_to for safety
                    real_path.relative_to(res_allowed)
                    return True
                except (ValueError, RuntimeError):
                    continue

            # If we get here, path is not allowed. Prompt user?
            if prompt_user:
                return self._prompt_user_for_access(real_path)

            return False

        except Exception as e:
            logger.error(f"Error validating path {path}: {e}")
            return False

    def _prompt_user_for_access(self, path: Path) -> bool:
        """Prompt user to allow access to a path."""
        print(
            "\n⚠️  SECURITY WARNING: Agent is attempting to access a path outside allowed directories."
        )
        print(f"   Path: {path}")
        print(f"   Allowed: {[str(p) for p in self.allowed_paths]}")

        while True:
            response = (
                input("Allow this access? [y]es / [n]o / [a]lways: ").lower().strip()
            )

            if response in ["y", "yes"]:
                # Allow for this session only (add to memory but don't persist)
                # We add the specific file or directory to allowed paths
                self.allowed_paths.add(path)
                logger.info(f"User temporarily allowed access to: {path}")
                return True

            elif response in ["a", "always"]:
                # Allow and persist
                self.allowed_paths.add(path)
                self._save_persisted_path(path)
                logger.info(f"User permanently allowed access to: {path}")
                return True

            elif response in ["n", "no"]:
                logger.warning(f"User denied access to: {path}")
                return False

            print("Please answer 'y', 'n', or 'a'.")

    # ── Write Guardrails ──────────────────────────────────────────────

    def is_write_blocked(self, path: str) -> Tuple[bool, str]:
        """Check if a path is blocked for write operations.

        Checks against:
        1. System/blocked directories (Windows, /etc, .ssh, etc.)
        2. Sensitive file names (.env, credentials, keys, etc.)
        3. Sensitive file extensions (.pem, .key, .crt, etc.)

        Args:
            path: File path to check for write permission.

        Returns:
            Tuple of (is_blocked, reason). If blocked, reason explains why.
        """
        try:
            real_path = Path(os.path.realpath(path)).resolve()
            real_path_str = str(real_path)
            norm_path = os.path.normpath(real_path_str)
            file_name = real_path.name.lower()
            file_ext = real_path.suffix.lower()

            # Check blocked directories (case-insensitive on Windows)
            for blocked_dir in BLOCKED_DIRECTORIES:
                # Case-insensitive comparison on Windows, case-sensitive elsewhere
                cmp_norm = (
                    norm_path.lower() if platform.system() == "Windows" else norm_path
                )
                cmp_blocked = (
                    blocked_dir.lower()
                    if platform.system() == "Windows"
                    else blocked_dir
                )
                if cmp_norm.startswith(cmp_blocked + os.sep) or cmp_norm == cmp_blocked:
                    return (
                        True,
                        f"Write blocked: '{real_path}' is inside protected "
                        f"system directory '{blocked_dir}'",
                    )

            # Check sensitive file names
            if file_name in {s.lower() for s in SENSITIVE_FILE_NAMES}:
                return (
                    True,
                    f"Write blocked: '{real_path.name}' is a sensitive file "
                    f"(credentials/keys/secrets). Writing to it is not allowed.",
                )

            # Check sensitive extensions
            if file_ext in SENSITIVE_EXTENSIONS:
                return (
                    True,
                    f"Write blocked: files with extension '{file_ext}' are "
                    f"sensitive (certificates/keys). Writing is not allowed.",
                )

            return (False, "")

        except Exception as e:
            logger.error(f"Error checking write block for {path}: {e}")
            # Fail-closed: block if we can't determine safety
            return (True, f"Write blocked: unable to validate path safety: {e}")

    def validate_write(
        self,
        path: str,
        content_size: int = 0,
        prompt_user: bool = True,
    ) -> Tuple[bool, str]:
        """Comprehensive write validation combining all guardrails.

        Checks in order:
        1. Path is in allowed paths (allowlist)
        2. Path is not in blocked directories (denylist)
        3. File is not a sensitive file
        4. Content size is within limits
        5. If file exists, prompts for overwrite confirmation

        Args:
            path: File path to validate for writing.
            content_size: Size of content to write in bytes (0 to skip check).
            prompt_user: Whether to prompt the user for confirmations.

        Returns:
            Tuple of (is_allowed, reason). If not allowed, reason explains why.
        """
        # 1. Check allowlist
        if not self.is_path_allowed(path, prompt_user=prompt_user):
            return (False, f"Access denied: '{path}' is not in allowed paths")

        # 2. Check blocked directories and sensitive files
        is_blocked, reason = self.is_write_blocked(path)
        if is_blocked:
            return (False, reason)

        # 3. Check content size
        if content_size > MAX_WRITE_SIZE_BYTES:
            size_mb = content_size / (1024 * 1024)
            limit_mb = MAX_WRITE_SIZE_BYTES / (1024 * 1024)
            return (
                False,
                f"Write blocked: content size ({size_mb:.1f} MB) exceeds "
                f"maximum allowed size ({limit_mb:.0f} MB)",
            )

        # 4. Overwrite confirmation for existing files
        real_path = Path(os.path.realpath(path)).resolve()
        if real_path.exists() and prompt_user:
            try:
                existing_size = real_path.stat().st_size
                if not self._prompt_overwrite(real_path, existing_size):
                    return (False, f"User declined to overwrite '{real_path}'")
            except OSError:
                pass  # File may have been deleted between check and prompt

        return (True, "")

    def _prompt_overwrite(self, path: Path, existing_size: int) -> bool:
        """Prompt user before overwriting an existing file.

        Args:
            path: Path to the existing file.
            existing_size: Current file size in bytes.

        Returns:
            True if user approves overwrite, False otherwise.
        """
        size_str = _format_size(existing_size)
        print(f"\n⚠️  File already exists: {path} ({size_str})")

        while True:
            response = input("Overwrite this file? [y]es / [n]o: ").lower().strip()
            if response in ["y", "yes"]:
                logger.info(f"User approved overwrite of: {path}")
                return True
            elif response in ["n", "no"]:
                logger.info(f"User declined overwrite of: {path}")
                return False
            print("Please answer 'y' or 'n'.")

    def create_backup(self, path: str) -> Optional[str]:
        """Create a timestamped backup of a file before modification.

        Args:
            path: Path to the file to back up.

        Returns:
            Backup file path if successful, None if file doesn't exist or backup failed.
        """
        try:
            real_path = Path(os.path.realpath(path)).resolve()
            if not real_path.exists():
                return None

            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = real_path.with_name(
                f"{real_path.stem}.{timestamp}.bak{real_path.suffix}"
            )

            shutil.copy2(str(real_path), str(backup_path))
            audit_logger.info(f"BACKUP | {real_path} -> {backup_path}")
            logger.debug(f"Created backup: {backup_path}")
            return str(backup_path)
        except Exception as e:
            logger.warning(f"Failed to create backup of {path}: {e}")
            return None

    def audit_write(
        self, operation: str, path: str, size: int, status: str, detail: str = ""
    ) -> None:
        """Log a file write operation to the audit log.

        Args:
            operation: Type of operation (write, edit, delete, etc.)
            path: File path that was modified.
            size: Size of content written in bytes.
            status: Result status (success, denied, error).
            detail: Additional detail about the operation.
        """
        size_str = _format_size(size) if size > 0 else "N/A"
        msg = f"{operation.upper()} | {status} | {path} | {size_str}"
        if detail:
            msg += f" | {detail}"

        if status == "success":
            audit_logger.info(msg)
        elif status == "denied":
            audit_logger.warning(msg)
        else:
            audit_logger.error(msg)


def _format_size(size_bytes: int) -> str:
    """Format byte count to human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"
