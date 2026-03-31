"""
GAIA ID Generator Module

Provides utilities for generating unique identifiers for pipelines, loops,
agents, and other GAIA components.
"""

import random
import string
import time
import uuid
from datetime import datetime, timezone
from typing import Optional


def generate_id(prefix: str = "", separator: str = "-") -> str:
    """
    Generate a unique ID with optional prefix.

    Format: {prefix}{separator}{timestamp}{separator}{random}

    Args:
        prefix: Optional prefix for the ID
        separator: Character to separate parts (default: '-')
        random: Optional random string to append

    Returns:
        Unique ID string

    Example:
        >>> generate_id("pipeline")
        'pipeline-20260323-7f3a2b'

        >>> generate_id("loop", separator="_")
        'loop_20260323_9c4e1d'
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    random_part = "".join(random.choices(string.hexdigits.lower(), k=6))

    parts = []
    if prefix:
        parts.append(prefix)
    parts.append(timestamp)
    parts.append(random_part)

    return separator.join(parts)


def generate_pipeline_id() -> str:
    """
    Generate a unique pipeline ID.

    Format: pipeline-{timestamp}-{random}

    Returns:
        Pipeline ID string

    Example:
        >>> generate_pipeline_id()
        'pipeline-20260323143052-7f3a2b'
    """
    return generate_id("pipeline")


def generate_loop_id(pipeline_id: Optional[str] = None) -> str:
    """
    Generate a unique loop ID.

    Format: loop-{timestamp}-{random}
    Or: {pipeline_id}.loop-{sequence}

    Args:
        pipeline_id: Optional parent pipeline ID to include

    Returns:
        Loop ID string

    Example:
        >>> generate_loop_id()
        'loop-20260323143052-9c4e1d'

        >>> generate_loop_id("pipeline-001")
        'pipeline-001.loop-20260323143052-9c4e1d'
    """
    loop_id = generate_id("loop")
    if pipeline_id:
        return f"{pipeline_id}.{loop_id}"
    return loop_id


def generate_agent_id() -> str:
    """
    Generate a unique agent instance ID.

    Format: agent-{uuid}

    Returns:
        Agent ID string
    """
    return f"agent-{uuid.uuid4().hex[:12]}"


def generate_phase_id(phase_name: str) -> str:
    """
    Generate a unique phase execution ID.

    Format: phase-{phase_name}-{timestamp}-{random}

    Args:
        phase_name: Name of the phase

    Returns:
        Phase ID string
    """
    return generate_id(f"phase-{phase_name.lower()}")


def generate_hook_id(hook_name: str) -> str:
    """
    Generate a unique hook execution ID.

    Format: hook-{hook_name}-{timestamp}-{random}

    Args:
        hook_name: Name of the hook

    Returns:
        Hook ID string
    """
    return generate_id(f"hook-{hook_name.lower()}")


def generate_uuid() -> str:
    """
    Generate a full UUID v4.

    Returns:
        UUID string
    """
    return str(uuid.uuid4())


def generate_short_uuid(length: int = 8) -> str:
    """
    Generate a shortened UUID.

    Args:
        length: Length of the UUID string (default: 8)

    Returns:
        Shortened UUID string
    """
    return uuid.uuid4().hex[:length]


def generate_correlation_id() -> str:
    """
    Generate a correlation ID for tracing requests across components.

    Format: corr-{timestamp}-{random}

    Returns:
        Correlation ID string
    """
    return generate_id("corr")


def parse_id(id_string: str, separator: str = "-") -> dict:
    """
    Parse an ID string into its components.

    Args:
        id_string: The ID string to parse
        separator: Character separating parts

    Returns:
        Dictionary with prefix, timestamp, and random parts

    Example:
        >>> parse_id("pipeline-20260323143052-7f3a2b")
        {'prefix': 'pipeline', 'timestamp': '20260323143052', 'random': '7f3a2b'}
    """
    parts = id_string.split(separator)

    if len(parts) < 3:
        return {"raw": id_string}

    return {
        "prefix": parts[0],
        "timestamp": parts[1],
        "random": parts[2],
    }


def timestamp_from_id(id_string: str, separator: str = "-") -> Optional[datetime]:
    """
    Extract timestamp from an ID string.

    Args:
        id_string: The ID string to parse
        separator: Character separating parts

    Returns:
        datetime object or None if parsing fails
    """
    parsed = parse_id(id_string, separator)
    timestamp_str = parsed.get("timestamp")

    if not timestamp_str or len(timestamp_str) < 14:
        return None

    try:
        return datetime.strptime(timestamp_str[:14], "%Y%m%d%H%M%S")
    except ValueError:
        return None


class IDGenerator:
    """
    Stateful ID generator with sequence tracking.

    Useful for generating sequential IDs within a session.
    """

    def __init__(self, prefix: str = "", separator: str = "-"):
        self.prefix = prefix
        self.separator = separator
        self._counter = 0
        self._base_time = time.time()

    def generate(self, include_timestamp: bool = True) -> str:
        """
        Generate a new ID with incrementing sequence.

        Args:
            include_timestamp: Whether to include timestamp (default: True)

        Returns:
            Unique ID string
        """
        self._counter += 1

        if include_timestamp:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            parts = [timestamp, str(self._counter)]
        else:
            parts = [str(self._counter)]

        if self.prefix:
            parts.insert(0, self.prefix)

        return self.separator.join(parts)

    def generate_with_prefix(self, prefix: str) -> str:
        """
        Generate an ID with a specific prefix.

        Args:
            prefix: Prefix for this ID

        Returns:
            Unique ID string
        """
        old_prefix = self.prefix
        self.prefix = prefix
        result = self.generate()
        self.prefix = old_prefix
        return result

    def reset(self) -> None:
        """Reset the counter."""
        self._counter = 0
        self._base_time = time.time()

    @property
    def count(self) -> int:
        """Get current counter value."""
        return self._counter


# Module-level generator for sequential IDs
_default_generator = IDGenerator()


def get_next_id(prefix: str = "") -> str:
    """
    Get the next sequential ID.

    Args:
        prefix: Optional prefix

    Returns:
        Sequential ID string
    """
    if prefix:
        return _default_generator.generate_with_prefix(prefix)
    return _default_generator.generate()


def reset_id_generator() -> None:
    """Reset the default ID generator."""
    _default_generator.reset()
