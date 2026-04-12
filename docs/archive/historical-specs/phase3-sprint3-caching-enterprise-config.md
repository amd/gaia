# Phase 3 Sprint 3: Caching + Enterprise Config Technical Specification

**Document Type:** Technical Specification
**Version:** 1.0.0
**Date:** 2026-04-06
**Author:** Dr. Sarah Kim, Technical Product Strategist & Engineering Lead
**Status:** Ready for Implementation

---

## Executive Summary

This specification defines Phase 3 Sprint 3 of the BAIBEL-GAIA integration program, focusing on **enterprise-grade caching infrastructure** and **configuration management systems**. This sprint builds upon the modular architecture (Sprint 1) and dependency injection + performance optimizations (Sprint 2) to deliver production-ready caching and configuration capabilities.

### Sprint Objectives

| Objective | Description | Success Metric |
|-----------|-------------|----------------|
| **CacheLayer** | Multi-level caching with TTL, hit/miss tracking | >80% cache hit rate (CACHE-001) |
| **ConfigSchema** | Type-safe configuration validation | 100% schema validation coverage (ENT-001) |
| **ConfigManager** | Hierarchical config with hot-reload | Sub-100ms config access |
| **SecretsManager** | Secure secrets handling | <10ms retrieval latency (ENT-002) |
| **Test Coverage** | Comprehensive unit + integration tests | >90% code coverage, 105+ tests |

---

## 1. Architecture Overview

### 1.1 Component Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Phase 3 Sprint 3 Architecture                         │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────────────┐         ┌──────────────────────┐                  │
│  │    CacheLayer        │         │    ConfigManager     │                  │
│  │  ┌────────────────┐  │         │  ┌────────────────┐  │                  │
│  │  │ In-Memory LRU  │  │         │  │ ConfigSchema   │  │                  │
│  │  │   Cache        │  │         │  │   Validation   │  │                  │
│  │  └────────────────┘  │         │  └────────────────┘  │                  │
│  │  ┌────────────────┐  │         │  ┌────────────────┐  │                  │
│  │  │    Disk Cache  │  │         │  │  Environment   │  │                  │
│  │  │   (SQLite)     │  │         │  │    Overrides   │  │                  │
│  │  └────────────────┘  │         │  └────────────────┘  │                  │
│  │  ┌────────────────┐  │         │  ┌────────────────┐  │                  │
│  │  │   TTL Manager  │  │         │  │  Hot-Reload    │  │                  │
│  │  └────────────────┘  │         │  │   File Watch   │  │                  │
│  │  └────────────────┐  │         │  └────────────────┘  │                  │
│  │  │ Hit/Miss Stats │  │         └──────────────────────┘                  │
│  │  └────────────────┘  │                    │                               │
│  └──────────────────────┘                    │                               │
│           │                                  │                               │
│           │         ┌──────────────────────┐ │                               │
│           │         │   SecretsManager     │◄┘                               │
│           │         │  ┌────────────────┐  │                                 │
│           │         │  │ Env Var Store  │  │                                 │
│           │         │  └────────────────┘  │                                 │
│           │         │  ┌────────────────┐  │                                 │
│           │         │  │ Secret Cache   │  │                                 │
│           │         │  │ (<10ms target) │  │                                 │
│           │         │  └────────────────┘  │                                 │
│           │         │  ┌────────────────┐  │                                 │
│           │         │  │ Access Logger  │  │                                 │
│           │         │  └────────────────┘  │                                 │
│           │         └──────────────────────┘                                 │
│           │                  │                                               │
│           └──────────────────┼───────────────────────────────────────────────┘
│                              │
│              ┌───────────────┼───────────────┐
│              │               │               │
│              ▼               ▼               ▼
│     ┌────────────────┐ ┌────────────────┐ ┌────────────────┐
│     │ Sprint 1 Utils │ │ Sprint 2 DI    │ │ Sprint 2 Async │
│     │ - file_watcher │ │ - container    │ │ - async_utils  │
│     │ - parsing      │ │ - di_container │ │ - cached       │
│     └────────────────┘ └────────────────┘ └────────────────┘
│
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Module Structure

```
src/gaia/
├── cache/                          # NEW: Caching infrastructure
│   ├── __init__.py                 # Public API exports
│   ├── cache_layer.py              # Main CacheLayer implementation (~400 LOC)
│   ├── lru_cache.py                # In-memory LRU cache component
│   ├── disk_cache.py               # SQLite-based disk cache
│   ├── ttl_manager.py              # TTL expiration management
│   ├── stats.py                    # Cache statistics and metrics
│   └── exceptions.py               # Cache-specific exceptions
│
├── config/                         # NEW: Configuration management
│   ├── __init__.py                 # Public API exports
│   ├── config_schema.py            # Schema definition and validation
│   ├── config_manager.py           # Hierarchical configuration
│   ├── secrets_manager.py          # Secure secrets handling
│   ├── loaders/                    # Configuration loaders
│   │   ├── __init__.py
│   │   ├── json_loader.py          # JSON config loader
│   │   ├── yaml_loader.py          # YAML config loader
│   │   ├── env_loader.py           # Environment variable loader
│   │   └── file_watcher_loader.py  # Hot-reload file watcher
│   └── validators/                 # Configuration validators
│       ├── __init__.py
│       ├── type_validator.py       # Type validation
│       ├── range_validator.py      # Range/constraint validation
│       └── required_validator.py   # Required field validation
│
└── testing/                        # EXTENDED: Test utilities
    ├── fixtures.py                 # Existing + new fixtures
    ├── mocks.py                    # Existing + new mocks
    └── cache_fixtures.py           # NEW: Cache-specific test fixtures
```

---

## 2. CacheLayer Specification (~400 LOC)

### 2.1 Design Patterns

| Pattern | Usage | Rationale |
|---------|-------|-----------|
| **Facade** | `CacheLayer` class | Unified interface for multi-level caching |
| **Strategy** | Cache eviction policies | Pluggable LRU, LFU, FIFO strategies |
| **Observer** | TTL expiration events | Async callbacks on cache miss/expiry |
| **Decorator** | `@cached` method decorator | Declarative caching for async functions |
| **Singleton** | Default cache instance | Shared cache across application |

### 2.2 File Paths and LOC Estimates

| File | LOC | Description |
|------|-----|-------------|
| `cache/__init__.py` | 30 | Public API exports, version |
| `cache/cache_layer.py` | 180 | Main CacheLayer class, decorator |
| `cache/lru_cache.py` | 100 | In-memory LRU implementation |
| `cache/disk_cache.py` | 120 | SQLite disk cache implementation |
| `cache/ttl_manager.py` | 80 | TTL expiration background task |
| `cache/stats.py` | 60 | Statistics and metrics tracking |
| `cache/exceptions.py` | 40 | Custom exception classes |
| **Total** | **~610 LOC** | Core implementation |

### 2.3 Key Classes and Methods

#### 2.3.1 CacheLayer (Main Facade)

```python
# File: cache/cache_layer.py

class CacheLayer:
    """
    Multi-level caching system with in-memory + disk backing.

    Features:
        - Two-tier caching: LRU in-memory + SQLite disk cache
        - TTL-based expiration with background cleanup
        - Cache hit/miss tracking and statistics
        - Async-safe with proper locking
        - Integration with Sprint 2 async utilities

    Example:
        >>> cache = CacheLayer(
        ...     memory_max_size=1000,
        ...     disk_path="./gaia_cache.db",
        ...     default_ttl=3600,
        ... )
        >>> await cache.set("key", {"data": "value"})
        >>> value = await cache.get("key")
        >>> stats = cache.stats()  # hit_rate, miss_rate, size
    """

    def __init__(
        self,
        memory_max_size: int = 1000,
        disk_path: Optional[str] = None,
        default_ttl: int = 3600,
        enable_stats: bool = True,
    ): ...

    async def get(self, key: str, default: Any = None) -> Any:
        """
        Retrieve value from cache.

        Checks memory cache first, then disk cache on miss.
        Updates LRU order and TTL on access.

        Returns:
            Cached value or default if not found/expired
        """

    async def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[int] = None,
        serialize: bool = True,
    ) -> None:
        """
        Store value in cache.

        Stores in memory cache; spills to disk if memory full.
        Sets TTL based on provided value or default.

        Args:
            key: Cache key
            value: Value to cache (auto-serialized if needed)
            ttl: Time-to-live in seconds (uses default if None)
            serialize: Whether to serialize non-primitive values
        """

    async def delete(self, key: str) -> bool:
        """Delete key from both memory and disk cache."""

    async def clear(self) -> None:
        """Clear all cache entries (memory + disk)."""

    async def get_or_set(
        self,
        key: str,
        factory: Callable[[], Awaitable[Any]],
        ttl: Optional[int] = None,
    ) -> Any:
        """
        Get value or compute and cache using factory.

        Atomic operation - prevents thundering herd.

        Args:
            key: Cache key
            factory: Async function to compute value on miss
            ttl: TTL for cached result
        """

    def stats(self) -> CacheStats:
        """Return current cache statistics."""

    async def start_ttl_cleanup(self, interval: int = 60) -> None:
        """Start background TTL cleanup task."""

    async def stop(self) -> None:
        """Graceful shutdown - flush disk, stop tasks."""
```

#### 2.3.2 LRUCache (In-Memory Tier)

```python
# File: cache/lru_cache.py

class LRUCache:
    """
    Thread-safe async LRU cache implementation.

    Uses OrderedDict for O(1) get/set operations.
    Supports max size with automatic eviction.

    Attributes:
        max_size: Maximum entries before eviction
        current_size: Current entry count
    """

    def __init__(self, max_size: int = 1000): ...

    async def get(self, key: str) -> Optional[Tuple[Any, float]]:
        """Get value and expiry timestamp."""

    async def set(self, key: str, value: Any, expires_at: float) -> None:
        """Set value with expiration timestamp."""

    async def delete(self, key: str) -> bool:
        """Delete key if exists."""

    async def clear(self) -> None:
        """Clear all entries."""

    def __len__(self) -> int: ...

    async def keys(self) -> List[str]: ...

    async def evict_lru(self) -> Optional[Tuple[str, Any]]:
        """Evict least recently used entry."""
```

#### 2.3.3 DiskCache (SQLite Backing Store)

```python
# File: cache/disk_cache.py

class DiskCache:
    """
    SQLite-based disk cache for overflow and persistence.

    Schema:
        CREATE TABLE cache_entries (
            key TEXT PRIMARY KEY,
            value BLOB NOT NULL,
            expires_at REAL NOT NULL,
            created_at REAL NOT NULL,
            access_count INTEGER DEFAULT 0
        )

    Features:
        - Automatic table creation
        - WAL mode for concurrent access
        - Periodic vacuum for space reclaim
    """

    def __init__(self, db_path: str, max_entries: int = 10000): ...

    async def get(self, key: str) -> Optional[bytes]:
        """Retrieve serialized value."""

    async def set(self, key: str, value: bytes, expires_at: float) -> None:
        """Store serialized value with TTL."""

    async def delete(self, key: str) -> bool:
        """Delete entry."""

    async def clear(self) -> None:
        """Delete all entries."""

    async def cleanup_expired(self) -> int:
        """Remove expired entries, return count deleted."""

    async def close(self) -> None:
        """Close database connection."""
```

#### 2.3.4 TTLManager (Expiration Handling)

```python
# File: cache/ttl_manager.py

class TTLManager:
    """
    Manages TTL expiration with background cleanup.

    Spawns async task for periodic expired entry removal.
    Notifies observers on expiration events.

    Example:
        >>> ttl_mgr = TTLManager()
        >>> ttl_mgr.on_expired(lambda key: print(f"Expired: {key}"))
        >>> await ttl_mgr.start(cleanup_interval=60)
    """

    def __init__(self, default_ttl: int = 3600): ...

    def compute_expiry(self, ttl: Optional[int]) -> float:
        """Compute absolute expiry timestamp from TTL."""

    def is_expired(self, expires_at: float) -> bool:
        """Check if timestamp is expired."""

    def on_expired(self, callback: Callable[[str], None]) -> None:
        """Register callback for expiration events."""

    async def start(self, cleanup_interval: int = 60) -> None:
        """Start background cleanup loop."""

    async def stop(self) -> None:
        """Stop cleanup loop gracefully."""
```

#### 2.3.5 CacheStats (Metrics Tracking)

```python
# File: cache/stats.py

@dataclass
class CacheStats:
    """
    Cache performance statistics.

    Attributes:
        hits: Number of cache hits
        misses: Number of cache misses
        hit_rate: Computed hit rate (0.0-1.0)
        miss_rate: Computed miss rate (0.0-1.0)
        memory_size: Current memory cache size
        disk_size: Current disk cache size
        evictions: Total evictions performed
        avg_get_latency_ms: Average get() latency
        avg_set_latency_ms: Average set() latency
    """
    hits: int = 0
    misses: int = 0
    memory_size: int = 0
    disk_size: int = 0
    evictions: int = 0
    total_gets: int = 0
    total_sets: int = 0
    total_get_latency_ms: float = 0.0
    total_set_latency_ms: float = 0.0

    @property
    def hit_rate(self) -> float:
        """Compute hit rate percentage."""
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

    @property
    def avg_get_latency_ms(self) -> float:
        """Compute average get() latency."""
        return self.total_get_latency_ms / self.total_gets if self.total_gets > 0 else 0.0

    @property
    def avg_set_latency_ms(self) -> float:
        """Compute average set() latency."""
        return self.total_set_latency_ms / self.total_sets if self.total_sets > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging/monitoring."""
```

#### 2.3.6 @cached Decorator

```python
# File: cache/cache_layer.py

def cached(
    cache: Optional[CacheLayer] = None,
    ttl: int = 3600,
    key_func: Optional[Callable] = None,
    skip_cache_on: Optional[Callable[[Any], bool]] = None,
):
    """
    Decorator for caching async function results.

    Args:
        cache: CacheLayer instance (uses default if None)
        ttl: Time-to-live in seconds
        key_func: Function to generate cache key from args
        skip_cache_on: Predicate to skip caching (e.g., on errors)

    Example:
        >>> @cached(ttl=600)
        ... async def get_user_data(user_id: int) -> dict:
        ...     return await db.query("SELECT * FROM users WHERE id = ?", user_id)

        >>> @cached(key_func=lambda uid, refresh: f"user:{uid}")
        ... async def get_user(uid: int, refresh: bool = False) -> dict:
        ...     ...
    """
```

### 2.4 Integration with Sprint 2 Async Utilities

The CacheLayer integrates with existing Sprint 2 utilities:

```python
# Reuse and extend gaia.perf.async_utils.async_cached
from gaia.perf.async_utils import async_cached as _async_cached
from gaia.cache.cache_layer import CacheLayer

# CacheLayer provides the storage backend
# async_cached decorator uses CacheLayer internally
```

---

## 3. ConfigSchema Specification

### 3.1 File Paths and LOC Estimates

| File | LOC | Description |
|------|-----|-------------|
| `config/__init__.py` | 30 | Public API exports |
| `config/config_schema.py` | 150 | Schema definition, validation engine |
| `config/validators/__init__.py` | 10 | Validator exports |
| `config/validators/type_validator.py` | 60 | Type checking validation |
| `config/validators/range_validator.py` | 50 | Range/constraint validation |
| `config/validators/required_validator.py` | 40 | Required field validation |
| **Total** | **~340 LOC** | Schema + validators |

### 3.2 Key Classes and Methods

#### 3.2.1 ConfigSchema (Schema Definition)

```python
# File: config/config_schema.py

from typing import Any, Dict, List, Optional, Type, Union
from dataclasses import dataclass, field
from enum import Enum


class ValidationSeverity(Enum):
    ERROR = "error"       # Blocks config usage
    WARNING = "warning"   # Logged but allowed


@dataclass
class FieldSchema:
    """
    Schema definition for a single configuration field.

    Attributes:
        name: Field name
        field_type: Expected Python type(s)
        required: Whether field is mandatory
        default: Default value if not provided
        validators: List of validator callables
        description: Human-readable description
        env_var: Environment variable name for override
        secret: Whether field contains sensitive data
    """
    name: str
    field_type: Union[Type, List[Type]]
    required: bool = False
    default: Any = None
    validators: List[Callable[[Any], bool]] = field(default_factory=list)
    description: str = ""
    env_var: Optional[str] = None
    secret: bool = False


@dataclass
class ValidationResult:
    """
    Result of configuration validation.

    Attributes:
        valid: Overall validation status
        errors: List of error messages
        warnings: List of warning messages
        fields_validated: Count of validated fields
    """
    valid: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    fields_validated: int = 0


class ConfigSchema:
    """
    Configuration schema definition and validation.

    Provides type-safe configuration with validation rules.

    Example:
        >>> schema = ConfigSchema("agent_config")
        >>> schema.add_field("model_id", str, required=True)
        >>> schema.add_field("max_tokens", int, default=4096, min=1, max=32768)
        >>> schema.add_field("temperature", float, default=0.7, min=0.0, max=2.0)
        >>> schema.add_field("api_key", str, secret=True, env_var="GAIA_API_KEY")
        >>>
        >>> config = {"model_id": "Qwen3.5-35B", "temperature": 0.5}
        >>> result = schema.validate(config)
        >>> if not result.valid:
        ...     print(f"Validation errors: {result.errors}")
    """

    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self._fields: Dict[str, FieldSchema] = {}

    def add_field(
        self,
        name: str,
        field_type: Union[Type, List[Type]],
        required: bool = False,
        default: Any = None,
        min_value: Optional[Union[int, float]] = None,
        max_value: Optional[Union[int, float]] = None,
        pattern: Optional[str] = None,  # Regex for strings
        env_var: Optional[str] = None,
        secret: bool = False,
        description: str = "",
    ) -> "ConfigSchema":
        """
        Add field to schema with validation rules.

        Returns self for method chaining.
        """

    def validate(self, config: Dict[str, Any]) -> ValidationResult:
        """
        Validate configuration against schema.

        Performs:
        1. Required field check
        2. Type validation
        3. Range/constraint validation
        4. Custom validator execution

        Returns:
            ValidationResult with errors/warnings
        """

    def normalize(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize configuration with defaults.

        Adds default values for missing optional fields.
        """

    @classmethod
    def from_dataclass(cls, schema_class: Type) -> "ConfigSchema":
        """
        Create schema from dataclass definition.

        Uses dataclass field metadata for validation rules.

        Example:
            @dataclass
            class AgentConfig:
                model_id: str
                max_tokens: int = field(default=4096, metadata={"min": 1, "max": 32768})
                temperature: float = field(default=0.7, metadata={"min": 0.0, "max": 2.0})

            schema = ConfigSchema.from_dataclass(AgentConfig)
        """
```

#### 3.2.2 Validators

```python
# File: config/validators/type_validator.py

def validate_type(value: Any, expected_type: Union[Type, List[Type]]) -> bool:
    """
    Validate value matches expected type(s).

    Supports Union types via list of types.
    Handles None/null gracefully.
    """


# File: config/validators/range_validator.py

def validate_range(
    value: Union[int, float],
    min_value: Optional[Union[int, float]] = None,
    max_value: Optional[Union[int, float]] = None,
) -> bool:
    """Validate numeric value within range."""


def validate_pattern(value: str, pattern: str) -> bool:
    """Validate string matches regex pattern."""


# File: config/validators/required_validator.py

def validate_required(value: Any, field_name: str) -> Optional[str]:
    """
    Validate required field is present.

    Returns error message if missing, None if valid.
    """
```

---

## 4. ConfigManager Specification

### 4.1 File Paths and LOC Estimates

| File | LOC | Description |
|------|-----|-------------|
| `config/config_manager.py` | 200 | Hierarchical config management |
| `config/loaders/__init__.py` | 15 | Loader exports |
| `config/loaders/json_loader.py` | 50 | JSON file loading |
| `config/loaders/yaml_loader.py` | 60 | YAML file loading |
| `config/loaders/env_loader.py` | 50 | Environment variable loading |
| `config/loaders/file_watcher_loader.py` | 80 | Hot-reload with file watcher |
| **Total** | **~455 LOC** | Config manager + loaders |

### 4.2 Key Classes and Methods

#### 4.2.1 ConfigManager (Hierarchical Management)

```python
# File: config/config_manager.py

from pathlib import Path
from typing import Any, Dict, List, Optional, Type, TypeVar

from gaia.cache.cache_layer import CacheLayer


T = TypeVar('T')


class ConfigManager:
    """
    Hierarchical configuration management with hot-reload support.

    Configuration Priority (highest to lowest):
    1. Environment variables (always takes precedence)
    2. Programmatically set values
    3. Configuration files (JSON/YAML)
    4. Schema defaults

    Features:
        - Hierarchical configuration with priority stacking
        - Environment variable overrides
        - Hot-reload via file system watching
        - Schema validation on load
        - Nested configuration support
        - Integration with CacheLayer for config caching

    Example:
        >>> from gaia.config import ConfigManager, ConfigSchema
        >>>
        >>> # Define schema
        >>> schema = ConfigSchema("app_config")
        >>> schema.add_field("debug", bool, default=False)
        >>> schema.add_field("log_level", str, default="INFO")
        >>> schema.add_field("database_url", str, required=True, secret=True)
        >>>
        >>> # Create manager
        >>> manager = ConfigManager(schema=schema)
        >>> manager.add_json_file("./config/base.json")
        >>> manager.add_json_file("./config/local.json")  # Overrides base
        >>> manager.load()
        >>>
        >>> # Access values
        >>> debug = manager.get("debug")
        >>> db_url = manager.get("database_url")
        >>>
        >>> # Nested access
        >>> manager.add_json_file("./config/app.json")
        >>> model = manager.get("llm.model_id")  # Dot notation
    """

    def __init__(
        self,
        schema: Optional[ConfigSchema] = None,
        cache: Optional[CacheLayer] = None,
        enable_env_overrides: bool = True,
    ):
        """
        Initialize ConfigManager.

        Args:
            schema: Configuration schema for validation
            cache: Optional cache for config values
            enable_env_overrides: Whether to check environment variables
        """
        self.schema = schema
        self.cache = cache
        self.enable_env_overrides = enable_env_overrides
        self._config: Dict[str, Any] = {}
        self._loaders: List[Callable[[], Dict[str, Any]]] = []
        self._watchers: List[Any] = []  # FileWatcher instances
        self._callbacks: List[Callable[[Dict[str, Any]], None]] = []

    def add_json_file(self, path: str, required: bool = True) -> "ConfigManager":
        """
        Add JSON configuration file to load stack.

        Files loaded later take precedence over earlier files.

        Args:
            path: Path to JSON file
            required: Whether file must exist (raises if missing)

        Returns:
            Self for method chaining
        """

    def add_yaml_file(self, path: str, required: bool = True) -> "ConfigManager":
        """Add YAML configuration file to load stack."""

    def load(self, validate: bool = True) -> ValidationResult:
        """
        Load all configuration sources.

        Applies configuration in priority order:
        1. Schema defaults
        2. JSON/YAML files (in order added)
        3. Environment variables

        Args:
            validate: Whether to validate against schema

        Returns:
            ValidationResult (empty if validate=False)
        """

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get configuration value by key.

        Supports dot notation for nested access.
        Checks environment variable override if enabled.

        Args:
            key: Configuration key (e.g., "llm.model_id")
            default: Default value if not found

        Returns:
            Configuration value or default
        """

    def get_typed(self, key: str, type_: Type[T], default: Optional[T] = None) -> T:
        """
        Get typed configuration value.

        Attempts to cast value to specified type.
        Raises TypeError if cast fails.

        Args:
            key: Configuration key
            type_: Target type
            default: Default if key not found

        Returns:
            Typed configuration value

        Raises:
            TypeError: If value cannot be cast to type
        """

    def get_all(self) -> Dict[str, Any]:
        """Get complete configuration dictionary."""

    def set(self, key: str, value: Any) -> None:
        """
        Set configuration value programmatically.

        Highest priority (overrides files and env vars).

        Args:
            key: Configuration key
            value: Value to set
        """

    def enable_hot_reload(self, callback: Optional[Callable] = None) -> "ConfigManager":
        """
        Enable hot-reload for configuration files.

        Uses FileWatcher (Sprint 1) to monitor file changes.
        Automatically reloads and notifies callbacks.

        Args:
            callback: Optional callback invoked on reload
                     Receives new config dict

        Returns:
            Self for method chaining
        """

    def on_reload(self, callback: Callable[[Dict[str, Any]], None]) -> "ConfigManager":
        """Register callback for configuration reload events."""

    async def reload(self) -> ValidationResult:
        """Manually trigger configuration reload."""

    def validate(self) -> ValidationResult:
        """Validate current configuration against schema."""
```

---

## 5. SecretsManager Specification

### 5.1 File Paths and LOC Estimates

| File | LOC | Description |
|------|-----|-------------|
| `config/secrets_manager.py` | 180 | Secure secrets handling |
| **Total** | **~180 LOC** | Secrets manager |

### 5.2 Key Classes and Methods

```python
# File: config/secrets_manager.py

import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from gaia.cache.cache_layer import CacheLayer


@dataclass
class SecretEntry:
    """
    Cached secret entry with metadata.

    Attributes:
        value: Secret value
        fetched_at: Fetch timestamp
        latency_ms: Retrieval latency
        source: Source (env, file, vault)
    """
    value: str
    fetched_at: float
    latency_ms: float
    source: str


class SecretsManager:
    """
    Secure secrets handling with optimized caching.

    Features:
        - Environment variable integration
        - Sub-10ms cached retrieval
        - Access logging for audit trail
        - Optional encryption at rest
        - Secret rotation support

    Security Considerations:
        - Secrets NEVER logged or printed
        - Access logged with timestamps
        - Memory cleared on shutdown
        - Redacted in configuration dumps

    Example:
        >>> secrets = SecretsManager()
        >>>
        >>> # Register secrets
        >>> secrets.register("api_key", env_var="GAIA_API_KEY", required=True)
        >>> secrets.register("db_password", env_var="GAIA_DB_PASSWORD")
        >>>
        >>> # Retrieve (cached, <10ms target)
        >>> api_key = secrets.get("api_key")
        >>> db_pass = secrets.get("db_password")
        >>>
        >>> # Audit trail
        >>> audit = secrets.get_access_log()
        >>> for entry in audit:
        ...     print(f"{entry.secret_name}: {entry.timestamp}")
    """

    def __init__(
        self,
        cache: Optional[CacheLayer] = None,
        enable_audit_log: bool = True,
        enable_encryption: bool = False,
    ):
        """
        Initialize SecretsManager.

        Args:
            cache: Cache for secret values (enables <10ms target)
            enable_audit_log: Log all access for audit trail
            enable_encryption: Encrypt secrets in memory (experimental)
        """
        self.cache = cache or CacheLayer(memory_max_size=100)
        self.enable_audit_log = enable_audit_log
        self._registered: Dict[str, Dict[str, Any]] = {}
        self._access_log: List[Dict[str, Any]] = []
        self._lock = asyncio.Lock()

    def register(
        self,
        name: str,
        env_var: str,
        required: bool = False,
        default: Optional[str] = None,
        description: str = "",
    ) -> "SecretsManager":
        """
        Register a secret for management.

        Args:
            name: Internal name for the secret
            env_var: Environment variable name
            required: Whether secret is mandatory
            default: Optional default value (not recommended for secrets)
            description: Description for documentation

        Returns:
            Self for method chaining

        Raises:
            ValueError: If required secret not found
        """

    def get(self, name: str) -> Optional[str]:
        """
        Retrieve secret value.

        First checks cache (sub-10ms), then environment.
        Logs access for audit trail.

        Args:
            name: Secret name

        Returns:
            Secret value or None if not found

        Performance:
            - Cache hit: <1ms
            - Environment lookup: <10ms
            - Average target: <10ms
        """
        start_time = time.perf_counter()

        # Check cache first
        cached_value = await self.cache.get(f"secret:{name}")
        if cached_value is not None:
            latency_ms = (time.perf_counter() - start_time) * 1000
            self._log_access(name, latency_ms, "cache")
            return cached_value

        # Fetch from environment
        env_var = self._registered[name]["env_var"]
        value = os.environ.get(env_var)

        if value is None:
            value = self._registered[name].get("default")

        latency_ms = (time.perf_counter() - start_time) * 1000

        # Cache the value
        if value is not None:
            await self.cache.set(f"secret:{name}", value, ttl=3600)

        self._log_access(name, latency_ms, "env")
        return value

    def get_all(self, redact: bool = True) -> Dict[str, str]:
        """
        Get all registered secrets.

        Args:
            redact: If True, show only first 3 chars of values

        Returns:
            Dictionary of secret values (optionally redacted)
        """

    def rotate(self, name: str, new_value: str) -> None:
        """
        Rotate a secret value.

        Updates cache immediately. Does NOT update environment.

        Args:
            name: Secret name
            new_value: New secret value
        """

    def _log_access(self, name: str, latency_ms: float, source: str) -> None:
        """Log secret access for audit trail."""
        if self.enable_audit_log:
            self._access_log.append({
                "secret_name": name,
                "timestamp": time.time(),
                "latency_ms": round(latency_ms, 3),
                "source": source,
            })

    def get_access_log(self) -> List[Dict[str, Any]]:
        """
        Get access log for audit.

        Returns:
            List of access entries
        """
        return list(self._access_log)

    def clear_cache(self) -> None:
        """Clear cached secrets (forces refresh on next get)."""

    async def shutdown(self) -> None:
        """
        Graceful shutdown.

        Clears cached secrets from memory.
        """
        await self.cache.clear()
        self._access_log.clear()
        self._registered.clear()
```

---

## 6. Test Specifications (~105 Tests)

### 6.1 Test File Structure

```
tests/
├── unit/
│   ├── cache/
│   │   ├── __init__.py
│   │   ├── test_cache_layer.py       # 25 tests
│   │   ├── test_lru_cache.py         # 15 tests
│   │   ├── test_disk_cache.py        # 12 tests
│   │   ├── test_ttl_manager.py       # 10 tests
│   │   ├── test_cache_stats.py       # 8 tests
│   │   └── test_cache_decorator.py   # 10 tests
│   ├── config/
│   │   ├── __init__.py
│   │   ├── test_config_schema.py     # 15 tests
│   │   ├── test_config_manager.py    # 20 tests
│   │   ├── test_secrets_manager.py   # 15 tests
│   │   └── test_validators.py        # 12 tests
│   └── integration/
│       ├── test_cache_integration.py # 10 tests
│       └── test_config_integration.py # 8 tests
└── stress/
    └── test_cache_thread_safety.py   # 10 tests (100+ concurrent threads)
```

### 6.2 Test Coverage Requirements

| Component | Unit Tests | Integration Tests | Stress Tests | Total |
|-----------|------------|-------------------|--------------|-------|
| CacheLayer | 25 | 10 | 10 | 45 |
| LRUCache | 15 | - | - | 15 |
| DiskCache | 12 | - | - | 12 |
| TTLManager | 10 | - | - | 10 |
| CacheStats | 8 | - | - | 8 |
| CacheDecorator | 10 | - | - | 10 |
| ConfigSchema | 15 | - | - | 15 |
| ConfigManager | 20 | 8 | - | 28 |
| SecretsManager | 15 | - | - | 15 |
| Validators | 12 | - | - | 12 |
| **Total** | **142** | **18** | **10** | **170** |

*Note: Estimate is ~105 minimum; actual may reach 150+ for comprehensive coverage.*

### 6.3 Quality Gate Test Cases

#### CACHE-001: Cache Hit Rate >80%

```python
# tests/unit/cache/test_cache_layer.py

class TestCacheHitRate:
    """Verify cache achieves target hit rates."""

    @pytest.mark.asyncio
    async def test_hit_rate_exceeds_80_percent(self):
        """Cache hit rate should exceed 80% for repeated accesses."""
        cache = CacheLayer(memory_max_size=100)
        await cache.start_ttl_cleanup()

        # Set 10 values
        for i in range(10):
            await cache.set(f"key:{i}", f"value:{i}")

        # Access 100 times (10 unique keys * 10 accesses)
        hits = 0
        total = 0
        for _ in range(10):
            for i in range(10):
                result = await cache.get(f"key:{i}")
                total += 1
                if result == f"value:{i}":
                    hits += 1

        stats = cache.stats()
        assert stats.hit_rate > 0.80, f"Hit rate {stats.hit_rate} below 80% target"

        await cache.stop()
```

#### ENT-001: Config Schema Validation 100%

```python
# tests/unit/config/test_config_schema.py

class TestSchemaValidationCoverage:
    """Verify all schema fields are validated."""

    def test_all_field_types_validated(self):
        """Every field type should be validated correctly."""
        schema = ConfigSchema("test")
        schema.add_field("str_field", str, required=True)
        schema.add_field("int_field", int, min_value=0, max_value=100)
        schema.add_field("float_field", float, min_value=0.0, max_value=1.0)
        schema.add_field("bool_field", bool, default=False)
        schema.add_field("list_field", list)

        # Valid config
        config = {
            "str_field": "hello",
            "int_field": 50,
            "float_field": 0.5,
            "bool_field": True,
            "list_field": [1, 2, 3],
        }
        result = schema.validate(config)
        assert result.valid, f"Valid config rejected: {result.errors}"

    def test_required_field_validation(self):
        """Missing required fields should fail validation."""
        schema = ConfigSchema("test")
        schema.add_field("required_field", str, required=True)

        result = schema.validate({})
        assert not result.valid
        assert "required_field" in result.errors[0]
```

#### ENT-002: Secrets Retrieval <10ms

```python
# tests/unit/config/test_secrets_manager.py

class TestSecretsLatency:
    """Verify secrets meet latency targets."""

    @pytest.mark.asyncio
    async def test_cached_secret_under_10ms(self):
        """Cached secret retrieval should be under 10ms."""
        secrets = SecretsManager()
        secrets.register("test_secret", env_var="TEST_SECRET_VALUE", default="test123")

        # First access (cache miss)
        _ = secrets.get("test_secret")

        # Second access (cache hit) - measure latency
        import time
        start = time.perf_counter()
        value = secrets.get("test_secret")
        latency_ms = (time.perf_counter() - start) * 1000

        assert latency_ms < 10, f"Secret retrieval {latency_ms}ms exceeds 10ms target"
        assert value == "test123"
```

#### PERF-003: Cache Overhead <5%

```python
# tests/unit/cache/test_cache_layer.py

class TestCacheOverhead:
    """Verify cache overhead is minimal."""

    @pytest.mark.asyncio
    async def test_overhead_under_5_percent(self):
        """Cache get/set overhead should be under 5% of operation time."""
        cache = CacheLayer(memory_max_size=1000)

        # Baseline: direct dict operation
        test_dict = {}
        baseline_times = []
        for i in range(1000):
            start = time.perf_counter()
            test_dict[f"key:{i}"] = f"value:{i}"
            _ = test_dict[f"key:{i}"]
            baseline_times.append(time.perf_counter() - start)
        baseline_avg = sum(baseline_times) / len(baseline_times)

        # Cached operation
        cache_times = []
        for i in range(1000):
            start = time.perf_counter()
            await cache.set(f"key:{i}", f"value:{i}")
            _ = await cache.get(f"key:{i}")
            cache_times.append(time.perf_counter() - start)
        cache_avg = sum(cache_times) / len(cache_times)

        overhead = (cache_avg - baseline_avg) / baseline_avg
        assert overhead < 0.05, f"Cache overhead {overhead*100:.1f}% exceeds 5% target"
```

#### THREAD-002: Thread Safety 100+ Threads

```python
# tests/stress/test_cache_thread_safety.py

import asyncio
import threading
import pytest


class TestCacheThreadSafety:
    """Verify cache is thread-safe under concurrent load."""

    @pytest.mark.asyncio
    async def test_concurrent_access_100_threads(self):
        """Cache should handle 100+ concurrent threads safely."""
        cache = CacheLayer(memory_max_size=1000)
        errors = []
        success_count = [0]
        lock = threading.Lock()

        async def worker(thread_id: int):
            try:
                for i in range(10):
                    key = f"thread:{thread_id}:key:{i}"
                    await cache.set(key, f"value:{thread_id}:{i}")
                    value = await cache.get(key)
                    assert value == f"value:{thread_id}:{i}"

                with lock:
                    success_count[0] += 1
            except Exception as e:
                errors.append((thread_id, str(e)))

        # Launch 100 concurrent workers
        tasks = [worker(i) for i in range(100)]
        await asyncio.gather(*tasks)

        assert len(errors) == 0, f"Thread safety errors: {errors}"
        assert success_count[0] == 100, f"Only {success_count[0]}/100 threads succeeded"
```

### 6.4 Test Fixtures

```python
# tests/conftest.py (additions)

import pytest
from gaia.cache.cache_layer import CacheLayer
from gaia.config.config_manager import ConfigManager
from gaia.config.secrets_manager import SecretsManager


@pytest.fixture
async def cache_layer():
    """Provide CacheLayer instance for tests."""
    cache = CacheLayer(memory_max_size=100, disk_path=None)
    await cache.start_ttl_cleanup()
    yield cache
    await cache.stop()


@pytest.fixture
def config_schema():
    """Provide sample ConfigSchema for tests."""
    from gaia.config import ConfigSchema

    schema = ConfigSchema("test_config")
    schema.add_field("debug", bool, default=False)
    schema.add_field("log_level", str, default="INFO")
    schema.add_field("max_connections", int, default=100, min_value=1, max_value=1000)
    return schema


@pytest.fixture
def config_manager(config_schema):
    """Provide ConfigManager instance for tests."""
    manager = ConfigManager(schema=config_schema, enable_env_overrides=False)
    yield manager


@pytest.fixture
async def secrets_manager():
    """Provide SecretsManager instance for tests."""
    secrets = SecretsManager(enable_audit_log=True)
    yield secrets
    await secrets.shutdown()


@pytest.fixture
def temp_config_files(tmp_path):
    """Create temporary configuration files."""
    import json
    import yaml

    base_config = {
        "debug": False,
        "log_level": "INFO",
        "database": {
            "host": "localhost",
            "port": 5432,
        }
    }

    local_config = {
        "debug": True,
        "log_level": "DEBUG",
    }

    base_file = tmp_path / "base.json"
    local_file = tmp_path / "local.json"

    with open(base_file, "w") as f:
        json.dump(base_config, f)

    with open(local_file, "w") as f:
        json.dump(local_config, f)

    return {
        "base": base_file,
        "local": local_file,
        "dir": tmp_path,
    }
```

---

## 7. Implementation Plan

### 7.1 Phase Breakdown

| Phase | Duration | Deliverables |
|-------|----------|--------------|
| **Phase 1: CacheLayer** | 3-4 days | cache_layer.py, lru_cache.py, disk_cache.py, tests |
| **Phase 2: ConfigSchema** | 2 days | config_schema.py, validators, tests |
| **Phase 3: ConfigManager** | 2-3 days | config_manager.py, loaders, tests |
| **Phase 4: SecretsManager** | 1-2 days | secrets_manager.py, tests |
| **Phase 5: Integration + Polish** | 2 days | Integration tests, documentation, stress tests |
| **Total** | **10-13 days** | Full sprint deliverables |

### 7.2 File Creation Order

1. `cache/exceptions.py` - Exception classes (foundation)
2. `cache/stats.py` - Statistics tracking
3. `cache/lru_cache.py` - In-memory tier
4. `cache/disk_cache.py` - Disk tier
5. `cache/ttl_manager.py` - TTL management
6. `cache/cache_layer.py` - Main facade
7. `config/validators/*.py` - Validation primitives
8. `config/config_schema.py` - Schema definition
9. `config/loaders/*.py` - Configuration loaders
10. `config/config_manager.py` - Config management
11. `config/secrets_manager.py` - Secrets handling
12. Test files throughout development

### 7.3 Dependencies

| Sprint 3 Component | Sprint 1 Dependency | Sprint 2 Dependency |
|--------------------|--------------------|--------------------|
| CacheLayer | file_watcher (for hot-reload) | async_utils (cached decorator) |
| ConfigManager | file_watcher (hot-reload) | di_container (service injection) |
| SecretsManager | - | cache_layer (for caching) |

---

## 8. Quality Gates Summary

| ID | Metric | Target | Measurement |
|----|--------|--------|-------------|
| **CACHE-001** | Cache hit rate | >80% | `CacheStats.hit_rate` |
| **ENT-001** | Config schema validation | 100% | `ValidationResult.valid` |
| **ENT-002** | Secrets retrieval latency | <10ms | `SecretEntry.latency_ms` |
| **PERF-003** | Cache overhead | <5% | Comparison benchmark |
| **THREAD-002** | Thread safety | 100+ threads | Stress test concurrency |

---

## 9. API Reference Summary

### 9.1 CacheLayer API

```python
from gaia.cache import CacheLayer, cached

# Direct usage
cache = CacheLayer(memory_max_size=1000, disk_path="./cache.db")
await cache.set("key", "value", ttl=3600)
value = await cache.get("key")
stats = cache.stats()

# Decorator usage
@cached(ttl=600)
async def get_data(query: str) -> dict:
    return await expensive_query(query)
```

### 9.2 ConfigManager API

```python
from gaia.config import ConfigManager, ConfigSchema

schema = ConfigSchema("app")
schema.add_field("model_id", str, required=True)
schema.add_field("temperature", float, default=0.7)

manager = ConfigManager(schema=schema)
manager.add_json_file("./config/base.json")
manager.add_json_file("./config/local.json")
manager.enable_hot_reload()
manager.load()

model_id = manager.get("model_id")
temp = manager.get_typed("temperature", float)
```

### 9.3 SecretsManager API

```python
from gaia.config import SecretsManager

secrets = SecretsManager()
secrets.register("api_key", env_var="GAIA_API_KEY", required=True)
secrets.register("db_password", env_var="GAIA_DB_PASSWORD")

api_key = secrets.get("api_key")
audit_log = secrets.get_access_log()
```

---

## 10. Documentation Requirements

All new modules require:
1. **Module docstrings** with examples
2. **Class docstrings** with attribute descriptions
3. **Method docstrings** with Args, Returns, Raises sections
4. **Inline comments** for complex logic
5. **Type hints** for all function signatures

Documentation files to create/update:
- `docs/sdk/infrastructure/caching.mdx` - CacheLayer guide
- `docs/sdk/infrastructure/configuration.mdx` - Config/Secrets guide
- `docs/reference/api/cache.md` - API reference
- `docs/reference/api/config.md` - API reference

---

## Appendix A: Design Decision Log

| Decision | Rationale | Alternatives Considered |
|----------|-----------|------------------------|
| **SQLite for disk cache** | Built-in, no external deps, WAL mode for concurrency | Redis (external dep), file-based (slower) |
| **LRU eviction default** | Most predictable for general workloads | LFU, FIFO, MRU |
| **Separate CacheLayer class** | Clear separation of concerns vs. decorator-only | Decorator-only (less flexible) |
| **Environment variable priority** | Standard 12-factor app pattern | File-only, code-only |
| **Sub-10ms secret target** | Meets enterprise latency expectations | No caching (slower), aggressive caching (security risk) |

---

*This specification is ready for handoff to senior-developer for implementation.*
