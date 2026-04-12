"""
Security module for GAIA workspace sandboxing and data protection.

Provides secure file operations with hard filesystem boundaries,
path traversal prevention, audit logging, and data protection.

Example:
    >>> from gaia.security import WorkspacePolicy, SecurityValidator, DataProtection
    >>> policy = WorkspacePolicy(allowed_paths=["/workspace"])
    >>> policy.write_file("file.txt", "content")
    >>> content = policy.read_file("file.txt")
    >>> protector = DataProtection()
    >>> encrypted = protector.encrypt("sensitive data")
"""

from gaia.security.workspace import WorkspacePolicy, WorkspaceSecurityError
from gaia.security.validator import SecurityValidator, SecurityAuditEvent
from gaia.security.data_protection import (
    DataProtection,
    EncryptionManager,
    EncryptionError,
    PIIDetector,
    PIIMatch,
    PIIType,
    CRYPTOGRAPHY_AVAILABLE,
)

# PathValidator — re-export from its home in the flat module.
# Since the package shadows gaia/security.py, we define it here directly.
import json
import logging as _logging
import os as _os
from pathlib import Path as _Path
from typing import List as _List, Optional as _Optional, Set as _Set

_pv_logger = _logging.getLogger(__name__)


class PathValidator:
    """
    Validates file paths against an allowed list, with user prompting for exceptions.
    Persists allowed paths to ~/.gaia/cache/allowed_paths.json.
    """

    def __init__(self, allowed_paths: _Optional[_List[str]] = None):
        self.allowed_paths: _Set[_Path] = set()
        if allowed_paths:
            for p in allowed_paths:
                self.allowed_paths.add(_Path(p).resolve())
        else:
            self.allowed_paths.add(_Path.cwd().resolve())
        self.cache_dir = _Path.home() / ".gaia" / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.cache_dir / "allowed_paths.json"
        self._load_persisted_paths()

    def _load_persisted_paths(self):
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for p in data.get("paths", []):
                        try:
                            path_obj = _Path(p).resolve()
                            if path_obj.exists():
                                self.allowed_paths.add(path_obj)
                        except Exception as e:
                            _pv_logger.warning(f"Invalid path in cache {p}: {e}")
            except Exception as e:
                _pv_logger.error(f"Failed to load allowed paths from {self.config_file}: {e}")

    def _save_persisted_path(self, path: _Path):
        try:
            data = {"paths": []}
            if self.config_file.exists():
                try:
                    with open(self.config_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    pass
            str_path = str(path)
            if str_path not in data["paths"]:
                data["paths"].append(str_path)
                with open(self.config_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                _pv_logger.info(f"Persisted new allowed path: {path}")
        except Exception as e:
            _pv_logger.error(f"Failed to save allowed path to {self.config_file}: {e}")

    def add_allowed_path(self, path: str) -> None:
        self.allowed_paths.add(_Path(path).resolve())
        _pv_logger.debug(f"Added allowed path: {path}")

    def is_path_allowed(self, path: str, prompt_user: bool = True) -> bool:
        try:
            real_path = _Path(_os.path.realpath(path)).resolve()
            real_path_str = str(real_path)

            def normalize_macos(p: str) -> str:
                if p.startswith("/private/"):
                    return p[len("/private"):]
                return p

            norm_real_path = normalize_macos(real_path_str)

            for allowed_path in list(self.allowed_paths):
                try:
                    allowed_path_str_raw = str(allowed_path)
                    res_allowed = _Path(_os.path.realpath(allowed_path_str_raw)).resolve()
                    allowed_path_str = str(res_allowed)
                    norm_allowed_path = normalize_macos(allowed_path_str)

                    norm_allowed_with_sep = (
                        norm_allowed_path
                        if norm_allowed_path.endswith(_os.sep)
                        else norm_allowed_path + _os.sep
                    )
                    if norm_real_path == norm_allowed_path or norm_real_path.startswith(norm_allowed_with_sep):
                        return True
                    real_path.relative_to(res_allowed)
                    return True
                except (ValueError, RuntimeError):
                    continue

            if prompt_user:
                return self._prompt_user_for_access(real_path)
            return False
        except Exception as e:
            _pv_logger.error(f"Error validating path {path}: {e}")
            return False

    def _prompt_user_for_access(self, path: _Path) -> bool:
        print(
            "\n\u26a0\ufe0f  SECURITY WARNING: Agent is attempting to access a path outside allowed directories."
        )
        print(f"   Path: {path}")
        print(f"   Allowed: {[str(p) for p in self.allowed_paths]}")
        while True:
            response = input("Allow this access? [y]es / [n]o / [a]lways: ").lower().strip()
            if response in ["y", "yes"]:
                self.allowed_paths.add(path)
                _pv_logger.info(f"User temporarily allowed access to: {path}")
                return True
            elif response in ["a", "always"]:
                self.allowed_paths.add(path)
                self._save_persisted_path(path)
                _pv_logger.info(f"User permanently allowed access to: {path}")
                return True
            elif response in ["n", "no"]:
                _pv_logger.warning(f"User denied access to: {path}")
                return False
            print("Please answer 'y', 'n', or 'a'.")


__all__ = [
    "WorkspacePolicy",
    "WorkspaceSecurityError",
    "SecurityValidator",
    "SecurityAuditEvent",
    "DataProtection",
    "EncryptionManager",
    "EncryptionError",
    "PIIDetector",
    "PIIMatch",
    "PIIType",
    "CRYPTOGRAPHY_AVAILABLE",
    "PathValidator",
]
