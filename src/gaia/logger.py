# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import logging
import sys
import warnings
from pathlib import Path


class GaiaLogger:
    def __init__(self, log_file="gaia.log"):
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

        # Create and configure handlers
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(console_formatter)

        file_handler = logging.FileHandler(self.log_file)
        file_handler.setFormatter(file_formatter)

        # Configure root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(self.default_level)
        root_logger.addHandler(console_handler)
        root_logger.addHandler(file_handler)

        # Add color filter to console handler
        console_handler.addFilter(self.add_color_filter)

        # Default levels for different modules
        self.default_levels = {
            "gaia.agents": logging.INFO,
            "gaia.interface": logging.INFO,
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
        return not "words count mismatch" in message

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
        if name in self.loggers:
            self.loggers[name].setLevel(level)
        else:
            self.default_levels[name] = level


# Create a global instance
log_manager = GaiaLogger()


# Convenience function to get a logger
def get_logger(name):
    return log_manager.get_logger(name)
