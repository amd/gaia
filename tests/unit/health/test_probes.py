"""
Unit tests for Health Probes.

Covers:
- MemoryProbe
- DiskProbe
- LLMConnectivityProbe
- DatabaseProbe
- BaseProbe abstract class
- create_standard_probes function
"""

import pytest
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock, patch

from gaia.health.models import HealthStatus, ProbeResult
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


class TestProbeConfig:
    """Test ProbeConfig dataclass."""

    def test_init_default_values(self):
        """Should initialize with default values."""
        config = ProbeConfig(name="test")

        assert config.name == "test"
        assert config.enabled is True
        assert config.timeout_seconds == 5.0
        assert config.warning_threshold == 0.8
        assert config.critical_threshold == 0.95
        assert config.check_interval_seconds == 30.0
        assert config.metadata == {}

    def test_init_custom_values(self):
        """Should initialize with custom values."""
        config = ProbeConfig(
            name="custom",
            enabled=False,
            timeout_seconds=10.0,
            warning_threshold=0.7,
            critical_threshold=0.9,
            metadata={"key": "value"},
        )

        assert config.name == "custom"
        assert config.enabled is False
        assert config.timeout_seconds == 10.0
        assert config.warning_threshold == 0.7
        assert config.critical_threshold == 0.9
        assert config.metadata["key"] == "value"


class TestBaseProbe:
    """Test BaseProbe abstract class."""

    def test_subclass_must_implement_check(self):
        """Should require check() implementation."""
        class IncompleteProbe(BaseProbe):
            pass

        # ABC prevents instantiation of incomplete subclasses
        with pytest.raises(TypeError):
            IncompleteProbe()

    def test_base_probe_properties(self):
        """Should have correct properties."""
        class TestProbe(BaseProbe):
            def check(self) -> ProbeResult:
                return ProbeResult("test", HealthStatus.HEALTHY, "OK")

        config = ProbeConfig(name="test_probe", enabled=True)
        probe = TestProbe(config)

        assert probe.name == "test_probe"
        assert probe.is_enabled is True
        assert probe.config is config

    def test_last_result_initially_none(self):
        """Should have None as last result initially."""
        class TestProbe(BaseProbe):
            def check(self) -> ProbeResult:
                return ProbeResult("test", HealthStatus.HEALTHY, "OK")

        probe = TestProbe()

        assert probe.last_result is None
        assert probe.last_check_time is None

    def test_check_cached(self):
        """Should cache check result."""
        call_count = [0]

        class TestProbe(BaseProbe):
            def check(self) -> ProbeResult:
                call_count[0] += 1
                return ProbeResult("test", HealthStatus.HEALTHY, "OK")

        probe = TestProbe()

        # First call - should execute
        result1 = probe.check_cached(cache_ttl_seconds=1.0)
        assert call_count[0] == 1

        # Second call within TTL - should use cache
        result2 = probe.check_cached(cache_ttl_seconds=1.0)
        assert call_count[0] == 1  # Not incremented

        assert result1 is result2

    def test_check_cached_after_ttl(self):
        """Should re-execute after TTL expires."""
        call_count = [0]

        class TestProbe(BaseProbe):
            def check(self) -> ProbeResult:
                call_count[0] += 1
                return ProbeResult("test", HealthStatus.HEALTHY, "OK")

        probe = TestProbe()

        # First call
        probe.check_cached(cache_ttl_seconds=0.01)
        assert call_count[0] == 1

        # Wait for TTL to expire
        time.sleep(0.02)

        # Second call after TTL - should re-execute
        probe.check_cached(cache_ttl_seconds=0.01)
        assert call_count[0] == 2

    def test_create_result_helper(self):
        """Should create result with helper method."""
        class TestProbe(BaseProbe):
            def check(self) -> ProbeResult:
                return self._create_result(
                    status=HealthStatus.HEALTHY,
                    message="Test OK",
                    response_time_ms=10.0,
                    threshold_exceeded=False,
                    recommendation=None,
                    custom_field="value",
                )

        probe = TestProbe()
        result = probe.check()

        assert result.probe_name == "TestProbe"
        assert result.status == HealthStatus.HEALTHY
        assert result.message == "Test OK"
        assert result.response_time_ms == 10.0
        assert result.threshold_exceeded is False
        assert result.metadata["custom_field"] == "value"


class TestMemoryProbe:
    """Test MemoryProbe."""

    def test_init_default_values(self):
        """Should initialize with default values."""
        probe = MemoryProbe()

        assert probe.name == "memory_probe"
        assert probe._warning_threshold == 0.8
        assert probe._critical_threshold == 0.95

    def test_init_custom_thresholds(self):
        """Should initialize with custom thresholds."""
        probe = MemoryProbe(
            warning_threshold=0.7,
            critical_threshold=0.85,
        )

        assert probe._warning_threshold == 0.7
        assert probe._critical_threshold == 0.85

    def test_check_returns_probe_result(self):
        """Should return ProbeResult."""
        probe = MemoryProbe()

        result = probe.check()

        assert isinstance(result, ProbeResult)
        assert result.probe_name == "memory_probe"

    def test_check_healthy_memory(self):
        """Should return HEALTHY for normal memory usage."""
        # Mock psutil to simulate healthy memory
        with patch('gaia.health.probes.psutil.virtual_memory') as mock_memory:
            mock_memory.return_value = Mock(
                percent=50.0,  # 50% usage
                used=4 * 1024 * 1024 * 1024,
                total=8 * 1024 * 1024 * 1024,
                available=4 * 1024 * 1024 * 1024,
            )

            probe = MemoryProbe(warning_threshold=0.8, critical_threshold=0.95)
            result = probe.check()

            assert result.status == HealthStatus.HEALTHY
            assert result.metadata["used_percent"] == 0.5

    def test_check_degraded_memory(self):
        """Should return DEGRADED for high memory usage."""
        with patch('gaia.health.probes.psutil.virtual_memory') as mock_memory:
            mock_memory.return_value = Mock(
                percent=85.0,  # 85% usage - above warning
                used=6.8 * 1024 * 1024 * 1024,
                total=8 * 1024 * 1024 * 1024,
                available=1.2 * 1024 * 1024 * 1024,
            )

            probe = MemoryProbe(warning_threshold=0.8, critical_threshold=0.95)
            result = probe.check()

            assert result.status == HealthStatus.DEGRADED
            assert result.threshold_exceeded is True
            assert result.recommendation is not None

    def test_check_unhealthy_memory(self):
        """Should return UNHEALTHY for critical memory usage."""
        with patch('gaia.health.probes.psutil.virtual_memory') as mock_memory:
            mock_memory.return_value = Mock(
                percent=97.0,  # 97% usage - above critical
                used=7.76 * 1024 * 1024 * 1024,
                total=8 * 1024 * 1024 * 1024,
                available=0.24 * 1024 * 1024 * 1024,
            )

            probe = MemoryProbe(warning_threshold=0.8, critical_threshold=0.95)
            result = probe.check()

            assert result.status == HealthStatus.UNHEALTHY
            assert result.threshold_exceeded is True

    def test_check_without_psutil(self):
        """Should use fallback without psutil."""
        with patch('gaia.health.probes.PSUTIL_AVAILABLE', False):
            probe = MemoryProbe()
            result = probe.check()

            # Should not raise, returns some result
            assert isinstance(result, ProbeResult)
            # Fallback returns 50% (unknown)
            assert result.metadata.get("used_percent", 0.5) >= 0

    def test_response_time_under_50ms(self):
        """Should complete in under 50ms."""
        probe = MemoryProbe()

        result = probe.check()

        assert result.response_time_ms < 50


class TestDiskProbe:
    """Test DiskProbe."""

    def test_init_default_values(self):
        """Should initialize with default values."""
        probe = DiskProbe()

        assert "disk_probe" in probe.name
        assert probe._path == "/"
        assert probe._warning_threshold == 0.8
        assert probe._critical_threshold == 0.95

    def test_init_custom_path(self):
        """Should initialize with custom path."""
        probe = DiskProbe(path="/tmp")

        assert probe._path == "/tmp"
        assert "tmp" in probe.name

    def test_init_custom_thresholds(self):
        """Should initialize with custom thresholds."""
        probe = DiskProbe(
            warning_threshold=0.7,
            critical_threshold=0.9,
        )

        assert probe._warning_threshold == 0.7
        assert probe._critical_threshold == 0.9

    def test_check_returns_probe_result(self):
        """Should return ProbeResult."""
        probe = DiskProbe()

        result = probe.check()

        assert isinstance(result, ProbeResult)
        assert "disk" in result.probe_name

    def test_check_healthy_disk(self):
        """Should return HEALTHY for normal disk usage."""
        with patch('gaia.health.probes.psutil.disk_usage') as mock_usage:
            mock_usage.return_value = Mock(
                percent=50.0,
                total=100 * 1024 ** 3,
                used=50 * 1024 ** 3,
                free=50 * 1024 ** 3,
            )

            probe = DiskProbe(warning_threshold=0.8, critical_threshold=0.95)
            result = probe.check()

            assert result.status == HealthStatus.HEALTHY
            assert "total_gb" in result.metadata

    def test_check_degraded_disk(self):
        """Should return DEGRADED for high disk usage."""
        with patch('gaia.health.probes.psutil.disk_usage') as mock_usage:
            mock_usage.return_value = Mock(
                percent=85.0,
                total=100 * 1024 ** 3,
                used=85 * 1024 ** 3,
                free=15 * 1024 ** 3,
            )

            probe = DiskProbe(warning_threshold=0.8, critical_threshold=0.95)
            result = probe.check()

            assert result.status == HealthStatus.DEGRADED
            assert result.threshold_exceeded is True

    def test_check_unhealthy_disk(self):
        """Should return UNHEALTHY for critical disk usage."""
        with patch('gaia.health.probes.psutil.disk_usage') as mock_usage:
            mock_usage.return_value = Mock(
                percent=97.0,
                total=100 * 1024 ** 3,
                used=97 * 1024 ** 3,
                free=3 * 1024 ** 3,
            )

            probe = DiskProbe(warning_threshold=0.8, critical_threshold=0.95)
            result = probe.check()

            assert result.status == HealthStatus.UNHEALTHY

    def test_check_without_psutil(self):
        """Should use shutil fallback without psutil."""
        with patch('gaia.health.probes.PSUTIL_AVAILABLE', False):
            probe = DiskProbe(path=tempfile.gettempdir())
            result = probe.check()

            # Should not raise
            assert isinstance(result, ProbeResult)

    def test_response_time_under_50ms(self):
        """Should complete in under 50ms."""
        probe = DiskProbe()

        result = probe.check()

        assert result.response_time_ms < 50


class TestLLMConnectivityProbe:
    """Test LLMConnectivityProbe."""

    def test_init_default_values(self):
        """Should initialize with default values."""
        probe = LLMConnectivityProbe()

        assert probe.name == "llm_connectivity_probe"
        assert probe._server_url == "http://localhost:11434"
        assert probe._timeout_seconds == 5.0

    def test_init_custom_url(self):
        """Should initialize with custom URL."""
        probe = LLMConnectivityProbe(server_url="http://custom:8080")

        assert probe._server_url == "http://custom:8080"

    def test_check_without_requests(self):
        """Should return UNKNOWN without requests library."""
        with patch('gaia.health.probes.REQUESTS_AVAILABLE', False):
            probe = LLMConnectivityProbe()
            result = probe.check()

            assert result.status == HealthStatus.UNKNOWN
            assert "requests" in result.message.lower()

    def test_check_server_unreachable(self):
        """Should return UNHEALTHY when server unreachable."""
        with patch('gaia.health.probes.requests.get') as mock_get:
            from requests.exceptions import RequestException
            mock_get.side_effect = RequestException("Connection refused")

            probe = LLMConnectivityProbe()
            result = probe.check()

            # Connection exceptions result in UNHEALTHY status
            assert result.status == HealthStatus.UNHEALTHY

    def test_check_server_healthy(self):
        """Should return HEALTHY when server responding."""
        with patch('gaia.health.probes.requests.get') as mock_get:
            mock_get.return_value = Mock(status_code=200)

            probe = LLMConnectivityProbe()
            result = probe.check()

            assert result.status == HealthStatus.HEALTHY

    def test_check_server_degraded(self):
        """Should handle degraded server response."""
        with patch('gaia.health.probes.requests.get') as mock_get:
            # Return a 503 status (service unavailable)
            mock_get.return_value = Mock(status_code=503)

            probe = LLMConnectivityProbe()
            result = probe.check()

            # 503 response means server is up but degraded
            assert result.status in (HealthStatus.DEGRADED, HealthStatus.UNHEALTHY, HealthStatus.UNKNOWN)


class TestDatabaseProbe:
    """Test DatabaseProbe."""

    def test_init_default_values(self):
        """Should initialize with default values."""
        def factory():
            pass

        probe = DatabaseProbe(connection_factory=factory)

        assert probe.name == "database_probe"
        assert probe._test_query == "SELECT 1"

    def test_init_custom_query(self):
        """Should initialize with custom query."""
        def factory():
            pass

        probe = DatabaseProbe(
            connection_factory=factory,
            test_query="SELECT 1 FROM dual",
        )

        assert probe._test_query == "SELECT 1 FROM dual"

    def test_check_database_healthy(self):
        """Should return HEALTHY for working database."""
        # Create temp SQLite database
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            def factory():
                return sqlite3.connect(db_path)

            probe = DatabaseProbe(connection_factory=factory)
            result = probe.check()

            assert result.status == HealthStatus.HEALTHY
            assert result.response_time_ms < 50
        finally:
            Path(db_path).unlink(missing_ok=True)

    def test_check_database_unhealthy(self):
        """Should return UNHEALTHY for failing database."""
        def factory():
            raise ConnectionError("Database unavailable")

        probe = DatabaseProbe(connection_factory=factory)
        result = probe.check()

        assert result.status == HealthStatus.UNHEALTHY
        assert "Database" in result.message


class TestMCPProbe:
    """Test MCPProbe."""

    def test_init_default_values(self):
        """Should initialize with default values."""
        probe = MCPProbe()

        assert probe.name == "mcp_probe"
        assert probe._server_url == "ws://localhost:8080"

    def test_init_custom_url(self):
        """Should initialize with custom URL."""
        probe = MCPProbe(server_url="ws://custom:9000")

        assert probe._server_url == "ws://custom:9000"

    def test_check_without_requests(self):
        """Should return UNKNOWN without requests."""
        with patch('gaia.health.probes.REQUESTS_AVAILABLE', False):
            probe = MCPProbe()
            result = probe.check()

            assert result.status == HealthStatus.UNKNOWN

    def test_check_server_unreachable(self):
        """Should return UNHEALTHY when unreachable."""
        with patch('gaia.health.probes.requests.get') as mock_get:
            mock_get.side_effect = Exception("Connection refused")

            probe = MCPProbe()
            result = probe.check()

            assert result.status == HealthStatus.UNHEALTHY

    def test_check_server_healthy(self):
        """Should return HEALTHY when server responding."""
        with patch('gaia.health.probes.requests.get') as mock_get:
            mock_get.return_value = Mock(status_code=200)

            probe = MCPProbe()
            result = probe.check()

            assert result.status == HealthStatus.HEALTHY


class TestCacheProbe:
    """Test CacheProbe."""

    def test_init_default_values(self):
        """Should initialize with default values."""
        mock_cache = Mock()
        probe = CacheProbe(cache=mock_cache)

        assert probe.name == "cache_probe"
        assert probe._test_key == "_health_check"

    def test_check_cache_healthy(self):
        """Should return HEALTHY for working cache."""
        mock_cache = Mock()
        mock_cache.get.return_value = "hc"

        probe = CacheProbe(cache=mock_cache)
        result = probe.check()

        assert result.status == HealthStatus.HEALTHY
        assert mock_cache.set.called
        assert mock_cache.get.called
        assert mock_cache.delete.called

    def test_check_cache_slow(self):
        """Should return DEGRADED for slow cache."""
        mock_cache = Mock()

        def slow_set(*args):
            time.sleep(0.15)

        mock_cache.set.side_effect = slow_set
        mock_cache.get.return_value = "hc"

        probe = CacheProbe(cache=mock_cache, slow_threshold_ms=100)
        result = probe.check()

        assert result.status == HealthStatus.DEGRADED
        assert result.threshold_exceeded is True

    def test_check_cache_unhealthy(self):
        """Should return UNHEALTHY for failing cache."""
        mock_cache = Mock()
        mock_cache.set.side_effect = Exception("Cache unavailable")

        probe = CacheProbe(cache=mock_cache)
        result = probe.check()

        assert result.status == HealthStatus.UNHEALTHY


class TestRAGProbe:
    """Test RAGProbe."""

    def test_init_default_values(self):
        """Should initialize with default values."""
        mock_index = Mock()
        probe = RAGProbe(rag_index=mock_index)

        assert probe.name == "rag_probe"
        assert probe._test_query == "health check"

    def test_check_rag_healthy(self):
        """Should return HEALTHY for working RAG index."""
        mock_index = Mock()
        mock_index.search.return_value = [{"doc": "result"}]

        probe = RAGProbe(rag_index=mock_index)
        result = probe.check()

        assert result.status == HealthStatus.HEALTHY

    def test_check_rag_not_loaded(self):
        """Should return UNHEALTHY when index not loaded."""
        mock_index = Mock()
        mock_index.is_loaded = False

        probe = RAGProbe(rag_index=mock_index)
        result = probe.check()

        assert result.status == HealthStatus.UNHEALTHY
        assert "not loaded" in result.message.lower()

    def test_check_rag_few_results(self):
        """Should return DEGRADED for few results."""
        mock_index = Mock()
        mock_index.search.return_value = []  # No results

        probe = RAGProbe(rag_index=mock_index, min_results=5)
        result = probe.check()

        assert result.status == HealthStatus.DEGRADED

    def test_check_rag_slow(self):
        """Should return DEGRADED for slow queries."""
        mock_index = Mock()

        def slow_search(query, top_k=1):
            time.sleep(0.25)
            return [{"doc": "result"}]

        mock_index.search.side_effect = slow_search

        probe = RAGProbe(rag_index=mock_index, slow_threshold_ms=200)
        result = probe.check()

        assert result.status == HealthStatus.DEGRADED
        assert result.threshold_exceeded is True


class TestCreateStandardProbes:
    """Test create_standard_probes function."""

    def test_create_basic_probes(self):
        """Should create basic probes."""
        probes = create_standard_probes()

        assert len(probes) == 3  # Memory, Disk, LLM
        assert any(isinstance(p, MemoryProbe) for p in probes)
        assert any(isinstance(p, DiskProbe) for p in probes)
        assert any(isinstance(p, LLMConnectivityProbe) for p in probes)

    def test_create_with_all_options(self):
        """Should create probes with all options."""
        def db_factory():
            pass

        mock_cache = Mock()
        mock_index = Mock()

        probes = create_standard_probes(
            llm_url="http://custom:8080",
            db_connection_factory=db_factory,
            cache_instance=mock_cache,
            rag_index=mock_index,
        )

        # Memory, Disk, LLM, Database, Cache, RAG
        assert len(probes) == 6

    def test_llm_url_propagated(self):
        """Should propagate LLM URL."""
        probes = create_standard_probes(llm_url="http://test:9999")

        llm_probe = next(p for p in probes if isinstance(p, LLMConnectivityProbe))
        assert llm_probe._server_url == "http://test:9999"


class TestProbeResponseTime:
    """Test probe response time requirements."""

    def test_memory_probe_under_50ms(self):
        """MemoryProbe should complete in under 50ms."""
        probe = MemoryProbe()

        for _ in range(5):
            result = probe.check()
            assert result.response_time_ms < 50, f"Memory probe took {result.response_time_ms}ms"

    def test_disk_probe_under_50ms(self):
        """DiskProbe should complete in under 50ms."""
        probe = DiskProbe(path=tempfile.gettempdir())

        for _ in range(5):
            result = probe.check()
            assert result.response_time_ms < 50, f"Disk probe took {result.response_time_ms}ms"

    def test_database_probe_under_50ms(self):
        """DatabaseProbe should complete in under 50ms."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            def factory():
                return sqlite3.connect(db_path)

            probe = DatabaseProbe(connection_factory=factory)

            for _ in range(5):
                result = probe.check()
                assert result.response_time_ms < 50, f"DB probe took {result.response_time_ms}ms"
        finally:
            Path(db_path).unlink(missing_ok=True)
