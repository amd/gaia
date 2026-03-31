"""
GAIA Logging Module

Provides structured logging configuration for the GAIA pipeline system.
Supports JSON logging for production environments and colored console output
for development.
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


class LogFormatter(logging.Formatter):
    """
    Custom formatter for GAIA logs.

    Provides structured output with:
    - Timestamp
    - Log level
    - Component/Module
    - Pipeline/Loop context (when available)
    - Message
    - Extra fields
    """

    # ANSI color codes for development
    COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"

    def __init__(
        self,
        use_colors: bool = True,
        include_extra: bool = True,
        json_format: bool = False,
    ):
        super().__init__()
        self.use_colors = use_colors and sys.stderr.isatty()
        self.include_extra = include_extra
        self.json_format = json_format

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record."""
        if self.json_format:
            return self._format_json(record)
        return self._format_text(record)

    def _format_text(self, record: logging.LogRecord) -> str:
        """Format as human-readable text."""
        timestamp = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")

        # Level with optional color
        level = record.levelname
        if self.use_colors:
            level = f"{self.COLORS.get(level, '')}{level}{self.RESET}"

        # Build base message
        parts = [
            f"[{timestamp}]",
            f"[{level}]",
            f"[{record.name}]",
        ]

        # Add context if available
        context = self._extract_context(record)
        if context:
            parts.append(f"[{context}]")

        parts.append(record.getMessage())

        # Add extra fields
        if self.include_extra:
            extra = self._extract_extra(record)
            if extra:
                parts.append(f"({extra})")

        return " ".join(parts)

    def _format_json(self, record: logging.LogRecord) -> str:
        """Format as JSON for structured logging."""
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add context
        context = self._extract_context(record)
        if context:
            log_entry["context"] = context

        # Add extra fields
        extra = self._extract_extra(record)
        if extra:
            log_entry["extra"] = extra

        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry)

    def _extract_context(self, record: logging.LogRecord) -> Optional[str]:
        """Extract pipeline/loop context from record."""
        context_parts = []

        pipeline_id = getattr(record, "pipeline_id", None)
        if pipeline_id:
            context_parts.append(f"pipeline:{pipeline_id}")

        loop_id = getattr(record, "loop_id", None)
        if loop_id:
            context_parts.append(f"loop:{loop_id}")

        phase = getattr(record, "phase", None)
        if phase:
            context_parts.append(f"phase:{phase}")

        return ",".join(context_parts) if context_parts else None

    def _extract_extra(self, record: logging.LogRecord) -> Optional[str]:
        """Extract extra fields from record."""
        skip_keys = {
            "pipeline_id",
            "loop_id",
            "phase",
            "agent_id",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "lineno",
            "funcName",
            "created",
        }

        extra_items = []
        for key, value in record.__dict__.items():
            if key not in skip_keys and not key.startswith("_"):
                extra_items.append(f"{key}={value}")

        return ", ".join(extra_items) if extra_items else None


class GAIALogger:
    """
    Wrapper class for GAIA logging with context support.

    Allows attaching pipeline/loop context to log messages.
    """

    def __init__(self, logger: logging.Logger):
        self._logger = logger

    def _add_context(
        self,
        msg: str,
        pipeline_id: Optional[str] = None,
        loop_id: Optional[str] = None,
        phase: Optional[str] = None,
        agent_id: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build context for log message."""
        context: Dict[str, Any] = {}

        if pipeline_id:
            context["pipeline_id"] = pipeline_id
        if loop_id:
            context["loop_id"] = loop_id
        if phase:
            context["phase"] = phase
        if agent_id:
            context["agent_id"] = agent_id
        if extra:
            context.update(extra)

        return context

    def debug(
        self,
        msg: str,
        pipeline_id: Optional[str] = None,
        loop_id: Optional[str] = None,
        phase: Optional[str] = None,
        agent_id: Optional[str] = None,
        **extra: Any,
    ) -> None:
        """Log debug message with context."""
        ctx = self._add_context(msg, pipeline_id, loop_id, phase, agent_id, extra)
        self._logger.debug(msg, extra=ctx)

    def info(
        self,
        msg: str,
        pipeline_id: Optional[str] = None,
        loop_id: Optional[str] = None,
        phase: Optional[str] = None,
        agent_id: Optional[str] = None,
        **extra: Any,
    ) -> None:
        """Log info message with context."""
        ctx = self._add_context(msg, pipeline_id, loop_id, phase, agent_id, extra)
        self._logger.info(msg, extra=ctx)

    def warning(
        self,
        msg: str,
        pipeline_id: Optional[str] = None,
        loop_id: Optional[str] = None,
        phase: Optional[str] = None,
        agent_id: Optional[str] = None,
        **extra: Any,
    ) -> None:
        """Log warning message with context."""
        ctx = self._add_context(msg, pipeline_id, loop_id, phase, agent_id, extra)
        self._logger.warning(msg, extra=ctx)

    def error(
        self,
        msg: str,
        pipeline_id: Optional[str] = None,
        loop_id: Optional[str] = None,
        phase: Optional[str] = None,
        agent_id: Optional[str] = None,
        **extra: Any,
    ) -> None:
        """Log error message with context."""
        ctx = self._add_context(msg, pipeline_id, loop_id, phase, agent_id, extra)
        self._logger.error(msg, extra=ctx)

    def critical(
        self,
        msg: str,
        pipeline_id: Optional[str] = None,
        loop_id: Optional[str] = None,
        phase: Optional[str] = None,
        agent_id: Optional[str] = None,
        **extra: Any,
    ) -> None:
        """Log critical message with context."""
        ctx = self._add_context(msg, pipeline_id, loop_id, phase, agent_id, extra)
        self._logger.critical(msg, extra=ctx)

    def exception(
        self,
        msg: str,
        pipeline_id: Optional[str] = None,
        loop_id: Optional[str] = None,
        phase: Optional[str] = None,
        agent_id: Optional[str] = None,
        **extra: Any,
    ) -> None:
        """Log exception with context."""
        ctx = self._add_context(msg, pipeline_id, loop_id, phase, agent_id, extra)
        self._logger.exception(msg, extra=ctx)


# Global logger registry
_loggers: Dict[str, GAIALogger] = {}


def setup_logging(
    level: int = logging.INFO,
    log_file: Optional[str] = None,
    json_format: bool = False,
    use_colors: bool = True,
) -> None:
    """
    Configure logging for GAIA.

    Args:
        level: Logging level (default: INFO)
        log_file: Optional file path for log output
        json_format: Whether to use JSON format (default: False for text)
        use_colors: Whether to use ANSI colors in console output
    """
    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Clear existing handlers
    root_logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(level)
    console_handler.setFormatter(
        LogFormatter(
            use_colors=use_colors,
            json_format=json_format,
        )
    )
    root_logger.addHandler(console_handler)

    # File handler (if specified)
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(level)
        file_handler.setFormatter(
            LogFormatter(
                use_colors=False,
                json_format=True,  # Always JSON for file logs
            )
        )
        root_logger.addHandler(file_handler)

    # Set GAIA-specific log levels
    logging.getLogger("gaia").setLevel(level)


def get_logger(name: str) -> GAIALogger:
    """
    Get a logger instance for the given name.

    Args:
        name: Logger name (usually __name__)

    Returns:
        GAIALogger instance
    """
    if name not in _loggers:
        logger = logging.getLogger(name)
        _loggers[name] = GAIALogger(logger)
    return _loggers[name]


# Convenience function for creating child loggers
def get_child_logger(parent: str, child: str) -> GAIALogger:
    """
    Get a child logger.

    Args:
        parent: Parent logger name
        child: Child logger name component

    Returns:
        GAIALogger instance
    """
    return get_logger(f"{parent}.{child}")
