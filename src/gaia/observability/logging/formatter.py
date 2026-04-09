"""
Structured logging module for GAIA observability.

This module provides structured logging capabilities including:
- JSON log formatting for machine-parseable logs
- Log sink abstraction for multiple output destinations
- Context-aware log filtering and enrichment

Example:
    >>> from gaia.observability.logging import JSONFormatter, ConsoleSink
    >>> import logging
    >>>
    >>> formatter = JSONFormatter(service_name="gaia-api")
    >>> sink = ConsoleSink()
    >>> logger = sink.get_logger("my.logger")
    >>> logger.setFormatter(formatter)
    >>> logger.info("Operation completed", extra={"duration_ms": 125})
"""

import json
import logging
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, TextIO
from pathlib import Path
import threading


class JSONFormatter(logging.Formatter):
    """
    JSON log formatter for structured logging.

    Formats log records as JSON objects with consistent field naming
    for easy parsing by log aggregation systems.

    Attributes:
        service_name: Name of the service for all log entries
        include_timestamps: Whether to include timestamp fields
        timestamp_format: Format string for timestamps

    Example:
        >>> formatter = JSONFormatter(service_name="gaia-api")
        >>> logger = logging.getLogger("gaia")
        >>> logger.setFormatter(formatter)
        >>> logger.info("Request received", extra={"request_id": "123"})
        # Output: {"service": "gaia-api", "level": "INFO", ...}
    """

    def __init__(
        self,
        service_name: str = "gaia",
        include_timestamps: bool = True,
        timestamp_format: str = "%Y-%m-%dT%H:%M:%S.%fZ",
    ) -> None:
        """
        Initialize JSON formatter.

        Args:
            service_name: Service name to include in all logs
            include_timestamps: Include timestamp fields
            timestamp_format: strftime format for timestamps

        Example:
            >>> formatter = JSONFormatter(
            ...     service_name="my-service",
            ...     timestamp_format="%Y-%m-%d %H:%M:%S"
            ... )
        """
        super().__init__()
        self.service_name = service_name
        self.include_timestamps = include_timestamps
        self.timestamp_format = timestamp_format

    def format(self, record: logging.LogRecord) -> str:
        """
        Format log record as JSON.

        Args:
            record: Log record to format

        Returns:
            JSON string representation of log entry

        Example:
            >>> formatter = JSONFormatter(service_name="test")
            >>> record = logging.LogRecord(
            ...     name="test", level=logging.INFO, pathname="",
            ...     lineno=0, msg="Hello", args=(), exc_info=None
            ... )
            >>> json_output = formatter.format(record)
        """
        log_data: Dict[str, Any] = {
            "service": self.service_name,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if self.include_timestamps:
            log_data["timestamp"] = datetime.fromtimestamp(
                record.created, tz=None
            ).strftime(self.timestamp_format)
            log_data["timestamp_unix"] = record.created

        # Add location info if available
        if record.pathname:
            log_data["location"] = {
                "file": Path(record.pathname).name,
                "line": record.lineno,
                "function": record.funcName,
            }

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "traceback": self.formatException(record.exc_info) if record.exc_info else None,
            }

        # Add extra fields from record
        extra_fields = self._get_extra_fields(record)
        if extra_fields:
            log_data["extra"] = extra_fields

        # Add trace context if available
        trace_context = self._get_trace_context(record)
        if trace_context:
            log_data["trace"] = trace_context

        return json.dumps(log_data, default=str, ensure_ascii=False)

    def _get_extra_fields(self, record: logging.LogRecord) -> Dict[str, Any]:
        """Extract extra fields from log record."""
        extra = {}
        reserved_fields = {
            "name", "msg", "args", "created", "filename", "funcName",
            "levelname", "levelno", "lineno", "module", "msecs",
            "pathname", "process", "processName", "relativeCreated",
            "stack_info", "exc_info", "exc_text", "thread", "threadName",
            "message", "asctime",
        }

        for key, value in record.__dict__.items():
            if key not in reserved_fields:
                extra[key] = value

        return extra

    def _get_trace_context(self, record: logging.LogRecord) -> Optional[Dict[str, str]]:
        """Extract trace context from log record."""
        trace_id = getattr(record, "trace_id", None)
        span_id = getattr(record, "span_id", None)

        if trace_id or span_id:
            context = {}
            if trace_id:
                context["trace_id"] = trace_id
            if span_id:
                context["span_id"] = span_id
            return context
        return None


class LogSink:
    """
    Abstract base class for log sinks.

    Log sinks define where log output is written. Implementations
    can write to console, files, or external services.

    Example:
        >>> class CustomSink(LogSink):
        ...     def write(self, message: str) -> None:
        ...         # Custom write logic
        ...         pass
        ...
        ...     def close(self) -> None:
        ...         # Cleanup logic
        ...         pass
    """

    def write(self, message: str) -> None:
        """
        Write log message to sink.

        Args:
            message: Formatted log message to write
        """
        pass

    def close(self) -> None:
        """Close the sink and release resources."""
        pass

    def get_logger(self, name: str) -> logging.Logger:
        """
        Get a logger configured for this sink.

        Args:
            name: Logger name

        Returns:
            Configured logger instance
        """
        logger = logging.getLogger(name)
        handler = logging.StreamHandler(self._get_stream())
        logger.addHandler(handler)
        return logger

    def _get_stream(self) -> TextIO:
        """Get the underlying stream for the handler."""
        return sys.stdout


class ConsoleSink(LogSink):
    """
    Log sink that writes to console (stdout/stderr).

    Example:
        >>> sink = ConsoleSink()
        >>> logger = sink.get_logger("my.logger")
        >>> logger.info("Console output")
    """

    def __init__(self, use_stderr: bool = False) -> None:
        """
        Initialize console sink.

        Args:
            use_stderr: Write to stderr instead of stdout

        Example:
            >>> sink = ConsoleSink(use_stderr=True)
        """
        self.use_stderr = use_stderr
        self._lock = threading.Lock()

    def write(self, message: str) -> None:
        """Write message to console."""
        with self._lock:
            stream = sys.stderr if self.use_stderr else sys.stdout
            stream.write(message + "\n")
            stream.flush()

    def close(self) -> None:
        """Console sink doesn't need cleanup."""
        pass

    def _get_stream(self) -> TextIO:
        """Get appropriate console stream."""
        return sys.stderr if self.use_stderr else sys.stdout


class FileSink(LogSink):
    """
    Log sink that writes to a file.

    Supports automatic file rotation based on size or time.

    Example:
        >>> sink = FileSink("logs/app.log", max_bytes=10*1024*1024)
        >>> logger = sink.get_logger("my.logger")
        >>> logger.info("File output")
    """

    def __init__(
        self,
        filepath: str,
        max_bytes: int = 0,
        backup_count: int = 0,
        encoding: str = "utf-8",
    ) -> None:
        """
        Initialize file sink.

        Args:
            filepath: Path to log file
            max_bytes: Max file size before rotation (0 = no rotation)
            backup_count: Number of backup files to keep
            encoding: File encoding

        Example:
            >>> sink = FileSink(
            ...     "logs/app.log",
            ...     max_bytes=100*1024*1024,  # 100MB
            ...     backup_count=5
            ... )
        """
        self.filepath = Path(filepath)
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.encoding = encoding
        self._file: Optional[TextIO] = None
        self._lock = threading.Lock()
        self._current_size = 0

        # Ensure directory exists
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self._open_file()

    def _open_file(self) -> None:
        """Open the log file."""
        mode = "a" if self.filepath.exists() else "w"
        self._file = open(self.filepath, mode, encoding=self.encoding)
        self._current_size = self.filepath.stat().st_size if self.filepath.exists() else 0

    def _rotate_if_needed(self) -> None:
        """Rotate log file if it exceeds max size."""
        if self.max_bytes > 0 and self._current_size >= self.max_bytes:
            self._rotate()

    def _rotate(self) -> None:
        """Perform log rotation."""
        if self._file:
            self._file.close()

        # Rotate existing files
        for i in range(self.backup_count, 0, -1):
            src = self.filepath.with_suffix(f".log.{i-1}" if i > 1 else "")
            dst = self.filepath.with_suffix(f".log.{i}")
            if src.exists():
                if dst.exists():
                    dst.unlink()
                src.rename(dst)

        # Create new file
        self._open_file()

    def write(self, message: str) -> None:
        """Write message to file."""
        with self._lock:
            if self._file is None:
                self._open_file()

            self._rotate_if_needed()

            message_bytes = (message + "\n").encode(self.encoding)
            self._file.write(message + "\n")
            self._file.flush()
            self._current_size += len(message_bytes)

    def close(self) -> None:
        """Close the log file."""
        with self._lock:
            if self._file:
                self._file.close()
                self._file = None

    def _get_stream(self) -> TextIO:
        """Get the file stream."""
        if self._file is None:
            self._open_file()
        return self._file


class MultiSink(LogSink):
    """
    Log sink that writes to multiple sinks simultaneously.

    Example:
        >>> console = ConsoleSink()
        >>> file_sink = FileSink("logs/app.log")
        >>> multi = MultiSink([console, file_sink])
        >>> logger = multi.get_logger("my.logger")
        >>> logger.info("Output to all sinks")
    """

    def __init__(self, sinks: List[LogSink]) -> None:
        """
        Initialize multi sink.

        Args:
            sinks: List of sinks to write to

        Example:
            >>> multi = MultiSink([ConsoleSink(), FileSink("app.log")])
        """
        self.sinks = sinks

    def write(self, message: str) -> None:
        """Write message to all sinks."""
        for sink in self.sinks:
            try:
                sink.write(message)
            except Exception:
                pass  # Don't let one sink failure affect others

    def close(self) -> None:
        """Close all sinks."""
        for sink in self.sinks:
            try:
                sink.close()
            except Exception:
                pass


class ContextFilter(logging.Filter):
    """
    Logging filter that adds trace context to log records.

    This filter automatically injects trace_id and span_id from
    the current trace context into log records.

    Example:
        >>> filter = ContextFilter()
        >>> logger = logging.getLogger("my.logger")
        >>> logger.addFilter(filter)
    """

    def __init__(self, context_getter=None) -> None:
        """
        Initialize context filter.

        Args:
            context_getter: Function to get current trace context
                           (default: uses global context manager)
        """
        super().__init__()
        self.context_getter = context_getter

    def filter(self, record: logging.LogRecord) -> bool:
        """
        Add trace context to log record.

        Args:
            record: Log record to enrich

        Returns:
            True to allow record to pass
        """
        try:
            from ..tracing.trace_context import get_current_trace_context

            context = get_current_trace_context()
            if context:
                record.trace_id = context.trace_id
                record.span_id = context.span_id
        except ImportError:
            pass

        return True


def setup_logging(
    service_name: str = "gaia",
    level: str = "INFO",
    log_format: str = "json",
    output: str = "console",
    filepath: Optional[str] = None,
) -> logging.Logger:
    """
    Set up structured logging for a service.

    Args:
        service_name: Service name for log entries
        level: Logging level (DEBUG, INFO, WARNING, ERROR)
        log_format: Log format ("json" or "text")
        output: Output destination ("console", "file", or "both")
        filepath: File path if output is "file" or "both"

    Returns:
        Root logger for the service

    Example:
        >>> logger = setup_logging(
        ...     service_name="gaia-api",
        ...     level="DEBUG",
        ...     log_format="json",
        ...     output="both",
        ...     filepath="logs/gaia.log"
        ... )
        >>> logger.info("Service started")
    """
    logger = logging.getLogger(service_name)
    logger.setLevel(getattr(logging, level.upper()))

    # Clear existing handlers
    logger.handlers.clear()

    # Create formatter
    if log_format == "json":
        formatter = JSONFormatter(service_name=service_name)
    else:
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )

    # Create sinks
    sinks: List[LogSink] = []

    if output in ("console", "both"):
        console_sink = ConsoleSink()
        sinks.append(console_sink)

    if output in ("file", "both") and filepath:
        file_sink = FileSink(filepath)
        sinks.append(file_sink)

    # Add handlers
    for sink in sinks:
        handler = logging.StreamHandler(sink._get_stream())
        handler.setFormatter(formatter)
        handler.addFilter(ContextFilter())
        logger.addHandler(handler)

    return logger
