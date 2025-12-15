# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Security utilities for GAIA.
Handles path validation, user prompting, and persistent allow-lists.
"""

import json
import logging
import os
from pathlib import Path
from typing import List, Optional, Set

logger = logging.getLogger(__name__)


class PathValidator:
    """
    Validates file paths against an allowed list, with user prompting for exceptions.
    Persists allowed paths to ~/.gaia/cache/allowed_paths.json.
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

        # Load persisted paths
        self._load_persisted_paths()

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

            # Check if real path is within any allowed directory
            for allowed_path in self.allowed_paths:
                try:
                    # is_relative_to requires Python 3.9+, use alternative for compatibility
                    real_path.relative_to(allowed_path)
                    return True
                except ValueError:
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
