"""
Built-in Health Probes for GAIA.

This module provides pre-built health probes for monitoring critical
GAIA components and system resources. Probes are designed to be fast,
non-intrusive, and thread-safe.

Available Probes:
    - MemoryProbe: Monitor memory usage
    - DiskProbe: Monitor disk space
    - LLMConnectivityProbe: Check LLM server connectivity
    - DatabaseProbe: Check database connectivity
    - MCPProbe: Check MCP server connectivity
    - CacheProbe: Check cache layer health
    - RAGProbe: Check RAG index health

Example:
    >>> from gaia.health.probes import MemoryProbe, DiskProbe
    >>> memory_probe = MemoryProbe(warning_threshold=0.8, critical_threshold=0.95)
    >>> result = memory_probe.check()
    >>> print(result.status)
    HealthStatus.HEALTHY
"""

import os
import shutil
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from gaia.health.models import HealthStatus, ProbeResult

# Import optional dependencies with graceful fallback
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


@dataclass
class ProbeConfig:
    """
    Configuration for a health probe.

    Attributes:
        name: Probe name identifier
        enabled: Whether probe is enabled
        timeout_seconds: Timeout for probe execution
        warning_threshold: Threshold for DEGRADED status
        critical_threshold: Threshold for UNHEALTHY status
        check_interval_seconds: Recommended interval between checks
        metadata: Additional configuration metadata
    """

    name: str
    enabled: bool = True
    timeout_seconds: float = 5.0
    warning_threshold: float = 0.8
    critical_threshold: float = 0.95
    check_interval_seconds: float = 30.0
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseProbe(ABC):
    """
    Abstract base class for health probes.

    All probes should inherit from BaseProbe and implement the check() method.
    Probes are designed to be fast (<50ms) and thread-safe.

    Example:
        >>> class CustomProbe(BaseProbe):
        ...     def __init__(self, config: Optional[ProbeConfig] = None):
        ...         super().__init__(config or ProbeConfig(name="custom"))
        ...
        ...     def check(self) -> ProbeResult:
        ...         start = time.time()
        ...         # Perform check...
        ...         elapsed = (time.time() - start) * 1000
        ...         return ProbeResult(
        ...             probe_name=self.name,
        ...             status=HealthStatus.HEALTHY,
        ...             message="Check passed",
        ...             response_time_ms=elapsed
        ...         )
    """

    def __init__(self, config: Optional[ProbeConfig] = None):
        """
        Initialize base probe.

        Args:
            config: Optional probe configuration
        """
        self._config = config or ProbeConfig(name=self.__class__.__name__)
        self._lock = threading.RLock()
        self._last_result: Optional[ProbeResult] = None
        self._last_check_time: Optional[float] = None

    @property
    def name(self) -> str:
        """Get probe name."""
        return self._config.name

    @property
    def config(self) -> ProbeConfig:
        """Get probe configuration."""
        return self._config

    @property
    def is_enabled(self) -> bool:
        """Check if probe is enabled."""
        return self._config.enabled

    @property
    def last_result(self) -> Optional[ProbeResult]:
        """Get last probe result."""
        return self._last_result

    @property
    def last_check_time(self) -> Optional[float]:
        """Get timestamp of last check."""
        return self._last_check_time

    @abstractmethod
    def check(self) -> ProbeResult:
        """
        Execute the health check.

        Returns:
            ProbeResult with check outcome

        Raises:
            NotImplementedError: If not overridden by subclass
        """
        raise NotImplementedError("Subclasses must implement check()")

    def check_cached(self, cache_ttl_seconds: float = 5.0) -> ProbeResult:
        """
        Execute check with caching.

        Returns cached result if TTL not expired, otherwise executes
        fresh check.

        Args:
            cache_ttl_seconds: Time-to-live for cached result

        Returns:
            Cached or fresh ProbeResult
        """
        with self._lock:
            now = time.time()

            # Return cached result if still valid
            if (
                self._last_result is not None
                and self._last_check_time is not None
                and (now - self._last_check_time) < cache_ttl_seconds
            ):
                return self._last_result

            # Execute fresh check
            result = self.check()
            self._last_result = result
            self._last_check_time = now

            return result

    def _create_result(
        self,
        status: HealthStatus,
        message: str,
        response_time_ms: float,
        threshold_exceeded: bool = False,
        recommendation: Optional[str] = None,
        **metadata: Any,
    ) -> ProbeResult:
        """
        Create a probe result with standardized fields.

        Args:
            status: Health status
            message: Result message
            response_time_ms: Execution time
            threshold_exceeded: Whether threshold exceeded
            recommendation: Optional remediation recommendation
            **metadata: Additional metadata

        Returns:
            ProbeResult instance
        """
        return ProbeResult(
            probe_name=self.name,
            status=status,
            message=message,
            response_time_ms=response_time_ms,
            metadata=metadata,
            threshold_exceeded=threshold_exceeded,
            recommendation=recommendation,
        )


class MemoryProbe(BaseProbe):
    """
    Probe for monitoring system memory usage.

    MemoryProbe checks the current memory utilization and compares
    against configured thresholds to determine health status.

    Status Determination:
        - HEALTHY: Memory usage < warning_threshold (default 80%)
        - DEGRADED: Memory usage >= warning_threshold and < critical_threshold
        - UNHEALTHY: Memory usage >= critical_threshold (default 95%)

    Attributes:
        warning_threshold: Threshold for DEGRADED status (0-1)
        critical_threshold: Threshold for UNHEALTHY status (0-1)

    Example:
        >>> probe = MemoryProbe(warning_threshold=0.8, critical_threshold=0.95)
        >>> result = probe.check()
        >>> print(f"Memory: {result.metadata['used_percent']:.1f}%")
    """

    def __init__(
        self,
        warning_threshold: float = 0.8,
        critical_threshold: float = 0.95,
        config: Optional[ProbeConfig] = None,
    ):
        """
        Initialize memory probe.

        Args:
            warning_threshold: Threshold for DEGRADED (default 0.8)
            critical_threshold: Threshold for UNHEALTHY (default 0.95)
            config: Optional probe configuration
        """
        if config is None:
            config = ProbeConfig(
                name="memory_probe",
                warning_threshold=warning_threshold,
                critical_threshold=critical_threshold,
            )
        super().__init__(config)
        self._warning_threshold = warning_threshold
        self._critical_threshold = critical_threshold

    def check(self) -> ProbeResult:
        """
        Execute memory health check.

        Returns:
            ProbeResult with memory usage status

        Example:
            >>> probe = MemoryProbe()
            >>> result = probe.check()
            >>> print(result.status)
        """
        start_time = time.perf_counter()

        try:
            if PSUTIL_AVAILABLE:
                memory = psutil.virtual_memory()
                used_percent = memory.percent / 100.0
                used_mb = memory.used / (1024 * 1024)
                total_mb = memory.total / (1024 * 1024)
                available_mb = memory.available / (1024 * 1024)
            else:
                # Fallback for systems without psutil
                used_percent = self._get_memory_usage_fallback()
                used_mb = 0.0
                total_mb = 0.0
                available_mb = 0.0

            # Determine status based on thresholds
            if used_percent >= self._critical_threshold:
                status = HealthStatus.UNHEALTHY
                message = f"Critical memory usage: {used_percent * 100:.1f}%"
                recommendation = "Immediately free memory or restart application"
            elif used_percent >= self._warning_threshold:
                status = HealthStatus.DEGRADED
                message = f"High memory usage: {used_percent * 100:.1f}%"
                recommendation = "Consider increasing memory allocation"
            else:
                status = HealthStatus.HEALTHY
                message = f"Memory usage normal: {used_percent * 100:.1f}%"
                recommendation = None

            elapsed_ms = (time.perf_counter() - start_time) * 1000

            return self._create_result(
                status=status,
                message=message,
                response_time_ms=elapsed_ms,
                threshold_exceeded=used_percent >= self._warning_threshold,
                recommendation=recommendation,
                used_percent=used_percent,
                used_mb=used_mb,
                total_mb=total_mb,
                available_mb=available_mb,
                warning_threshold=self._warning_threshold,
                critical_threshold=self._critical_threshold,
            )

        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            return self._create_result(
                status=HealthStatus.UNKNOWN,
                message=f"Memory check failed: {str(e)}",
                response_time_ms=elapsed_ms,
                exception=e,
            )

    def _get_memory_usage_fallback(self) -> float:
        """
        Get memory usage without psutil (fallback method).

        Returns:
            Estimated memory usage percentage (0-1)
        """
        try:
            # Try reading from /proc on Linux
            if os.path.exists("/proc/meminfo"):
                with open("/proc/meminfo", "r") as f:
                    lines = f.readlines()

                mem_info = {}
                for line in lines:
                    parts = line.split(":")
                    if len(parts) == 2:
                        key = parts[0].strip()
                        value = int(parts[1].strip().split()[0])
                        mem_info[key] = value

                total = mem_info.get("MemTotal", 0)
                available = mem_info.get("MemAvailable", mem_info.get("MemFree", 0))

                if total > 0:
                    return 1.0 - (available / total)
        except Exception:
            pass

        # Return unknown (50%) if all methods fail
        return 0.5


class DiskProbe(BaseProbe):
    """
    Probe for monitoring disk space usage.

    DiskProbe checks the disk utilization for a specified path
    and compares against configured thresholds.

    Status Determination:
        - HEALTHY: Disk usage < warning_threshold (default 80%)
        - DEGRADED: Disk usage >= warning_threshold and < critical_threshold
        - UNHEALTHY: Disk usage >= critical_threshold (default 95%)

    Attributes:
        path: Path to monitor (default: root /)
        warning_threshold: Threshold for DEGRADED (0-1)
        critical_threshold: Threshold for UNHEALTHY (0-1)

    Example:
        >>> probe = DiskProbe(path="/var/log", warning_threshold=0.7)
        >>> result = probe.check()
        >>> print(f"Disk: {result.metadata['used_gb']:.1f}GB / {result.metadata['total_gb']:.1f}GB")
    """

    def __init__(
        self,
        path: str = "/",
        warning_threshold: float = 0.8,
        critical_threshold: float = 0.95,
        config: Optional[ProbeConfig] = None,
    ):
        """
        Initialize disk probe.

        Args:
            path: Path to monitor (default: "/")
            warning_threshold: Threshold for DEGRADED (default 0.8)
            critical_threshold: Threshold for UNHEALTHY (default 0.95)
            config: Optional probe configuration
        """
        if config is None:
            config = ProbeConfig(
                name=f"disk_probe_{path.replace('/', '_') or 'root'}",
                warning_threshold=warning_threshold,
                critical_threshold=critical_threshold,
                metadata={"path": path},
            )
        super().__init__(config)
        self._path = path
        self._warning_threshold = warning_threshold
        self._critical_threshold = critical_threshold

    def check(self) -> ProbeResult:
        """
        Execute disk space health check.

        Returns:
            ProbeResult with disk usage status

        Example:
            >>> probe = DiskProbe()
            >>> result = probe.check()
        """
        start_time = time.perf_counter()

        try:
            # Get disk usage statistics
            if PSUTIL_AVAILABLE:
                usage = psutil.disk_usage(self._path)
                used_percent = usage.percent / 100.0
                total_bytes = usage.total
                used_bytes = usage.used
                free_bytes = usage.free
            else:
                # Fallback using shutil
                total_bytes, used_bytes, free_bytes = shutil.disk_usage(self._path)
                used_percent = used_bytes / total_bytes if total_bytes > 0 else 0.0

            # Convert to GB for readability
            total_gb = total_bytes / (1024 ** 3)
            used_gb = used_bytes / (1024 ** 3)
            free_gb = free_bytes / (1024 ** 3)

            # Determine status based on thresholds
            if used_percent >= self._critical_threshold:
                status = HealthStatus.UNHEALTHY
                message = f"Critical disk usage: {used_percent * 100:.1f}%"
                recommendation = "Immediately free disk space"
            elif used_percent >= self._warning_threshold:
                status = HealthStatus.DEGRADED
                message = f"High disk usage: {used_percent * 100:.1f}%"
                recommendation = "Consider cleaning up or expanding storage"
            else:
                status = HealthStatus.HEALTHY
                message = f"Disk usage normal: {used_percent * 100:.1f}%"
                recommendation = None

            elapsed_ms = (time.perf_counter() - start_time) * 1000

            return self._create_result(
                status=status,
                message=message,
                response_time_ms=elapsed_ms,
                threshold_exceeded=used_percent >= self._warning_threshold,
                recommendation=recommendation,
                path=self._path,
                used_percent=used_percent,
                total_gb=total_gb,
                used_gb=used_gb,
                free_gb=free_gb,
                warning_threshold=self._warning_threshold,
                critical_threshold=self._critical_threshold,
            )

        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            return self._create_result(
                status=HealthStatus.UNKNOWN,
                message=f"Disk check failed: {str(e)}",
                response_time_ms=elapsed_ms,
                exception=e,
            )


class LLMConnectivityProbe(BaseProbe):
    """
    Probe for checking LLM server connectivity.

    LLMConnectivityProbe performs a lightweight health check
    against the configured LLM server endpoint.

    Status Determination:
        - HEALTHY: LLM server responds successfully
        - UNHEALTHY: LLM server unreachable or returns error
        - UNKNOWN: Check could not be performed (missing dependencies)

    Attributes:
        server_url: LLM server base URL
        timeout_seconds: Request timeout
        model_id: Optional specific model to check

    Example:
        >>> probe = LLMConnectivityProbe(server_url="http://localhost:11434")
        >>> result = probe.check()
        >>> print(f"LLM Status: {result.status}")
    """

    def __init__(
        self,
        server_url: str = "http://localhost:11434",
        timeout_seconds: float = 5.0,
        model_id: Optional[str] = None,
        config: Optional[ProbeConfig] = None,
    ):
        """
        Initialize LLM connectivity probe.

        Args:
            server_url: LLM server base URL (default: http://localhost:11434)
            timeout_seconds: Request timeout (default: 5.0)
            model_id: Optional specific model to check
            config: Optional probe configuration
        """
        if config is None:
            config = ProbeConfig(
                name="llm_connectivity_probe",
                timeout_seconds=timeout_seconds,
                metadata={
                    "server_url": server_url,
                    "model_id": model_id,
                },
            )
        super().__init__(config)
        self._server_url = server_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._model_id = model_id

    def check(self) -> ProbeResult:
        """
        Execute LLM connectivity health check.

        Returns:
            ProbeResult with connectivity status

        Example:
            >>> probe = LLMConnectivityProbe()
            >>> result = probe.check()
        """
        start_time = time.perf_counter()

        if not REQUESTS_AVAILABLE:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            return self._create_result(
                status=HealthStatus.UNKNOWN,
                message="requests library not available",
                response_time_ms=elapsed_ms,
                recommendation="Install requests: pip install requests",
            )

        try:
            # Try different health check endpoints
            endpoints_to_try = [
                "/api/tags",  # Ollama
                "/v1/models",  # OpenAI-compatible
                "/health",  # Generic health endpoint
                "/",  # Root endpoint
            ]

            last_error: Optional[Exception] = None
            response_time_ms = 0.0
            status_code: Optional[int] = None

            for endpoint in endpoints_to_try:
                url = f"{self._server_url}{endpoint}"
                try:
                    response = requests.get(
                        url,
                        timeout=self._timeout_seconds,
                    )
                    response_time_ms = (time.perf_counter() - start_time) * 1000
                    status_code = response.status_code

                    if response.status_code < 400:
                        # Success
                        elapsed_ms = response_time_ms
                        return self._create_result(
                            status=HealthStatus.HEALTHY,
                            message=f"LLM server responding ({status_code})",
                            response_time_ms=elapsed_ms,
                            server_url=self._server_url,
                            endpoint=endpoint,
                            status_code=status_code,
                        )
                    else:
                        last_error = Exception(f"HTTP {status_code}")

                except requests.exceptions.RequestException as e:
                    last_error = e
                    continue

            # All endpoints failed
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            return self._create_result(
                status=HealthStatus.UNHEALTHY,
                message=f"LLM server unreachable: {str(last_error)}",
                response_time_ms=elapsed_ms,
                server_url=self._server_url,
                exception=last_error,
                recommendation="Verify LLM server is running",
            )

        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            return self._create_result(
                status=HealthStatus.UNKNOWN,
                message=f"LLM check failed: {str(e)}",
                response_time_ms=elapsed_ms,
                exception=e,
            )


class DatabaseProbe(BaseProbe):
    """
    Probe for checking database connectivity.

    DatabaseProbe verifies database connection by executing
    a simple query (typically SELECT 1 or equivalent).

    Status Determination:
        - HEALTHY: Database responds to queries
        - UNHEALTHY: Database connection failed
        - UNKNOWN: Check could not be performed

    Attributes:
        connection_factory: Callable that returns a connection
        test_query: Query to execute (default: "SELECT 1")
        timeout_seconds: Connection/query timeout

    Example:
        >>> import sqlite3
        >>> probe = DatabaseProbe(
        ...     connection_factory=lambda: sqlite3.connect("test.db"),
        ...     test_query="SELECT 1"
        ... )
        >>> result = probe.check()
        >>> print(f"DB Status: {result.status}")
    """

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        test_query: str = "SELECT 1",
        timeout_seconds: float = 5.0,
        config: Optional[ProbeConfig] = None,
    ):
        """
        Initialize database probe.

        Args:
            connection_factory: Callable returning DB connection
            test_query: Query to execute for health check
            timeout_seconds: Timeout for connection/query
            config: Optional probe configuration
        """
        if config is None:
            config = ProbeConfig(
                name="database_probe",
                timeout_seconds=timeout_seconds,
            )
        super().__init__(config)
        self._connection_factory = connection_factory
        self._test_query = test_query
        self._timeout_seconds = timeout_seconds

    def check(self) -> ProbeResult:
        """
        Execute database connectivity health check.

        Returns:
            ProbeResult with database connectivity status
        """
        start_time = time.perf_counter()
        conn = None

        try:
            # Create connection
            conn = self._connection_factory()

            # Execute test query
            cursor = conn.cursor()
            cursor.execute(self._test_query)
            cursor.fetchone()

            elapsed_ms = (time.perf_counter() - start_time) * 1000

            return self._create_result(
                status=HealthStatus.HEALTHY,
                message="Database responding",
                response_time_ms=elapsed_ms,
                query=self._test_query,
            )

        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            return self._create_result(
                status=HealthStatus.UNHEALTHY,
                message=f"Database connection failed: {str(e)}",
                response_time_ms=elapsed_ms,
                exception=e,
                recommendation="Check database server and connection string",
            )

        finally:
            # Clean up connection
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


class MCPProbe(BaseProbe):
    """
    Probe for checking MCP (Model Context Protocol) server connectivity.

    MCPProbe verifies the MCP server is running and responsive.

    Status Determination:
        - HEALTHY: MCP server responds to ping/health requests
        - UNHEALTHY: MCP server unreachable
        - UNKNOWN: Check could not be performed

    Example:
        >>> probe = MCPProbe(server_url="ws://localhost:8080")
        >>> result = probe.check()
    """

    def __init__(
        self,
        server_url: str = "ws://localhost:8080",
        timeout_seconds: float = 5.0,
        config: Optional[ProbeConfig] = None,
    ):
        """
        Initialize MCP probe.

        Args:
            server_url: MCP server URL
            timeout_seconds: Connection timeout
            config: Optional probe configuration
        """
        if config is None:
            config = ProbeConfig(
                name="mcp_probe",
                timeout_seconds=timeout_seconds,
                metadata={"server_url": server_url},
            )
        super().__init__(config)
        self._server_url = server_url
        self._timeout_seconds = timeout_seconds

    def check(self) -> ProbeResult:
        """
        Execute MCP connectivity health check.

        Returns:
            ProbeResult with MCP connectivity status
        """
        start_time = time.perf_counter()

        # For now, just check if MCP server URL is reachable via HTTP
        # In a full implementation, this would use the MCP client protocol
        try:
            if REQUESTS_AVAILABLE:
                # Try to connect to MCP server HTTP endpoint if available
                http_url = self._server_url.replace("ws://", "http://").replace(
                    "wss://", "https://"
                )
                response = requests.get(
                    f"{http_url}/health",
                    timeout=self._timeout_seconds,
                )
                elapsed_ms = (time.perf_counter() - start_time) * 1000

                if response.status_code < 400:
                    return self._create_result(
                        status=HealthStatus.HEALTHY,
                        message="MCP server responding",
                        response_time_ms=elapsed_ms,
                        server_url=self._server_url,
                    )
                else:
                    return self._create_result(
                        status=HealthStatus.DEGRADED,
                        message=f"MCP server returned {response.status_code}",
                        response_time_ms=elapsed_ms,
                        server_url=self._server_url,
                    )
            else:
                # Without requests, we can't verify connectivity
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                return self._create_result(
                    status=HealthStatus.UNKNOWN,
                    message="Cannot verify MCP connectivity (requests unavailable)",
                    response_time_ms=elapsed_ms,
                )

        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            return self._create_result(
                status=HealthStatus.UNHEALTHY,
                message=f"MCP server unreachable: {str(e)}",
                response_time_ms=elapsed_ms,
                exception=e,
            )


class CacheProbe(BaseProbe):
    """
    Probe for checking cache layer health.

    CacheProbe verifies the cache layer is operational by
    performing a simple set/get/delete operation.

    Status Determination:
        - HEALTHY: Cache operations successful
        - DEGRADED: Cache operations slow (>threshold)
        - UNHEALTHY: Cache operations failed

    Example:
        >>> cache = SomeCache()
        >>> probe = CacheProbe(
        ...     cache=cache,
        ...     test_key="health_check",
        ...     slow_threshold_ms=100
        ... )
        >>> result = probe.check()
    """

    def __init__(
        self,
        cache: Any,
        test_key: str = "_health_check",
        test_value: str = "hc",
        slow_threshold_ms: float = 100.0,
        config: Optional[ProbeConfig] = None,
    ):
        """
        Initialize cache probe.

        Args:
            cache: Cache instance with get/set/delete methods
            test_key: Key to use for health check
            test_value: Value to use for health check
            slow_threshold_ms: Threshold for DEGRADED status
            config: Optional probe configuration
        """
        if config is None:
            config = ProbeConfig(
                name="cache_probe",
                metadata={
                    "test_key": test_key,
                    "slow_threshold_ms": slow_threshold_ms,
                },
            )
        super().__init__(config)
        self._cache = cache
        self._test_key = test_key
        self._test_value = test_value
        self._slow_threshold_ms = slow_threshold_ms

    def check(self) -> ProbeResult:
        """
        Execute cache health check.

        Returns:
            ProbeResult with cache health status
        """
        start_time = time.perf_counter()

        try:
            # Test set operation
            set_start = time.perf_counter()
            if hasattr(self._cache, "set"):
                self._cache.set(self._test_key, self._test_value)
            elif hasattr(self._cache, "__setitem__"):
                self._cache[self._test_key] = self._test_value
            else:
                raise AttributeError("Cache has no set method")
            set_time = (time.perf_counter() - set_start) * 1000

            # Test get operation
            get_start = time.perf_counter()
            if hasattr(self._cache, "get"):
                value = self._cache.get(self._test_key)
            elif hasattr(self._cache, "__getitem__"):
                value = self._cache[self._test_key]
            else:
                raise AttributeError("Cache has no get method")
            get_time = (time.perf_counter() - get_start) * 1000

            # Verify value
            if value != self._test_value:
                raise ValueError(f"Cache value mismatch: expected {self._test_value}, got {value}")

            # Test delete operation (cleanup)
            if hasattr(self._cache, "delete"):
                self._cache.delete(self._test_key)
            elif hasattr(self._cache, "__delitem__"):
                del self._cache[self._test_key]

            total_time = (time.perf_counter() - start_time) * 1000

            # Determine status based on response time
            if total_time > self._slow_threshold_ms:
                status = HealthStatus.DEGRADED
                message = f"Cache operations slow: {total_time:.1f}ms"
                recommendation = "Consider scaling cache layer"
            else:
                status = HealthStatus.HEALTHY
                message = f"Cache responding in {total_time:.1f}ms"
                recommendation = None

            return self._create_result(
                status=status,
                message=message,
                response_time_ms=total_time,
                set_time_ms=set_time,
                get_time_ms=get_time,
                total_time_ms=total_time,
                threshold_exceeded=total_time > self._slow_threshold_ms,
                recommendation=recommendation,
            )

        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            return self._create_result(
                status=HealthStatus.UNHEALTHY,
                message=f"Cache check failed: {str(e)}",
                response_time_ms=elapsed_ms,
                exception=e,
                recommendation="Check cache server connectivity",
            )


class RAGProbe(BaseProbe):
    """
    Probe for checking RAG (Retrieval-Augmented Generation) index health.

    RAGProbe verifies the RAG index is loaded and searchable by
    performing a test query.

    Status Determination:
        - HEALTHY: RAG index responding with good latency
        - DEGRADED: RAG index responding but slow
        - UNHEALTHY: RAG index not accessible or empty

    Example:
        >>> rag_index = SomeRAGIndex()
        >>> probe = RAGProbe(
        ...     rag_index=rag_index,
        ...     test_query="test",
        ...     slow_threshold_ms=200
        ... )
        >>> result = probe.check()
    """

    def __init__(
        self,
        rag_index: Any,
        test_query: str = "health check",
        min_results: int = 0,
        slow_threshold_ms: float = 200.0,
        config: Optional[ProbeConfig] = None,
    ):
        """
        Initialize RAG probe.

        Args:
            rag_index: RAG index instance with search/query method
            test_query: Query to use for health check
            min_results: Minimum expected results (0 = any)
            slow_threshold_ms: Threshold for DEGRADED status
            config: Optional probe configuration
        """
        if config is None:
            config = ProbeConfig(
                name="rag_probe",
                metadata={
                    "test_query": test_query,
                    "min_results": min_results,
                    "slow_threshold_ms": slow_threshold_ms,
                },
            )
        super().__init__(config)
        self._rag_index = rag_index
        self._test_query = test_query
        self._min_results = min_results
        self._slow_threshold_ms = slow_threshold_ms

    def check(self) -> ProbeResult:
        """
        Execute RAG index health check.

        Returns:
            ProbeResult with RAG health status
        """
        start_time = time.perf_counter()

        try:
            # Check if index is loaded/initialized
            if hasattr(self._rag_index, "is_loaded"):
                if not self._rag_index.is_loaded:
                    elapsed_ms = (time.perf_counter() - start_time) * 1000
                    return self._create_result(
                        status=HealthStatus.UNHEALTHY,
                        message="RAG index not loaded",
                        response_time_ms=elapsed_ms,
                        recommendation="Load the RAG index",
                    )

            # Execute test query
            if hasattr(self._rag_index, "search"):
                results = self._rag_index.search(self._test_query, top_k=1)
            elif hasattr(self._rag_index, "query"):
                results = self._rag_index.query(self._test_query, top_k=1)
            elif hasattr(self._rag_index, "__len__"):
                # Just check if index has documents
                results = list(range(len(self._rag_index)))
            else:
                raise AttributeError("RAG index has no search/query method")

            elapsed_ms = (time.perf_counter() - start_time) * 1000

            # Check result count
            result_count = len(results) if results else 0
            if result_count < self._min_results:
                status = HealthStatus.DEGRADED
                message = f"RAG index has few results: {result_count}"
                recommendation = "Consider rebuilding RAG index"
            elif elapsed_ms > self._slow_threshold_ms:
                status = HealthStatus.DEGRADED
                message = f"RAG query slow: {elapsed_ms:.1f}ms"
                recommendation = "Consider optimizing RAG index"
            else:
                status = HealthStatus.HEALTHY
                message = f"RAG index responding in {elapsed_ms:.1f}ms"
                recommendation = None

            return self._create_result(
                status=status,
                message=message,
                response_time_ms=elapsed_ms,
                result_count=result_count,
                query_time_ms=elapsed_ms,
                threshold_exceeded=elapsed_ms > self._slow_threshold_ms,
                recommendation=recommendation,
            )

        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            return self._create_result(
                status=HealthStatus.UNHEALTHY,
                message=f"RAG check failed: {str(e)}",
                response_time_ms=elapsed_ms,
                exception=e,
                recommendation="Check RAG index initialization",
            )


# Convenience function to create all standard probesbes
def create_standard_probes(
    llm_url: str = "http://localhost:11434",
    db_connection_factory: Optional[Callable[[], Any]] = None,
    cache_instance: Optional[Any] = None,
    rag_index: Optional[Any] = None,
) -> List[BaseProbe]:
    """
    Create a standard set of health probes.

    Creates probes for all critical GAIA components with sensible defaults.

    Args:
        llm_url: LLM server URL
        db_connection_factory: Optional DB connection factory
        cache_instance: Optional cache instance
        rag_index: Optional RAG index instance

    Returns:
        List of configured probes

    Example:
        >>> probes = create_standard_probes()
        >>> for probe in probes:
        ...     result = probe.check()
        ...     print(f"{probe.name}: {result.status}")
    """
    probes: List[BaseProbe] = [
        MemoryProbe(),
        DiskProbe(),
        LLMConnectivityProbe(server_url=llm_url),
    ]

    if db_connection_factory:
        probes.append(DatabaseProbe(connection_factory=db_connection_factory))

    if cache_instance:
        probes.append(CacheProbe(cache=cache_instance))

    if rag_index:
        probes.append(RAGProbe(rag_index=rag_index))

    return probes
