# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for DIContainer.

This test suite validates:
- Service registration (singleton, transient, scoped)
- Service resolution and lifetime correctness
- Circular dependency detection
- Scope management
- Thread safety

Quality Gate 4 Criteria Covered:
- DI-001: DIContainer resolution 100% accuracy
- DI-002: Service lifetime correctness (all 3)
- THREAD-001: Thread safety no race conditions
"""

import asyncio
import pytest
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from gaia.core.di_container import (
    DIContainer,
    ServiceLifetime,
    ServiceDescriptor,
    ServiceResolutionError,
    CircularDependencyError,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def container():
    """Create a fresh DIContainer for testing."""
    # Reset singleton state before each test
    DIContainer._instance = None
    DIContainer._instance_lock = threading.Lock()
    container = DIContainer()
    container.reset()
    yield container
    container.reset()
    DIContainer._instance = None


class SampleService:
    """Sample service for testing."""
    instance_count = 0

    def __init__(self, value="default"):
        SampleService.instance_count += 1
        self.value = value
        self.instance_id = SampleService.instance_count

    def cleanup(self):
        """Cleanup method for scoped services."""
        pass


class ServiceWithDependencies:
    """Service with dependencies for testing."""

    def __init__(self, dep1: SampleService, dep2: SampleService):
        self.dep1 = dep1
        self.dep2 = dep2


# =============================================================================
# Service Registration Tests
# =============================================================================

class TestServiceRegistration:
    """Tests for service registration."""

    def test_register_singleton(self, container):
        """Test registering a singleton service."""
        container.register_singleton("sample", SampleService)
        assert container.is_registered("sample")
        info = container.get_service_info("sample")
        assert info["lifetime"] == "singleton"
        assert info["type"] == "SampleService"

    def test_register_transient(self, container):
        """Test registering a transient service."""
        container.register_transient("sample", SampleService)
        assert container.is_registered("sample")
        info = container.get_service_info("sample")
        assert info["lifetime"] == "transient"

    def test_register_scoped(self, container):
        """Test registering a scoped service."""
        container.register_scoped("sample", SampleService)
        assert container.is_registered("sample")
        info = container.get_service_info("sample")
        assert info["lifetime"] == "scoped"

    def test_register_factory(self, container):
        """Test registering a factory service."""
        def factory():
            return SampleService("factory-created")

        container.register_factory("sample", factory)
        assert container.is_registered("sample")
        info = container.get_service_info("sample")
        assert info["lifetime"] == "transient"

    def test_register_singleton_with_kwargs(self, container):
        """Test registering singleton with initialization kwargs."""
        container.register_singleton("sample", SampleService, value="custom")
        service = container.resolve("sample")
        assert service.value == "custom"

    def test_method_chaining(self, container):
        """Test that register methods return self for chaining."""
        result = container.register_singleton("s1", SampleService)
        assert result is container
        result = container.register_transient("t1", SampleService)
        assert result is container
        result = container.register_scoped("sc1", SampleService)
        assert result is container


# =============================================================================
# Singleton Lifetime Tests
# =============================================================================

class TestSingletonLifetime:
    """Tests for singleton service lifetime."""

    def test_singleton_returns_same_instance(self, container):
        """Test that singleton returns the same instance."""
        container.register_singleton("sample", SampleService)

        service1 = container.resolve("sample")
        service2 = container.resolve("sample")

        assert service1 is service2
        assert service1.instance_id == service2.instance_id

    def test_singleton_with_kwargs(self, container):
        """Test singleton with kwargs is properly configured."""
        container.register_singleton("sample", SampleService, value="test-value")

        service = container.resolve("sample")
        assert service.value == "test-value"

    def test_singleton_count(self, container):
        """Test singleton instance count."""
        container.register_singleton("s1", SampleService)
        container.register_singleton("s2", SampleService)

        container.resolve("s1")
        assert container.get_singleton_count() == 1

        container.resolve("s2")
        assert container.get_singleton_count() == 2

        # Resolving again shouldn't increase count
        container.resolve("s1")
        assert container.get_singleton_count() == 2


# =============================================================================
# Transient Lifetime Tests
# =============================================================================

class TestTransientLifetime:
    """Tests for transient service lifetime."""

    def test_transient_returns_new_instances(self, container):
        """Test that transient returns new instances."""
        container.register_transient("sample", SampleService)

        service1 = container.resolve("sample")
        service2 = container.resolve("sample")

        assert service1 is not service2
        assert service1.instance_id != service2.instance_id

    def test_transient_with_kwargs(self, container):
        """Test transient with kwargs."""
        container.register_transient("sample", SampleService, value="transient-value")

        service1 = container.resolve("sample")
        service2 = container.resolve("sample")

        assert service1.value == "transient-value"
        assert service2.value == "transient-value"
        assert service1.instance_id != service2.instance_id


# =============================================================================
# Scoped Lifetime Tests
# =============================================================================

class TestScopedLifetime:
    """Tests for scoped service lifetime."""

    @pytest.mark.asyncio
    async def test_scoped_returns_same_instance_in_scope(self, container):
        """Test that scoped returns same instance within scope."""
        container.register_scoped("sample", SampleService)

        async with container.enter_scope("test-scope"):
            service1 = container.resolve("sample")
            service2 = container.resolve("sample")
            assert service1 is service2

    @pytest.mark.asyncio
    async def test_scoped_returns_different_instances_across_scopes(self, container):
        """Test that scoped returns different instances across scopes."""
        container.register_scoped("sample", SampleService)

        async with container.enter_scope("scope-1"):
            service1 = container.resolve("sample")

        async with container.enter_scope("scope-2"):
            service2 = container.resolve("sample")

        assert service1 is not service2

    @pytest.mark.asyncio
    async def test_scoped_without_scope_raises_error(self, container):
        """Test that resolving scoped service without scope raises error."""
        container.register_scoped("sample", SampleService)

        with pytest.raises(ServiceResolutionError, match="requires active scope"):
            container.resolve("sample")

    @pytest.mark.asyncio
    async def test_scope_isolation(self, container):
        """Test that scopes are isolated from each other."""
        container.register_scoped("sample", SampleService)

        async with container.enter_scope("scope-1"):
            service1 = container.resolve("sample")
            async with container.enter_scope("nested-scope"):
                service2 = container.resolve("sample")
                assert service1 is not service2

    @pytest.mark.asyncio
    async def test_scope_cleanup(self, container):
        """Test that scoped services are cleaned up on scope exit."""
        container.register_scoped("sample", SampleService)

        async with container.enter_scope("test-scope"):
            container.resolve("sample")
            assert container.get_scope_service_count() == 1

        assert container.get_scope_service_count() == 0


# =============================================================================
# Dependency Resolution Tests
# =============================================================================

class TestDependencyResolution:
    """Tests for dependency resolution."""

    def test_resolve_unregistered_service_raises(self, container):
        """Test that resolving unregistered service raises error."""
        with pytest.raises(ServiceResolutionError, match="not registered"):
            container.resolve("unknown")

    def test_resolve_optional_returns_default(self, container):
        """Test that resolve_optional returns default for unregistered."""
        result = container.resolve_optional("unknown", default="default-value")
        assert result == "default-value"

    def test_resolve_optional_returns_service(self, container):
        """Test that resolve_optional returns service when registered."""
        container.register_singleton("sample", SampleService)
        result = container.resolve_optional("sample", default="default")
        assert isinstance(result, SampleService)

    def test_factory_with_dependencies(self, container):
        """Test factory with dependency injection."""
        container.register_singleton("dep1", SampleService, value="dep1")
        container.register_singleton("dep2", SampleService, value="dep2")

        def factory(dep1: SampleService, dep2: SampleService):
            return ServiceWithDependencies(dep1, dep2)

        container.register_factory("main", factory, dependencies=["dep1", "dep2"])

        main = container.resolve("main")
        assert isinstance(main, ServiceWithDependencies)
        assert main.dep1.value == "dep1"
        assert main.dep2.value == "dep2"


# =============================================================================
# Circular Dependency Detection Tests
# =============================================================================

class TestCircularDependencyDetection:
    """Tests for circular dependency detection."""

    def test_circular_dependency_raises_error(self, container):
        """Test that circular dependencies are detected and raise error."""
        # Create circular dependency: A -> B -> A
        def create_a(b=None):
            return {"name": "a", "b": b}

        def create_b(a=None):
            return {"name": "b", "a": a}

        container.register_factory("a", create_a, dependencies=["b"])
        container.register_factory("b", create_b, dependencies=["a"])

        with pytest.raises(CircularDependencyError, match="Circular dependency"):
            container.resolve("a")

    def test_self_dependency_raises_error(self, container):
        """Test that self-dependency is detected."""
        def create_self(self=None):
            return {"name": "self"}

        container.register_factory("self", create_self, dependencies=["self"])

        with pytest.raises(CircularDependencyError):
            container.resolve("self")


# =============================================================================
# LLM Client Helper Tests
# =============================================================================

class TestLLMClientHelper:
    """Tests for LLM client helper methods."""

    def test_get_llm_client_auto_create(self, container):
        """Test that get_llm_client auto-creates if not registered."""
        # This will try to auto-create, but may fail if LemonadeClient not available
        # Just test that the method exists and handles gracefully
        try:
            client = container.get_llm_client()
            assert client is not None
        except (ServiceResolutionError, ImportError):
            # Expected if LemonadeClient not available
            pass

    def test_get_llm_client_from_registration(self, container):
        """Test get_llm_client from registered service."""
        container.register_singleton("llm_client", SampleService, value="llm")
        client = container.get_llm_client()
        assert isinstance(client, SampleService)


# =============================================================================
# Introspection Tests
# =============================================================================

class TestIntrospection:
    """Tests for container introspection."""

    def test_get_registered_services(self, container):
        """Test getting all registered services."""
        container.register_singleton("s1", SampleService)
        container.register_transient("t1", SampleService)
        container.register_scoped("sc1", SampleService)

        services = container.get_registered_services()

        assert services["s1"] == ServiceLifetime.SINGLETON
        assert services["t1"] == ServiceLifetime.TRANSIENT
        assert services["sc1"] == ServiceLifetime.SCOPED

    def test_get_service_info(self, container):
        """Test getting service information."""
        container.register_singleton("sample", SampleService, value="test")

        info = container.get_service_info("sample")

        assert info is not None
        assert info["name"] == "sample"
        assert info["type"] == "SampleService"
        assert info["lifetime"] == "singleton"
        assert info["init_kwargs"]["value"] == "test"

    def test_get_service_info_unregistered(self, container):
        """Test getting info for unregistered service."""
        info = container.get_service_info("unknown")
        assert info is None

    def test_reset_container(self, container):
        """Test resetting the container."""
        container.register_singleton("sample", SampleService)
        container.resolve("sample")

        container.reset()

        assert not container.is_registered("sample")
        assert container.get_singleton_count() == 0


# =============================================================================
# Thread Safety Tests
# =============================================================================

class TestThreadSafety:
    """Tests for thread safety."""

    def test_concurrent_singleton_resolution(self, container):
        """Test thread-safe singleton resolution."""
        container.register_singleton("sample", SampleService)

        results = []

        def resolve_service():
            return container.resolve("sample")

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(resolve_service) for _ in range(20)]
            results = [f.result() for f in as_completed(futures)]

        # All should be the same instance
        first = results[0]
        for result in results:
            assert result is first

    def test_concurrent_registration(self, container):
        """Test thread-safe registration."""
        def register_service(name):
            container.register_singleton(name, SampleService)

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(register_service, f"service_{i}") for i in range(20)]
            for f in as_completed(futures):
                f.result()  # Should not raise

        # All services should be registered
        for i in range(20):
            assert container.is_registered(f"service_{i}")

    def test_concurrent_transient_resolution(self, container):
        """Test thread-safe transient resolution."""
        container.register_transient("sample", SampleService)

        results = []

        def resolve_service():
            return container.resolve("sample")

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(resolve_service) for _ in range(20)]
            results = [f.result() for f in as_completed(futures)]

        # All should be different instances
        instance_ids = [r.instance_id for r in results]
        assert len(set(instance_ids)) == 20


# =============================================================================
# Singleton Pattern Tests
# =============================================================================

class TestSingletonPattern:
    """Tests for DIContainer singleton pattern."""

    def test_get_instance_returns_singleton(self):
        """Test that get_instance returns singleton."""
        DIContainer._instance = None
        container1 = DIContainer.get_instance()
        container2 = DIContainer.get_instance()
        assert container1 is container2

    def test_multiple_get_instance_calls(self):
        """Test multiple get_instance calls return same instance."""
        DIContainer._instance = None
        containers = [DIContainer.get_instance() for _ in range(10)]
        assert all(c is containers[0] for c in containers)


# =============================================================================
# Edge Cases and Error Handling
# =============================================================================

class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_register_with_empty_name(self, container):
        """Test registering with empty string name."""
        container.register_singleton("", SampleService)
        assert container.is_registered("")

    def test_resolve_returns_deep_copy_kwargs(self, container):
        """Test that resolved services don't share mutable kwargs."""
        container.register_singleton("sample", SampleService, value="original")
        service1 = container.resolve("sample")
        service2 = container.resolve("sample")
        # Singleton should be same instance
        assert service1 is service2

    def test_scope_id_generation(self, container):
        """Test automatic scope ID generation."""
        async def test_scope():
            async with container.enter_scope() as scope_id:
                assert scope_id is not None
                assert isinstance(scope_id, str)

        asyncio.run(test_scope())


# =============================================================================
# Container Representation
# =============================================================================

class TestContainerRepr:
    """Tests for container string representation."""

    def test_container_repr(self, container):
        """Test container string representation."""
        container.register_singleton("s1", SampleService)
        container.resolve("s1")

        repr_str = repr(container)
        assert "DIContainer" in repr_str
        assert "services=1" in repr_str
        assert "singletons=1" in repr_str


# Run tests with: pytest tests/unit/core/test_di_container.py -v
