# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Persistent cache and rate protection for Context7 API calls."""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

from gaia.config import gaia_home
from gaia.logger import get_logger
from gaia.utils.file_lock import file_lock, write_text_atomic

logger = get_logger(__name__)


class Context7CacheError(RuntimeError):
    """A Context7 cache or rate-state file exists but cannot be used."""


def _read_json_object(path: Path, remedy: str) -> Optional[Dict]:
    """The JSON object in *path*, or None when absent. Caller holds the lock."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as e:
        raise Context7CacheError(f"Cannot read {path}: {e}. {remedy}") from e
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise Context7CacheError(f"{path} is not valid JSON: {e}. {remedy}") from e
    if not isinstance(data, dict):
        raise Context7CacheError(
            f"{path} must hold a JSON object, got {type(data).__name__}. {remedy}"
        )
    return data


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
        self.cache_dir = cache_dir or (gaia_home() / "cache" / "context7")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.docs_dir = self.cache_dir / "documentation"
        self.docs_dir.mkdir(exist_ok=True)

        self.library_ids_file = self.cache_dir / "library_ids.json"
        self.rate_state_file = self.cache_dir / "rate_state.json"
        # One lock for the library map and every documentation entry.
        self._lock_target = self.cache_dir / "cache"

    def get_library_id(self, library_name: str) -> Optional[str]:
        """Get cached library ID if valid.

        Args:
            library_name: Library name to lookup (e.g., "nextjs")

        Returns:
            Cached library ID or None if not found/expired
        """
        with file_lock(self._lock_target):
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
        with file_lock(self._lock_target):
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
        with file_lock(self._lock_target):
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
        with file_lock(self._lock_target):
            self._save_json(
                cache_file,
                {"content": content, "timestamp": datetime.now().isoformat()},
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
        hash_key = hashlib.md5(key.encode(), usedforsecurity=False).hexdigest()[:12]
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
        """The cache file's contents; {} when absent. Caller holds the lock.

        Raises:
            Context7CacheError: the file exists but is unreadable or not a JSON
                object. Treating it as empty would wipe every other entry on the
                next write.
        """
        data = _read_json_object(
            path, "Run `gaia cache clear --context7` to reset the Context7 cache."
        )
        return {} if data is None else data

    def _save_json(self, path: Path, data: Dict):
        """Atomically replace *path* with *data*. Caller holds the lock."""
        write_text_atomic(path, json.dumps(data, indent=2))

    def clear(self):
        """Clear all cached data."""
        with file_lock(self._lock_target):
            self.library_ids_file.unlink(missing_ok=True)

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
            gaia_home() / "cache" / "context7" / "rate_state.json"
        )
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(self.state_file):
            self.state = self._load_state()

    def can_make_request(self) -> tuple[bool, str]:
        """Check if we can make a request.

        Returns:
            Tuple of (can_proceed, reason)
        """
        with file_lock(self.state_file):
            self.state = self._load_state()
        self._refill_tokens()

        # Check circuit breaker
        if self.state.circuit_open_until:
            try:
                open_until = datetime.fromisoformat(self.state.circuit_open_until)
                if datetime.now() < open_until:
                    remaining = int((open_until - datetime.now()).total_seconds())
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

    def acquire(self) -> tuple[bool, str]:
        """Check and take a token in one step, so two processes cannot both
        pass the check on the last one. Returns ``(can_proceed, reason)``."""
        with file_lock(self.state_file):
            can_proceed, reason = self.can_make_request()
            if can_proceed:
                self.state.tokens = max(0, self.state.tokens - 1)
            self._save_state()
        return can_proceed, reason

    def consume_token(self):
        """Consume a token for a request."""
        with file_lock(self.state_file):
            self.state = self._load_state()
            self._refill_tokens()
            self.state.tokens = max(0, self.state.tokens - 1)
            self._save_state()

    def record_success(self):
        """Record successful request."""
        with file_lock(self.state_file):
            self.state = self._load_state()
            self.state.consecutive_failures = 0
            self._save_state()

    def record_failure(self, is_rate_limit: bool = False):
        """Record failed request.

        Args:
            is_rate_limit: True if failure was due to rate limiting (HTTP 429)
        """
        with file_lock(self.state_file):
            self.state = self._load_state()
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
        """The state on disk, or a full bucket when there is none. Caller holds the lock.

        Raises:
            Context7CacheError: the file exists but cannot be used. Starting a
                fresh bucket would silently lift the rate limit.
        """
        remedy = f"Delete {self.state_file} to reset the Context7 rate limiter."
        data = _read_json_object(self.state_file, remedy)
        if data is None:
            return RateState(last_update=datetime.now().isoformat())
        try:
            return RateState(**data)
        except TypeError as e:
            raise Context7CacheError(
                f"{self.state_file} does not hold a rate state: {e}. {remedy}"
            ) from e

    def _save_state(self):
        """Atomically persist the state. Caller holds the lock."""
        write_text_atomic(self.state_file, json.dumps(asdict(self.state), indent=2))

    def get_status(self) -> dict:
        """Get current rate limiter status.

        Returns:
            Dict with status information
        """
        with file_lock(self.state_file):
            self.state = self._load_state()
        self._refill_tokens()
        return {
            "tokens_available": round(self.state.tokens, 1),
            "max_tokens": self.state.MAX_TOKENS,
            "circuit_open": bool(self.state.circuit_open_until),
            "consecutive_failures": self.state.consecutive_failures,
        }
