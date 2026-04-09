"""
Health Monitoring Module for GAIA.

This module provides comprehensive health monitoring capabilities for
GAIA components and services, including:

- Liveness probes: Is the component running?
- Readiness probes: Is the component ready to serve?
- Startup probes: Is the component still initializing?
- Custom health checks: User-defined health checks
- Built-in probes: Memory, Disk, LLM, Database, MCP, Cache, RAG
- Aggregated health status with degradation detection
- Thread-safe operations for concurrent environments
- Integration with MetricsCollector for health metrics

Module Structure:
    - models.py: Data models (HealthStatus, HealthCheckResult, AggregatedHealthStatus)
    - checker.py: HealthChecker class for managing health checks
    - probes.py: Built-in health probes (Memory, Disk, LLM, etc.)

Example:
    >>> from gaia.health import (
    ...     HealthChecker,
    ...     HealthStatus,
    ...     HealthCheckResult,
    ...     MemoryProbe,
    ...     DiskProbe,
    ...     LLMConnectivityProbe,
    ... )
    >>>
    >>> # Create health checker
    >>> checker = HealthChecker(service_name="gaia-api")
    >>>
    >>> # Register probes
    >>> checker.register_probe(MemoryProbe())
    >>> checker.register_probe(DiskProbe())
    >>> checker.register_probe(LLMConnectivityProbe())
    >>>
    >>> # Perform health checks
    >>> import asyncio
    >>> async def check_health():
    ...     liveness = await checker.check_liveness()
    ...     print(f"Service alive: {liveness.is_healthy}")
    ...
    ...     health = await checker.get_aggregated_health()
    ...     print(health.summary())
    >>>
    >>> asyncio.run(check_health())
"""

from gaia.health.checker import (
    HealthChecker,
    RegisteredCheck,
    get_health_checker,
    reset_health_checker,
)
from gaia.health.models import (
    AggregatedHealthStatus,
    HealthCheckResult,
    HealthStatus,
    ProbeResult,
)
from gaia.health.probes import (
    BaseProbe,
    CacheProbe,
    DatabaseProbe,
    DiskProbe,
    LLMConnectivityProbe,
    MCPProbe,
    MemoryProbe,
    ProbeConfig,
    RAGProbe,
    create_standard_probes,
)

__version__ = "1.0.0"

__all__ = [
    # Models
    "HealthStatus",
    "HealthCheckResult",
    "AggregatedHealthStatus",
    "ProbeResult",
    # Checker
    "HealthChecker",
    "RegisteredCheck",
    "get_health_checker",
    "reset_health_checker",
    # Probes
    "BaseProbe",
    "ProbeConfig",
    "MemoryProbe",
    "DiskProbe",
    "LLMConnectivityProbe",
    "DatabaseProbe",
    "MCPProbe",
    "CacheProbe",
    "RAGProbe",
    "create_standard_probes",
    # Version
    "__version__",
]
