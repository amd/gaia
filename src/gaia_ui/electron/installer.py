# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Silent installer utilities for the GAIA Electron UI.

This module provides a minimal implementation of a silent/unattended install
mode. The function is deliberately lightweight – it records the supplied
configuration to a log file and returns a boolean status. Real installation
steps can be added later without changing the public interface.
"""

import json
import logging
from pathlib import Path
from typing import Any, Mapping

__all__ = ["silent_install"]


def _setup_logger(log_path: Path) -> logging.Logger:
    """Create a logger that writes to *log_path*.

    The logger is configured with a simple ``INFO`` format and replaces any
    existing handlers to avoid duplicate entries when the function is called
    multiple times.
    """
    logger = logging.getLogger("gaia_installer")
    logger.setLevel(logging.INFO)
    # Ensure a clean handler list for repeated calls.
    logger.handlers.clear()
    handler = logging.FileHandler(log_path, encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


def silent_install(config: Mapping[str, Any], log_file: str | Path) -> bool:
    """Run a silent installation using *config* and write a log to *log_file*.

    Args:
        config: Mapping containing installation options (e.g., target
            directory, feature toggles, etc.).
        log_file: Destination path for the installation log. Parent directories
            are created automatically.

    Returns:
        ``True`` if the installation completed without raising an exception,
        otherwise ``False``.
    """
    log_path = Path(log_file).expanduser().resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = _setup_logger(log_path)
    logger.info("Starting silent installation")
    try:
        # Record the configuration for troubleshooting.
        logger.info("Configuration: %s", json.dumps(config, indent=2, sort_keys=True))
        # TODO: Insert real installation logic here, using the supplied
        # configuration. This placeholder simply logs success.
        logger.info("Silent installation completed successfully")
        return True
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Silent installation failed: %s", exc)
        return False
