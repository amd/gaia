# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for PluginRegistry and PluginMetadata.

This test suite validates:
- PluginMetadata creation, validation, and serialization
- PluginRegistry singleton behavior
- Plugin registration, retrieval, and execution
- Plugin lifecycle management (enable/disable)
- Lazy plugin loading
- Thread safety
- Performance requirements (<1ms lookup)

Quality Gate 4 Criteria Covered:
- PERF-006: Plugin registry latency (<1ms lookup)
- THREAD-004: Thread safety (100+ concurrent threads)
"""

import asyncio
import pytest
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from gaia.core.plugin import PluginMetadata, PluginRegistry


# =============================================================================
# PluginMetadata Tests
# =============================================================================

class TestPluginMetadataCreation:
    """Tests for PluginMetadata creation and initialization."""

    def test_create_minimal_metadata(self):
        """Test creating metadata with minimal required fields."""
        metadata = PluginMetadata(name="test-plugin")
        assert metadata.name == "test-plugin"
        assert metadata.version == "1.0.0"
        assert metadata.description == ""
        assert metadata.author == ""
        assert metadata.enabled is True

    def test_create_metadata_with_all_fields(self):
        """Test creating metadata with all fields specified."""
        metadata = PluginMetadata(
            name="full-plugin",
            version="2.0.0",
            description="A full plugin",
            author="John Doe",
            author_email="john@example.com",
            homepage="https://example.com",
            license="Apache-2.0",
            dependencies=["dep1", "dep2"],
            tags=["tag1", "tag2"],
        )
        assert metadata.name == "full-plugin"
        assert metadata.version == "2.0.0"
        assert metadata.description == "A full plugin"
        assert metadata.author == "John Doe"
        assert metadata.license == "Apache-2.0"
        assert len(metadata.dependencies) == 2
        assert len(metadata.tags) == 2

    def test_post_init_sets_timestamps(self):
        """Test that __post_init__ sets timestamps."""
        metadata = PluginMetadata(name="test")
        assert metadata.created_at > 0
        assert metadata.updated_at > 0

    def test_post_init_copies_lists(self):
        """Test that __post_init__ creates copies of list fields."""
        deps = ["dep1"]
        tags = ["tag1"]
        metadata = PluginMetadata(
            name="test",
            dependencies=deps,
            tags=tags,
        )
        deps.append("dep2")
        tags.append("tag2")
        assert len(metadata.dependencies) == 1
        assert len(metadata.tags) == 1


class TestPluginMetadataValidation:
    """Tests for PluginMetadata validation."""

    def test_validate_minimal_metadata(self):
        """Test validating minimal metadata."""
        metadata = PluginMetadata(name="test")
        assert metadata.validate() is True

    def test_validate_empty_name_raises(self):
        """Test that empty name raises ValueError."""
        metadata = PluginMetadata(name="")
        with pytest.raises(ValueError, match="name cannot be empty"):
            metadata.validate()

    def test_validate_whitespace_name_raises(self):
        """Test that whitespace-only name raises ValueError."""
        metadata = PluginMetadata(name="   ")
        with pytest.raises(ValueError, match="name cannot be empty"):
            metadata.validate()

    def test_validate_missing_version_raises(self):
        """Test that missing version raises ValueError."""
        metadata = PluginMetadata(name="test", version="")
        with pytest.raises(ValueError, match="version is required"):
            metadata.validate()

    def test_validate_valid_versions(self):
        """Test various valid version formats."""
        valid_versions = ["1", "1.0", "1.0.0", "1.0.0.0", "1.0.0-beta", "2.0.0-alpha"]
        for version in valid_versions:
            metadata = PluginMetadata(name="test", version=version)
            assert metadata.validate() is True, f"Version {version} should be valid"


class TestPluginMetadataSerialization:
    """Tests for PluginMetadata serialization."""

    def test_to_dict(self):
        """Test converting to dictionary."""
        metadata = PluginMetadata(
            name="test",
            version="2.0.0",
            description="Test plugin",
        )
        d = metadata.to_dict()
        assert d["name"] == "test"
        assert d["version"] == "2.0.0"
        assert d["description"] == "Test plugin"
        assert d["enabled"] is True

    def test_from_dict(self):
        """Test creating from dictionary."""
        d = {
            "name": "restored",
            "version": "1.5.0",
            "description": "Restored plugin",
            "author": "Jane Doe",
        }
        metadata = PluginMetadata.from_dict(d)
        assert metadata.name == "restored"
        assert metadata.version == "1.5.0"
        assert metadata.description == "Restored plugin"
        assert metadata.author == "Jane Doe"

    def test_from_dict_with_defaults(self):
        """Test creating from dictionary uses defaults."""
        d = {"name": "test"}
        metadata = PluginMetadata.from_dict(d)
        assert metadata.version == "1.0.0"
        assert metadata.enabled is True

    def test_to_dict_returns_copy(self):
        """Test that to_dict returns a copy."""
        metadata = PluginMetadata(name="test", tags=["tag1"])
        d = metadata.to_dict()
        d["tags"].append("tag2")
        assert len(metadata.tags) == 1

    def test_repr(self):
        """Test string representation."""
        metadata = PluginMetadata(name="test", version="1.0.0", enabled=True)
        repr_str = repr(metadata)
        assert "PluginMetadata" in repr_str
        assert "name='test'" in repr_str
        assert "version='1.0.0'" in repr_str


# =============================================================================
# PluginRegistry Singleton Tests
# =============================================================================

class TestPluginRegistrySingleton:
    """Tests for PluginRegistry singleton behavior."""

    def teardown_method(self):
        """Reset singleton after each test."""
        PluginRegistry._instance = None

    def test_get_instance_returns_singleton(self):
        """Test that get_instance returns same instance."""
        registry1 = PluginRegistry.get_instance()
        registry2 = PluginRegistry.get_instance()
        assert registry1 is registry2

    def test_singleton_across_multiple_calls(self):
        """Test singleton behavior across multiple calls."""
        instances = [PluginRegistry.get_instance() for _ in range(10)]
        assert all(instance is instances[0] for instance in instances)

    def test_registry_initialization_only_once(self):
        """Test that registry initializes only once."""
        registry1 = PluginRegistry.get_instance()
        registry1._test_init_count = 1
        registry2 = PluginRegistry.get_instance()
        assert registry2._test_init_count == 1


# =============================================================================
# PluginRegistry Registration Tests
# =============================================================================

class TestPluginRegistryRegistration:
    """Tests for PluginRegistry plugin registration."""

    def setup_method(self):
        """Reset singleton before each test."""
        PluginRegistry._instance = None
        self.registry = PluginRegistry.get_instance()
        self.registry.cleanup()

    def teardown_method(self):
        """Reset singleton after each test."""
        PluginRegistry._instance = None

    def test_register_plugin(self):
        """Test registering a plugin."""
        plugin_fn = lambda ctx: "result"
        self.registry.register_plugin("test-plugin", plugin_fn)
        assert self.registry.has_plugin("test-plugin") is True

    def test_register_plugin_with_metadata(self):
        """Test registering plugin with metadata."""
        metadata = PluginMetadata(
            name="meta-plugin",
            version="2.0.0",
            description="Plugin with metadata",
        )
        plugin_fn = lambda ctx: "result"
        self.registry.register_plugin("meta-plugin", plugin_fn, metadata=metadata)
        retrieved = self.registry.get_metadata("meta-plugin")
        assert retrieved is not None
        assert retrieved.version == "2.0.0"

    def test_register_duplicate_plugin_overwrites(self):
        """Test that registering duplicate plugin overwrites."""
        self.registry.register_plugin("dup-plugin", lambda ctx: "first")
        self.registry.register_plugin("dup-plugin", lambda ctx: "second")
        result = self.registry.execute_plugin("dup-plugin", context={})
        assert result == "second"

    def test_unregister_plugin_successfully(self):
        """Test unregistering a plugin."""
        self.registry.register_plugin("test", lambda ctx: "result")
        result = self.registry.unregister_plugin("test")
        assert result is True
        assert self.registry.has_plugin("test") is False

    def test_unregister_nonexistent_plugin(self):
        """Test unregistering non-existent plugin."""
        result = self.registry.unregister_plugin("nonexistent")
        assert result is False

    def test_get_plugin(self):
        """Test getting a plugin function."""
        def my_plugin(ctx):
            return "hello"

        self.registry.register_plugin("my-plugin", my_plugin)
        retrieved = self.registry.get_plugin("my-plugin")
        assert retrieved == my_plugin

    def test_get_nonexistent_plugin(self):
        """Test getting non-existent plugin."""
        result = self.registry.get_plugin("nonexistent")
        assert result is None

    def test_list_plugins(self):
        """Test listing all plugins."""
        self.registry.register_plugin("plugin1", lambda ctx: None)
        self.registry.register_plugin("plugin2", lambda ctx: None)
        self.registry.register_plugin("plugin3", lambda ctx: None)
        plugins = self.registry.list_plugins()
        assert len(plugins) == 3
        assert "plugin1" in plugins
        assert "plugin2" in plugins
        assert "plugin3" in plugins


# =============================================================================
# PluginRegistry Execution Tests
# =============================================================================

class TestPluginRegistryExecution:
    """Tests for PluginRegistry plugin execution."""

    def setup_method(self):
        """Reset singleton before each test."""
        PluginRegistry._instance = None
        self.registry = PluginRegistry.get_instance()
        self.registry.cleanup()

    def teardown_method(self):
        """Reset singleton after each test."""
        PluginRegistry._instance = None

    def test_execute_plugin(self):
        """Test executing a plugin."""
        def my_plugin(ctx):
            return f"Processed: {ctx.get('input', 'default')}"

        self.registry.register_plugin("my-plugin", my_plugin)
        result = self.registry.execute_plugin("my-plugin", context={"input": "test"})
        assert result == "Processed: test"

    def test_execute_plugin_with_kwargs(self):
        """Test executing plugin with keyword arguments."""
        def my_plugin(ctx, multiplier=1):
            return ctx.get("value", 0) * multiplier

        self.registry.register_plugin("my-plugin", my_plugin)
        result = self.registry.execute_plugin("my-plugin", context={"value": 5}, multiplier=3)
        assert result == 15

    def test_execute_nonexistent_plugin_raises(self):
        """Test executing non-existent plugin raises ValueError."""
        with pytest.raises(ValueError, match="Plugin not found"):
            self.registry.execute_plugin("nonexistent", context={})

    def test_execute_disabled_plugin_raises(self):
        """Test executing disabled plugin raises ValueError."""
        self.registry.register_plugin("disabled", lambda ctx: "result")
        self.registry.disable_plugin("disabled")
        with pytest.raises(ValueError, match="Plugin not enabled"):
            self.registry.execute_plugin("disabled", context={})

    def test_execute_plugin_with_exception(self):
        """Test plugin execution with exception."""
        def failing_plugin(ctx):
            raise RuntimeError("Plugin failed")

        self.registry.register_plugin("failing", failing_plugin)
        with pytest.raises(RuntimeError, match="Plugin failed"):
            self.registry.execute_plugin("failing", context={})


# =============================================================================
# PluginRegistry Lifecycle Tests
# =============================================================================

class TestPluginRegistryLifecycle:
    """Tests for PluginRegistry plugin lifecycle management."""

    def setup_method(self):
        """Reset singleton before each test."""
        PluginRegistry._instance = None
        self.registry = PluginRegistry.get_instance()
        self.registry.cleanup()

    def teardown_method(self):
        """Reset singleton after each test."""
        PluginRegistry._instance = None

    def test_enable_plugin(self):
        """Test enabling a plugin."""
        self.registry.register_plugin("test", lambda ctx: "result")
        self.registry.disable_plugin("test")
        result = self.registry.enable_plugin("test")
        assert result is True
        assert self.registry.is_enabled("test") is True

    def test_disable_plugin(self):
        """Test disabling a plugin."""
        self.registry.register_plugin("test", lambda ctx: "result")
        result = self.registry.disable_plugin("test")
        assert result is True
        assert self.registry.is_enabled("test") is False

    def test_enable_nonexistent_plugin(self):
        """Test enabling non-existent plugin."""
        result = self.registry.enable_plugin("nonexistent")
        assert result is False

    def test_disable_nonexistent_plugin(self):
        """Test disabling non-existent plugin."""
        result = self.registry.disable_plugin("nonexistent")
        assert result is False

    def test_is_enabled(self):
        """Test checking if plugin is enabled."""
        self.registry.register_plugin("test", lambda ctx: "result")
        assert self.registry.is_enabled("test") is True
        self.registry.disable_plugin("test")
        assert self.registry.is_enabled("test") is False

    def test_is_enabled_nonexistent(self):
        """Test checking if non-existent plugin is enabled."""
        assert self.registry.is_enabled("nonexistent") is False

    def test_list_enabled_plugins(self):
        """Test listing enabled plugins."""
        self.registry.register_plugin("enabled1", lambda ctx: None)
        self.registry.register_plugin("enabled2", lambda ctx: None)
        self.registry.register_plugin("disabled1", lambda ctx: None)
        self.registry.disable_plugin("disabled1")
        enabled = self.registry.list_enabled_plugins()
        assert "enabled1" in enabled
        assert "enabled2" in enabled
        assert "disabled1" not in enabled

    def test_list_disabled_plugins(self):
        """Test listing disabled plugins."""
        self.registry.register_plugin("enabled1", lambda ctx: None)
        self.registry.register_plugin("disabled1", lambda ctx: None)
        self.registry.register_plugin("disabled2", lambda ctx: None)
        self.registry.disable_plugin("disabled1")
        self.registry.disable_plugin("disabled2")
        disabled = self.registry.list_disabled_plugins()
        assert "disabled1" in disabled
        assert "disabled2" in disabled
        assert "enabled1" not in disabled

    def test_get_plugin_count(self):
        """Test getting plugin count."""
        assert self.registry.get_plugin_count() == 0
        self.registry.register_plugin("p1", lambda ctx: None)
        self.registry.register_plugin("p2", lambda ctx: None)
        assert self.registry.get_plugin_count() == 2

    def test_get_enabled_count(self):
        """Test getting enabled plugin count."""
        self.registry.register_plugin("enabled1", lambda ctx: None)
        self.registry.register_plugin("enabled2", lambda ctx: None)
        self.registry.register_plugin("disabled1", lambda ctx: None)
        self.registry.disable_plugin("disabled1")
        assert self.registry.get_enabled_count() == 2


# =============================================================================
# PluginRegistry Lazy Loading Tests
# =============================================================================

class TestPluginRegistryLazyLoading:
    """Tests for PluginRegistry lazy loading."""

    def setup_method(self):
        """Reset singleton before each test."""
        PluginRegistry._instance = None
        self.registry = PluginRegistry.get_instance()
        self.registry.cleanup()

    def teardown_method(self):
        """Reset singleton after each test."""
        PluginRegistry._instance = None

    def test_register_lazy_plugin(self):
        """Test registering a lazy plugin."""
        # Create a test module structure
        import sys
        import types

        # Create mock module
        mock_module = types.ModuleType("test_lazy_module")
        mock_module.test_function = lambda ctx: "lazy result"
        sys.modules["test_lazy_module"] = mock_module

        try:
            self.registry.register_lazy(
                "lazy-plugin",
                "test_lazy_module",
                "test_function",
            )
            assert self.registry.has_plugin("lazy-plugin") is True
            # Plugin function should be None before first access
            assert self.registry._plugins["lazy-plugin"]["function"] is None
        finally:
            del sys.modules["test_lazy_module"]

    def test_lazy_plugin_loads_on_access(self):
        """Test that lazy plugin loads on first access."""
        import sys
        import types

        mock_module = types.ModuleType("test_lazy_module")
        mock_module.test_function = lambda ctx: "lazy loaded"
        sys.modules["test_lazy_module"] = mock_module

        try:
            self.registry.register_lazy(
                "lazy-plugin",
                "test_lazy_module",
                "test_function",
            )
            # Execute should trigger lazy loading
            result = self.registry.execute_plugin("lazy-plugin", context={})
            assert result == "lazy loaded"
            # Function should now be loaded
            assert self.registry._plugins["lazy-plugin"]["function"] is not None
        finally:
            del sys.modules["test_lazy_module"]

    def test_lazy_plugin_load_failure(self):
        """Test lazy plugin load failure."""
        self.registry.register_lazy(
            "nonexistent-plugin",
            "nonexistent_module",
            "nonexistent_function",
        )
        # Should return None when loading fails
        result = self.registry.get_plugin("nonexistent-plugin")
        assert result is None


# =============================================================================
# PluginRegistry Statistics Tests
# =============================================================================

class TestPluginRegistryStatistics:
    """Tests for PluginRegistry statistics."""

    def setup_method(self):
        """Reset singleton before each test."""
        PluginRegistry._instance = None
        self.registry = PluginRegistry.get_instance()
        self.registry.cleanup()

    def teardown_method(self):
        """Reset singleton after each test."""
        PluginRegistry._instance = None

    def test_get_stats(self):
        """Test getting plugin stats."""
        self.registry.register_plugin("test", lambda ctx: "result")
        self.registry.execute_plugin("test", context={})
        stats = self.registry.get_stats("test")
        assert stats["execution_count"] == 1
        assert stats["total_time_ms"] >= 0

    def test_get_stats_nonexistent_plugin(self):
        """Test getting stats for non-existent plugin."""
        stats = self.registry.get_stats("nonexistent")
        assert stats["execution_count"] == 0

    def test_get_all_stats(self):
        """Test getting all plugin stats."""
        self.registry.register_plugin("p1", lambda ctx: None)
        self.registry.register_plugin("p2", lambda ctx: None)
        self.registry.execute_plugin("p1", context={})
        all_stats = self.registry.get_all_stats()
        assert "p1" in all_stats
        assert "p2" in all_stats
        assert all_stats["p1"]["execution_count"] == 1
        assert all_stats["p2"]["execution_count"] == 0

    def test_clear_stats_single_plugin(self):
        """Test clearing stats for single plugin."""
        self.registry.register_plugin("test", lambda ctx: "result")
        self.registry.execute_plugin("test", context={})
        self.registry.clear_stats("test")
        stats = self.registry.get_stats("test")
        assert stats["execution_count"] == 0

    def test_clear_stats_all_plugins(self):
        """Test clearing stats for all plugins."""
        self.registry.register_plugin("p1", lambda ctx: None)
        self.registry.register_plugin("p2", lambda ctx: None)
        self.registry.execute_plugin("p1", context={})
        self.registry.execute_plugin("p2", context={})
        self.registry.clear_stats()
        stats1 = self.registry.get_stats("p1")
        stats2 = self.registry.get_stats("p2")
        assert stats1["execution_count"] == 0
        assert stats2["execution_count"] == 0


# =============================================================================
# PluginRegistry Metadata Tests
# =============================================================================

class TestPluginRegistryMetadata:
    """Tests for PluginRegistry metadata operations."""

    def setup_method(self):
        """Reset singleton before each test."""
        PluginRegistry._instance = None
        self.registry = PluginRegistry.get_instance()
        self.registry.cleanup()

    def teardown_method(self):
        """Reset singleton after each test."""
        PluginRegistry._instance = None

    def test_get_metadata(self):
        """Test getting plugin metadata."""
        metadata = PluginMetadata(name="test", version="1.0.0")
        self.registry.register_plugin("test", lambda ctx: None, metadata=metadata)
        retrieved = self.registry.get_metadata("test")
        assert retrieved is not None
        assert retrieved.name == "test"
        assert retrieved.version == "1.0.0"

    def test_get_metadata_nonexistent(self):
        """Test getting metadata for non-existent plugin."""
        result = self.registry.get_metadata("nonexistent")
        assert result is None

    def test_get_all_metadata(self):
        """Test getting all metadata."""
        self.registry.register_plugin("p1", lambda ctx: None)
        self.registry.register_plugin("p2", lambda ctx: None)
        all_metadata = self.registry.get_all_metadata()
        assert "p1" in all_metadata
        assert "p2" in all_metadata

    def test_enable_plugin_updates_metadata(self):
        """Test that enabling plugin updates metadata."""
        self.registry.register_plugin("test", lambda ctx: None)
        self.registry.disable_plugin("test")
        metadata = self.registry.get_metadata("test")
        assert metadata.enabled is False
        self.registry.enable_plugin("test")
        metadata = self.registry.get_metadata("test")
        assert metadata.enabled is True


# =============================================================================
# PluginRegistry Performance Tests
# =============================================================================

class TestPluginRegistryPerformance:
    """Performance tests for PluginRegistry (PERF-006: <1ms lookup)."""

    def setup_method(self):
        """Reset singleton before each test."""
        PluginRegistry._instance = None
        self.registry = PluginRegistry.get_instance()
        self.registry.cleanup()

        # Register test plugins
        for i in range(100):
            self.registry.register_plugin(f"plugin_{i}", lambda ctx: "result")

    def teardown_method(self):
        """Reset singleton after each test."""
        PluginRegistry._instance = None

    def test_plugin_lookup_latency(self):
        """Test plugin lookup latency is under 1ms (PERF-006)."""
        # Warm up
        self.registry.get_plugin("plugin_50")

        # Measure lookup time
        iterations = 100
        times = []
        for _ in range(iterations):
            start = time.perf_counter()
            self.registry.get_plugin("plugin_50")
            elapsed_ms = (time.perf_counter() - start) * 1000
            times.append(elapsed_ms)

        avg_time_ms = sum(times) / len(times)
        max_time_ms = max(times)

        # PERF-006 requirement: <1ms lookup
        assert avg_time_ms < 1.0, f"Average lookup time {avg_time_ms:.3f}ms exceeds 1ms"
        # Allow some variance for max, but should still be fast
        assert max_time_ms < 5.0, f"Max lookup time {max_time_ms:.3f}ms too high"

    def test_plugin_execution_latency(self):
        """Test plugin execution latency."""
        # Simple fast plugin
        self.registry.register_plugin("fast", lambda ctx: "fast")

        iterations = 100
        times = []
        for _ in range(iterations):
            start = time.perf_counter()
            self.registry.execute_plugin("fast", context={})
            elapsed_ms = (time.perf_counter() - start) * 1000
            times.append(elapsed_ms)

        avg_time_ms = sum(times) / len(times)
        # Should be very fast for simple plugin
        assert avg_time_ms < 5.0, f"Average execution time {avg_time_ms:.3f}ms too high"


# =============================================================================
# PluginRegistry Thread Safety Tests
# =============================================================================

class TestPluginRegistryThreadSafety:
    """Thread safety tests for PluginRegistry (THREAD-004)."""

    def setup_method(self):
        """Reset singleton before each test."""
        PluginRegistry._instance = None
        self.registry = PluginRegistry.get_instance()
        self.registry.cleanup()

    def teardown_method(self):
        """Reset singleton after each test."""
        PluginRegistry._instance = None

    def test_concurrent_plugin_registration(self):
        """Test concurrent plugin registration."""
        errors = []
        lock = threading.Lock()

        def register_plugin(plugin_id):
            try:
                self.registry.register_plugin(f"plugin_{plugin_id}", lambda ctx: f"result_{plugin_id}")
            except Exception as e:
                with lock:
                    errors.append(e)

        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = [executor.submit(register_plugin, i) for i in range(100)]
            for future in as_completed(futures):
                future.result()

        assert len(errors) == 0
        assert self.registry.get_plugin_count() == 100

    def test_concurrent_plugin_execution(self):
        """Test concurrent plugin execution."""
        self.registry.register_plugin("test", lambda ctx: "result")
        results = []
        errors = []
        lock = threading.Lock()

        def execute_plugin(thread_id):
            try:
                for _ in range(10):
                    result = self.registry.execute_plugin("test", context={"thread": thread_id})
                    with lock:
                        results.append(result)
            except Exception as e:
                with lock:
                    errors.append(e)

        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = [executor.submit(execute_plugin, i) for i in range(50)]
            for future in as_completed(futures):
                future.result()

        assert len(errors) == 0
        assert len(results) == 500

    def test_concurrent_enable_disable(self):
        """Test concurrent enable/disable operations."""
        self.registry.register_plugin("test", lambda ctx: "result")
        errors = []
        lock = threading.Lock()

        def toggle_plugin(action):
            try:
                if action == "enable":
                    self.registry.enable_plugin("test")
                else:
                    self.registry.disable_plugin("test")
            except Exception as e:
                with lock:
                    errors.append(e)

        with ThreadPoolExecutor(max_workers=100) as executor:
            futures = [executor.submit(toggle_plugin, "enable" if i % 2 == 0 else "disable") for i in range(200)]
            for future in as_completed(futures):
                future.result()

        assert len(errors) == 0

    def test_100_concurrent_threads(self):
        """Test 100+ concurrent threads (THREAD-004 requirement)."""
        # Register plugins
        for i in range(10):
            self.registry.register_plugin(f"plugin_{i}", lambda ctx, i=i: f"result_{i}")

        results = []
        errors = []
        lock = threading.Lock()

        def execute(thread_id):
            try:
                plugin_name = f"plugin_{thread_id % 10}"
                result = self.registry.execute_plugin(plugin_name, context={"thread": thread_id})
                with lock:
                    results.append(result)
            except Exception as e:
                with lock:
                    errors.append(e)

        with ThreadPoolExecutor(max_workers=100) as executor:
            futures = [executor.submit(execute, i) for i in range(100)]
            for future in as_completed(futures):
                future.result()

        assert len(errors) == 0
        assert len(results) == 100

    def test_concurrent_metadata_access(self):
        """Test concurrent metadata access."""
        for i in range(20):
            metadata = PluginMetadata(name=f"plugin_{i}", version="1.0.0")
            self.registry.register_plugin(f"plugin_{i}", lambda ctx: None, metadata=metadata)

        results = []
        errors = []
        lock = threading.Lock()

        def access_metadata(thread_id):
            try:
                for _ in range(10):
                    plugin_name = f"plugin_{thread_id % 20}"
                    metadata = self.registry.get_metadata(plugin_name)
                    with lock:
                        results.append(metadata is not None)
            except Exception as e:
                with lock:
                    errors.append(e)

        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = [executor.submit(access_metadata, i) for i in range(50)]
            for future in as_completed(futures):
                future.result()

        assert len(errors) == 0
        assert all(results)

    def test_concurrent_stats_updates(self):
        """Test concurrent stats updates."""
        self.registry.register_plugin("test", lambda ctx: "result")
        errors = []
        lock = threading.Lock()

        def execute_and_stats(thread_id):
            try:
                self.registry.execute_plugin("test", context={})
                stats = self.registry.get_stats("test")
                with lock:
                    lock.acquire()
                    lock.release()
            except Exception as e:
                with lock:
                    errors.append(e)

        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = [executor.submit(execute_and_stats, i) for i in range(100)]
            for future in as_completed(futures):
                future.result()

        assert len(errors) == 0


# =============================================================================
# PluginRegistry Cleanup Tests
# =============================================================================

class TestPluginRegistryCleanup:
    """Tests for PluginRegistry cleanup."""

    def setup_method(self):
        """Reset singleton before each test."""
        PluginRegistry._instance = None
        self.registry = PluginRegistry.get_instance()

    def teardown_method(self):
        """Reset singleton after each test."""
        PluginRegistry._instance = None

    def test_cleanup_clears_all(self):
        """Test that cleanup clears all data."""
        self.registry.register_plugin("test", lambda ctx: "result")
        self.registry.execute_plugin("test", context={})
        self.registry.cleanup()
        assert self.registry.get_plugin_count() == 0
        assert len(self.registry.list_plugins()) == 0

    def test_after_cleanup_can_reregister(self):
        """Test that plugins can be registered after cleanup."""
        self.registry.register_plugin("test1", lambda ctx: "result1")
        self.registry.cleanup()
        self.registry.register_plugin("test2", lambda ctx: "result2")
        assert self.registry.get_plugin_count() == 1
        result = self.registry.execute_plugin("test2", context={})
        assert result == "result2"

    def test_cleanup_multiple_times_safe(self):
        """Test calling cleanup multiple times is safe."""
        self.registry.register_plugin("test", lambda ctx: None)
        self.registry.cleanup()
        self.registry.cleanup()
        self.registry.cleanup()
        assert self.registry.get_plugin_count() == 0
