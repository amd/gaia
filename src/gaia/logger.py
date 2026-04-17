# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import logging
import subprocess
import sys
import warnings
from pathlib import Path


def configure_console_encoding():
    """Configure console encoding to support Unicode characters on Windows."""
    if sys.platform.startswith("win"):
        try:
            # Use reconfigure() to change encoding in-place without creating
            # new TextIOWrapper instances. Creating new wrappers detaches the
            # underlying buffer from the original stream, which breaks pytest's
            # capture mechanism (causes "I/O operation on closed file" errors).
            for stream_name in ("stdout", "stderr"):
                stream = getattr(sys, stream_name)
                if stream and not stream.closed and hasattr(stream, "reconfigure"):
                    stream.reconfigure(encoding="utf-8", errors="replace")

            # Also try to set the console code page to UTF-8
            try:
                subprocess.run(
                    ["chcp", "65001"], capture_output=True, shell=True, check=False
                )
            except (subprocess.SubprocessError, OSError, FileNotFoundError):
                pass  # Ignore if chcp command fails

        except Exception:
            # If configuration fails, fall back to original streams
            pass


class GaiaLogger:
    def __init__(self, log_file=None):
        # Configure console encoding for Unicode support first
        configure_console_encoding()

        # Default to the user's ~/.gaia directory so we don't litter the
        # current working directory with gaia.log files and don't crash
        # when CWD is not writable (e.g. /, system dirs, read-only mounts).
        if log_file is None:
            log_file = Path.home() / ".gaia" / "gaia.log"
            try:
                log_file.parent.mkdir(parents=True, exist_ok=True)
            except (PermissionError, OSError):
                # Home directory not writable either -- fall through to
                # the file-handler try/except below which falls back to
                # a tempfile.
                pass

        self.log_file = Path(log_file)
        self.loggers = {}

        # Filter warnings
        warnings.filterwarnings(
            "ignore", message="dropout option adds dropout after all but last"
        )
        warnings.filterwarnings(
            "ignore", message="torch.nn.utils.weight_norm is deprecated"
        )

        # Define color codes
        self.colors = {
            "DEBUG": "\033[37m",  # White
            "INFO": "\033[37m",  # White
            "WARNING": "\033[33m",  # Yellow
            "ERROR": "\033[31m",  # Red
            "CRITICAL": "\033[41m",  # Red background
            "RESET": "\033[0m",  # Reset color
        }

        # Base configuration
        self.default_level = logging.INFO

        # Create colored formatter for console and regular formatter for file
        console_formatter = logging.Formatter(
            "%(asctime)s | %(color)s%(levelname)s%(reset)s | %(name)s.%(funcName)s | %(filename)s:%(lineno)d | %(message)s",
            datefmt="[%Y-%m-%d %H:%M:%S]",
        )
        file_formatter = logging.Formatter(
            "[%(asctime)s] | %(levelname)s | %(name)s.%(funcName)s | %(filename)s:%(lineno)d | %(message)s"
        )

        # Create and configure handlers with Unicode support
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(console_formatter)

        # Configure file handler with UTF-8 encoding.
        # Fall back gracefully if the log file can't be opened for writing
        # (e.g. permission denied, read-only filesystem). We try a couple
        # of fallback locations before giving up and going console-only.
        file_handler = None
        try:
            file_handler = logging.FileHandler(self.log_file, encoding="utf-8")
            file_handler.setFormatter(file_formatter)
        except (PermissionError, OSError) as primary_err:
            fallback_candidates = []

            home_fallback = Path.home() / ".gaia" / "gaia.log"
            if Path(self.log_file).resolve() != home_fallback.resolve():
                fallback_candidates.append(home_fallback)

            try:
                import tempfile

                fallback_candidates.append(Path(tempfile.gettempdir()) / "gaia.log")
            except Exception:
                pass

            print(
                f"[gaia] Cannot write to {self.log_file} ({primary_err}).",
                file=sys.stderr,
            )

            for candidate in fallback_candidates:
                try:
                    candidate.parent.mkdir(parents=True, exist_ok=True)
                    file_handler = logging.FileHandler(candidate, encoding="utf-8")
                    file_handler.setFormatter(file_formatter)
                    print(
                        f"[gaia] Writing logs to: {candidate}",
                        file=sys.stderr,
                    )
                    self.log_file = candidate
                    break
                except (PermissionError, OSError):
                    continue

            if file_handler is None:
                print(
                    "[gaia] No writable log location found; "
                    "continuing with console logging only.",
                    file=sys.stderr,
                )

        # Configure root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(self.default_level)
        root_logger.addHandler(console_handler)
        if file_handler is not None:
            root_logger.addHandler(file_handler)

        # Add color filter to console handler
        console_handler.addFilter(self.add_color_filter)

        # Default levels for different modules
        self.default_levels = {
            "gaia.agents": logging.INFO,
            "gaia.llm": logging.INFO,
        }

        # Suppress specific aiohttp.access log messages
        aiohttp_access_logger = logging.getLogger("aiohttp.access")
        aiohttp_access_logger.addFilter(self.filter_aiohttp_access)

        # Suppress specific datasets log messages
        datasets_logger = logging.getLogger("datasets")
        datasets_logger.addFilter(self.filter_datasets)

        # Suppress specific httpx log messages
        httpx_logger = logging.getLogger("httpx")
        httpx_logger.addFilter(self.filter_httpx)

        # Suppress phonemizer warnings
        phonemizer_logger = logging.getLogger("phonemizer")
        phonemizer_logger.addFilter(self.filter_phonemizer)

    def add_color_filter(self, record):
        record.color = self.colors.get(record.levelname, "")
        record.reset = self.colors["RESET"]
        return True

    def filter_aiohttp_access(self, record):
        return not (
            record.name == "aiohttp.access"
            and "POST /stream_to_ui" in record.getMessage()
        )

    def filter_datasets(self, record):
        return not (
            "PyTorch version" in record.getMessage()
            and "available." in record.getMessage()
        )

    def filter_httpx(self, record):
        message = record.getMessage()
        return not ("HTTP Request:" in message and "HTTP/1.1 200 OK" in message)

    def filter_phonemizer(self, record):
        message = record.getMessage()
        return "words count mismatch" not in message

    def get_logger(self, name):
        if name not in self.loggers:
            logger = logging.getLogger(name)
            level = self._get_level_for_module(name)
            logger.setLevel(level)
            self.loggers[name] = logger
        return self.loggers[name]

    def _get_level_for_module(self, name):
        for module, level in self.default_levels.items():
            if module in name:
                return level
        return self.default_level

    def set_level(self, name, level):
        """Set logging level for a logger name or prefix.

        If name matches an existing logger exactly, update that logger.
        Otherwise, set as default_level for future loggers matching the prefix.
        Also updates all existing loggers that start with the given name prefix.
        """
        # Update exact match if it exists
        if name in self.loggers:
            self.loggers[name].setLevel(level)

        # Update all existing loggers that start with this prefix
        for logger_name, logger in self.loggers.items():
            if logger_name.startswith(name + ".") or logger_name == name:
                logger.setLevel(level)

        # Set as default for future loggers matching this prefix
        self.default_levels[name] = level


# Create a global instance
log_manager = GaiaLogger()


# Convenience function to get a logger
def get_logger(name):
    return log_manager.get_logger(name)
