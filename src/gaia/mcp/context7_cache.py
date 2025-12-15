# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Persistent cache and rate protection for Context7 API calls."""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

from gaia.logger import get_logger

logger = get_logger(__name__)


class Context7Cache:
    """File-based persistent cache for Context7 results.

    Caches library ID mappings and documentation across sessions to reduce API calls.
    """

    # TTL values
    TTL_LIBRARY_ID = timedelta(days=7)
    TTL_DOCUMENTATION = timedelta(hours=24)
    TTL_FAILED = timedelta(hours=1)

    def __init__(self, cache_dir: Optional[Path] = None):
        """Initialize Context7 cache.

        Args:
            cache_dir: Optional custom cache directory (defaults to ~/.gaia/cache/context7)
        """
        self.cache_dir = cache_dir or Path.home() / ".gaia" / "cache" / "context7"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.docs_dir = self.cache_dir / "documentation"
        self.docs_dir.mkdir(exist_ok=True)

        self.library_ids_file = self.cache_dir / "library_ids.json"
        self.rate_state_file = self.cache_dir / "rate_state.json"

    def get_library_id(self, library_name: str) -> Optional[str]:
        """Get cached library ID if valid.

        Args:
            library_name: Library name to lookup (e.g., "nextjs")

        Returns:
            Cached library ID or None if not found/expired
        """
        cache = self._load_json(self.library_ids_file)
        key = library_name.lower()

        if key in cache:
            entry = cache[key]
            if self._is_valid(entry, self.TTL_LIBRARY_ID):
                return entry["value"]

        return None

    def set_library_id(self, library_name: str, library_id: Optional[str]):
        """Cache library ID resolution.

        Args:
            library_name: Library name (e.g., "nextjs")
            library_id: Resolved Context7 ID (e.g., "/vercel/next.js") or None if failed
        """
        cache = self._load_json(self.library_ids_file)
        cache[library_name.lower()] = {
            "value": library_id,
            "timestamp": datetime.now().isoformat(),
        }
        self._save_json(self.library_ids_file, cache)

    def get_documentation(self, library: str, query: str) -> Optional[str]:
        """Get cached documentation if valid.

        Args:
            library: Library identifier
            query: Documentation query

        Returns:
            Cached documentation content or None if not found/expired
        """
        cache_file = self._doc_cache_file(library, query)
        if cache_file.exists():
            entry = self._load_json(cache_file)
            if self._is_valid(entry, self.TTL_DOCUMENTATION):
                return entry.get("content")

        return None

    def set_documentation(self, library: str, query: str, content: str):
        """Cache documentation result.

        Args:
            library: Library identifier
            query: Documentation query
            content: Documentation content to cache
        """
        cache_file = self._doc_cache_file(library, query)
        self._save_json(
            cache_file, {"content": content, "timestamp": datetime.now().isoformat()}
        )

    def _doc_cache_file(self, library: str, query: str) -> Path:
        """Generate cache filename for documentation.

        Args:
            library: Library identifier
            query: Documentation query

        Returns:
            Path to cache file
        """
        key = f"{library}:{query}"
        hash_key = hashlib.md5(key.encode()).hexdigest()[:12]
        safe_lib = library.replace("/", "_").replace(".", "_")
        return self.docs_dir / f"{safe_lib}_{hash_key}.json"

    def _is_valid(self, entry: Dict, ttl: timedelta) -> bool:
        """Check if cache entry is still valid.

        Args:
            entry: Cache entry dict
            ttl: Time-to-live duration

        Returns:
            True if entry is valid and not expired
        """
        if not entry or "timestamp" not in entry:
            return False

        try:
            timestamp = datetime.fromisoformat(entry["timestamp"])
            return datetime.now() - timestamp < ttl
        except (ValueError, TypeError):
            return False

    def _load_json(self, path: Path) -> Dict:
        """Load JSON file or return empty dict.

        Args:
            path: Path to JSON file

        Returns:
            Loaded data or empty dict
        """
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                logger.warning(f"Failed to load cache file: {path}")
                return {}
        return {}

    def _save_json(self, path: Path, data: Dict):
        """Save data to JSON file.

        Args:
            path: Path to JSON file
            data: Data to save
        """
        try:
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as e:
            logger.error(f"Failed to save cache file: {path}: {e}")

    def clear(self):
        """Clear all cached data."""
        if self.library_ids_file.exists():
            self.library_ids_file.unlink()

        for f in self.docs_dir.glob("*.json"):
            f.unlink()

        logger.info("Context7 cache cleared")


@dataclass
class RateState:
    """Rate limiter state persisted to disk."""

    tokens: float = 30.0  # Available tokens
    last_update: str = ""  # ISO timestamp
    consecutive_failures: int = 0
    circuit_open_until: str = ""  # ISO timestamp if open

    # Constants
    MAX_TOKENS: float = 30.0
    REFILL_RATE: float = 0.5  # tokens per minute (30/hour)
    CIRCUIT_OPEN_DURATION: int = 300  # 5 minutes


class Context7RateLimiter:
    """Token bucket rate limiter with circuit breaker.

    Protects against Context7 rate limiting by:
    - Limiting requests to 30/hour (0.5/minute refill)
    - Opening circuit breaker after 5 consecutive failures
    - Reopening circuit after 5 minute cooldown
    """

    def __init__(self, state_file: Optional[Path] = None):
        """Initialize rate limiter.

        Args:
            state_file: Optional custom state file path
        """
        self.state_file = state_file or (
            Path.home() / ".gaia" / "cache" / "context7" / "rate_state.json"
        )
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state = self._load_state()

    def can_make_request(self) -> tuple[bool, str]:
        """Check if we can make a request.

        Returns:
            Tuple of (can_proceed, reason)
        """
        self._refill_tokens()

        # Check circuit breaker
        if self.state.circuit_open_until:
            try:
                open_until = datetime.fromisoformat(self.state.circuit_open_until)
                if datetime.now() < open_until:
                    remaining = (open_until - datetime.now()).seconds
                    return False, f"Circuit breaker open. Retry in {remaining}s"
                else:
                    # Circuit recovered
                    self.state.circuit_open_until = ""
                    self.state.consecutive_failures = 0
                    logger.info("Circuit breaker closed - recovered from failures")
            except (ValueError, TypeError):
                # Invalid timestamp, clear it
                self.state.circuit_open_until = ""

        # Check tokens
        if self.state.tokens < 1.0:
            return False, "Rate limit reached. Try again in a few minutes."

        return True, "OK"

    def consume_token(self):
        """Consume a token for a request."""
        self._refill_tokens()
        self.state.tokens = max(0, self.state.tokens - 1)
        self._save_state()

    def record_success(self):
        """Record successful request."""
        self.state.consecutive_failures = 0
        self._save_state()

    def record_failure(self, is_rate_limit: bool = False):
        """Record failed request.

        Args:
            is_rate_limit: True if failure was due to rate limiting (HTTP 429)
        """
        self.state.consecutive_failures += 1

        # Open circuit on rate limit or too many failures
        if is_rate_limit or self.state.consecutive_failures >= 5:
            open_until = datetime.now() + timedelta(
                seconds=self.state.CIRCUIT_OPEN_DURATION
            )
            self.state.circuit_open_until = open_until.isoformat()
            logger.warning(
                f"Circuit breaker opened until {open_until} "
                f"(failures: {self.state.consecutive_failures})"
            )

        self._save_state()

    def _refill_tokens(self):
        """Refill tokens based on time elapsed."""
        now = datetime.now()

        if self.state.last_update:
            try:
                last = datetime.fromisoformat(self.state.last_update)
                elapsed_minutes = (now - last).total_seconds() / 60
                refill = elapsed_minutes * self.state.REFILL_RATE
                self.state.tokens = min(
                    self.state.MAX_TOKENS, self.state.tokens + refill
                )
            except (ValueError, TypeError):
                # Invalid timestamp, reset
                self.state.tokens = self.state.MAX_TOKENS

        self.state.last_update = now.isoformat()

    def _load_state(self) -> RateState:
        """Load state from disk.

        Returns:
            Loaded rate state or new state if file doesn't exist
        """
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                return RateState(**data)
            except (json.JSONDecodeError, TypeError, OSError) as e:
                logger.warning(f"Failed to load rate state: {e}, creating new state")

        return RateState(last_update=datetime.now().isoformat())

    def _save_state(self):
        """Save state to disk."""
        try:
            self.state_file.write_text(
                json.dumps(asdict(self.state), indent=2), encoding="utf-8"
            )
        except OSError as e:
            logger.error(f"Failed to save rate state: {e}")

    def get_status(self) -> dict:
        """Get current rate limiter status.

        Returns:
            Dict with status information
        """
        self._refill_tokens()
        return {
            "tokens_available": round(self.state.tokens, 1),
            "max_tokens": self.state.MAX_TOKENS,
            "circuit_open": bool(self.state.circuit_open_until),
            "consecutive_failures": self.state.consecutive_failures,
        }
