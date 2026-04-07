"""
Structured logging module for GAIA observability.

This module provides structured logging capabilities including:
- JSON log formatting for machine-parseable logs
- Log sink abstraction for multiple output destinations
- Context-aware log filtering and enrichment

Example:
    >>> from gaia.observability.logging import (
    ...     JSONFormatter, ConsoleSink, setup_logging
    ... )
    >>>
    >>> logger = setup_logging(service_name="gaia-api")
    >>> logger.info("Service started")
"""

from .formatter import (
    JSONFormatter,
    LogSink,
    ConsoleSink,
    FileSink,
    MultiSink,
    ContextFilter,
    setup_logging,
)

__all__ = [
    "JSONFormatter",
    "LogSink",
    "ConsoleSink",
    "FileSink",
    "MultiSink",
    "ContextFilter",
    "setup_logging",
]
