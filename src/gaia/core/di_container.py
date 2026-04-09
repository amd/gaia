# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Dependency Injection Container for GAIA.

This module provides a lightweight dependency injection container
supporting singleton, transient, and scoped service lifetimes.

Service Lifetimes:
    - Singleton: Single instance shared across application
    - Transient: New instance created on each resolution
    - Scoped: Instance created per scope (request/context)

Thread Safety:
    All operations are thread-safe using RLock for reentrant locking.
    Scoped services are isolated per scope.

Example:
    >>> container = DIContainer()
    >>> container.register_singleton("logger", LoggerFactory)
    >>> container.register_transient("llm_client", LemonadeClient, model="Qwen3.5-35B")
    >>> logger = container.resolve("logger")
    >>> async with container.enter_scope("request"):
    ...     client = container.resolve("llm_client")
"""

import asyncio
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Type, TypeVar

from gaia.utils.logging import get_logger

logger = get_logger(__name__)

T = TypeVar('T')


class ServiceLifetime(Enum):
    """
    Enumeration of service lifetime types.

    Attributes:
        SINGLETON: Single instance shared across application lifetime
        TRANSIENT: New instance created on each resolution
        SCOPED: Instance created once per scope (request/context)

    Example:
        >>> ServiceLifetime.SINGLETON.value
        'singleton'
    """
    SINGLETON = 'singleton'
    TRANSIENT = 'transient'
    SCOPED = 'scoped'


@dataclass
class ServiceDescriptor:
    """
    Service registration descriptor.

    This dataclass captures all information about a registered service
    including its lifetime, implementation, and dependencies.

    Attributes:
        service_type: Type of service (class or interface)
        implementation: Implementation class or factory function
        lifetime: Service lifetime enum value
        dependencies: List of dependency service names
        init_kwargs: Keyword arguments for instantiation
        created_at: Timestamp when service was registered

    Example:
        >>> descriptor = ServiceDescriptor(
        ...     service_type=Logger,
        ...     implementation=ConsoleLogger,
        ...     lifetime=ServiceLifetime.SINGLETON,
        ... )
        >>> print(descriptor.lifetime)
        ServiceLifetime.SINGLETON
    """
    service_type: Type
    implementation: Any
    lifetime: ServiceLifetime
    dependencies: List[str] = field(default_factory=list)
    init_kwargs: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def __post_init__(self):
        """Initialize descriptor internals."""
        self.dependencies = list(self.dependencies) if self.dependencies else []
        self.init_kwargs = dict(self.init_kwargs) if self.init_kwargs else {}


class ServiceResolutionError(Exception):
    """
    Raised when a service cannot be resolved.

    This exception is raised when:
    - A service is not registered
    - A scoped service is resolved without an active scope
    - Service instantiation fails

    Example:
        >>> try:
        ...     container.resolve("unknown_service")
        ... except ServiceResolutionError as e:
        ...     print(f"Resolution failed: {e}")
    """
    pass


class CircularDependencyError(Exception):
    """
    Raised when circular dependencies are detected.

    This exception is raised when resolving services would create
    an infinite loop (e.g., A depends on B, B depends on A).

    Example:
        >>> try:
        ...     container.resolve("service_a")
        ... except CircularDependencyError as e:
        ...     print(f"Circular dependency: {e}")
    """
    pass


class DIContainer:
    """
    Dependency Injection Container.

    Supports singleton, transient, and scoped service lifetimes.
    Thread-safe with automatic dependency resolution and circular
    dependency detection.

    Features:
        - Three service lifetimes: singleton, transient, scoped
        - Automatic dependency resolution
        - Circular dependency detection
        - Thread-safe operations with RLock
        - Async scope management
        - Factory function registration
        - Service introspection

    Thread Safety:
        All public methods are thread-safe using RLock for reentrant locking.
        Scoped services are isolated per scope using async lock.

    Example:
        >>> container = DIContainer()
        >>> container.register_singleton("config", ConfigManager, config_path="config.yaml")
        >>> container.register_transient("llm_client", LemonadeClient, model_id="Qwen3.5-35B")
        >>> config = container.resolve("config")
        >>> async with container.enter_scope("request"):
        ...     session = container.resolve("db_session")
    """

    _instance: Optional["DIContainer"] = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "DIContainer":
        """Get singleton instance of container using double-checked locking."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """Initialize container (only once per singleton)."""
        if self._initialized:
            return

        self._services: Dict[str, ServiceDescriptor] = {}
        self._singletons: Dict[str, Any] = {}
        self._scopes: Dict[str, Dict[str, Any]] = {}
        self._current_scope: Optional[str] = None
        self._resolution_stack: List[str] = []  # For circular dependency detection
        self._lock = threading.RLock()
        self._async_lock = asyncio.Lock()
        self._initialized = True

        logger.info("DIContainer initialized")

    @classmethod
    def get_instance(cls) -> "DIContainer":
        """
        Get singleton container instance.

        Returns:
            DIContainer singleton instance

        Example:
            >>> container = DIContainer.get_instance()
            >>> container2 = DIContainer.get_instance()
            >>> container is container2
            True
        """
        return cls()

    def reset(self) -> None:
        """
        Reset container state (for testing).

        This method clears all registered services, singletons, and scopes.
        Should only be used in test environments.

        Example:
            >>> container.register_singleton("test", TestClass)
            >>> container.reset()
            >>> container.is_registered("test")
            False
        """
        with self._lock:
            self._services.clear()
            self._singletons.clear()
            self._scopes.clear()
            self._current_scope = None
            self._resolution_stack.clear()
            logger.info("DIContainer reset")

    # ==================== Registration ====================

    def register_singleton(
        self,
        name: str,
        implementation: Type[T],
        **kwargs: Any,
    ) -> "DIContainer":
        """
        Register a singleton service.

        Singleton services are instantiated once on first resolution and
        reused for all subsequent resolutions. The instance is cached
        until the container is reset.

        Args:
            name: Service name for resolution
            implementation: Implementation class
            **kwargs: Keyword arguments passed to implementation constructor

        Returns:
            Self for method chaining

        Raises:
            ValueError: If implementation is not a class

        Example:
            >>> container.register_singleton("config", ConfigManager, config_path="config.yaml")
            >>> config1 = container.resolve("config")
            >>> config2 = container.resolve("config")
            >>> config1 is config2  # Same instance
            True
        """
        with self._lock:
            descriptor = ServiceDescriptor(
                service_type=implementation,
                implementation=implementation,
                lifetime=ServiceLifetime.SINGLETON,
                init_kwargs=kwargs,
            )
            self._services[name] = descriptor
            logger.debug(f"Registered singleton service: {name}")
        return self

    def register_transient(
        self,
        name: str,
        implementation: Type[T],
        **kwargs: Any,
    ) -> "DIContainer":
        """
        Register a transient service.

        Transient services are created fresh on each resolution. No
        instance caching occurs - each resolve() call creates a new instance.

        Args:
            name: Service name for resolution
            implementation: Implementation class
            **kwargs: Keyword arguments passed to implementation constructor

        Returns:
            Self for method chaining

        Example:
            >>> container.register_transient("request", RequestHandler)
            >>> req1 = container.resolve("request")
            >>> req2 = container.resolve("request")
            >>> req1 is req2  # Different instances
            False
        """
        with self._lock:
            descriptor = ServiceDescriptor(
                service_type=implementation,
                implementation=implementation,
                lifetime=ServiceLifetime.TRANSIENT,
                init_kwargs=kwargs,
            )
            self._services[name] = descriptor
            logger.debug(f"Registered transient service: {name}")
        return self

    def register_scoped(
        self,
        name: str,
        implementation: Type[T],
        **kwargs: Any,
    ) -> "DIContainer":
        """
        Register a scoped service.

        Scoped services are created once per scope (request/context).
        Within the same scope, multiple resolutions return the same instance.
        Different scopes get different instances.

        Args:
            name: Service name for resolution
            implementation: Implementation class
            **kwargs: Keyword arguments passed to implementation constructor

        Returns:
            Self for method chaining

        Raises:
            ServiceResolutionError: If resolved without active scope

        Example:
            >>> container.register_scoped("session", DatabaseSession)
            >>> async with container.enter_scope("request-1"):
            ...     session1 = container.resolve("session")
            ...     session2 = container.resolve("session")
            >>> session1 is session2  # Same instance in scope
            True
        """
        with self._lock:
            descriptor = ServiceDescriptor(
                service_type=implementation,
                implementation=implementation,
                lifetime=ServiceLifetime.SCOPED,
                init_kwargs=kwargs,
            )
            self._services[name] = descriptor
            logger.debug(f"Registered scoped service: {name}")
        return self

    def register_factory(
        self,
        name: str,
        factory: Callable[..., T],
        lifetime: ServiceLifetime = ServiceLifetime.TRANSIENT,
        dependencies: Optional[List[str]] = None,
    ) -> "DIContainer":
        """
        Register a service factory.

        Factory functions are called to create service instances instead
        of directly instantiating a class. This allows for custom
        instantiation logic.

        Args:
            name: Service name
            factory: Factory function/callable that returns service instance
            lifetime: Service lifetime (default: TRANSIENT)
            dependencies: Optional list of dependency service names to inject

        Returns:
            Self for method chaining

        Example:
            >>> def create_client():
            ...     return LemonadeClient(model_id="Qwen3.5-35B")
            >>> container.register_factory("llm_client", create_client)
            >>> client = container.resolve("llm_client")
        """
        with self._lock:
            descriptor = ServiceDescriptor(
                service_type=Callable,
                implementation=factory,
                lifetime=lifetime,
                dependencies=dependencies or [],
            )
            self._services[name] = descriptor
            logger.debug(f"Registered factory service: {name}")
        return self

    # ==================== Resolution ====================

    def resolve(self, name: str) -> Any:
        """
        Resolve a service by name.

        This method retrieves or creates a service instance based on
        its registered lifetime. Singleton services are cached, scoped
        services are resolved from current scope, and transient services
        are created fresh.

        Args:
            name: Service name to resolve

        Returns:
            Service instance

        Raises:
            ServiceResolutionError: If service not found or cannot be resolved
            CircularDependencyError: If circular dependency detected

        Example:
            >>> container.register_singleton("logger", Logger)
            >>> logger = container.resolve("logger")
        """
        with self._lock:
            # Check for circular dependencies
            if name in self._resolution_stack:
                cycle = " -> ".join(self._resolution_stack + [name])
                raise CircularDependencyError(f"Circular dependency detected: {cycle}")

            if name not in self._services:
                raise ServiceResolutionError(f"Service '{name}' not registered")

            descriptor = self._services[name]
            lifetime = descriptor.lifetime

            # Handle singleton
            if lifetime == ServiceLifetime.SINGLETON:
                if name not in self._singletons:
                    self._resolution_stack.append(name)
                    try:
                        instance = self._create_instance(descriptor)
                        self._singletons[name] = instance
                    finally:
                        self._resolution_stack.pop()
                return self._singletons[name]

            # Handle scoped
            if lifetime == ServiceLifetime.SCOPED:
                if self._current_scope is None:
                    raise ServiceResolutionError(
                        f"Scoped service '{name}' requires active scope. "
                        "Use 'async with container.enter_scope()'"
                    )
                scope = self._scopes[self._current_scope]
                if name not in scope:
                    self._resolution_stack.append(name)
                    try:
                        instance = self._create_instance(descriptor)
                        scope[name] = instance
                    finally:
                        self._resolution_stack.pop()
                return scope[name]

            # Handle transient
            if lifetime == ServiceLifetime.TRANSIENT:
                self._resolution_stack.append(name)
                try:
                    return self._create_instance(descriptor)
                finally:
                    self._resolution_stack.pop()

            raise ServiceResolutionError(f"Unknown lifetime: {lifetime}")

    def _create_instance(self, descriptor: ServiceDescriptor) -> Any:
        """
        Create service instance from descriptor.

        This internal method handles both class instantiation and
        factory function calls. It also resolves and injects dependencies.

        Args:
            descriptor: Service descriptor with implementation details

        Returns:
            Created service instance

        Raises:
            ServiceResolutionError: If instance creation fails
        """
        impl = descriptor.implementation

        # Handle factory functions
        if callable(impl) and not isinstance(impl, type):
            # Resolve dependencies for factory
            kwargs = {}
            for dep_name in descriptor.dependencies:
                kwargs[dep_name] = self.resolve(dep_name)
            return impl(**kwargs) if kwargs else impl()

        # Resolve dependencies for class
        kwargs = dict(descriptor.init_kwargs)
        for dep_name in descriptor.dependencies:
            kwargs[dep_name] = self.resolve(dep_name)

        # Create instance
        try:
            if isinstance(impl, type):
                return impl(**kwargs)
            else:
                return impl(**kwargs)
        except Exception as e:
            raise ServiceResolutionError(
                f"Failed to create service '{descriptor.service_type.__name__}': {e}"
            ) from e

    def resolve_optional(self, name: str, default: Any = None) -> Any:
        """
        Resolve a service or return default value.

        This method attempts to resolve a service and returns the default
        value if the service is not registered or resolution fails.

        Args:
            name: Service name to resolve
            default: Default value if service not found (default: None)

        Returns:
            Service instance or default value

        Example:
            >>> container.resolve_optional("optional_service", default=None)
        """
        try:
            return self.resolve(name)
        except ServiceResolutionError:
            return default

    def is_registered(self, name: str) -> bool:
        """
        Check if service is registered.

        Args:
            name: Service name to check

        Returns:
            True if service is registered, False otherwise

        Example:
            >>> container.register_singleton("test", TestClass)
            >>> container.is_registered("test")
            True
            >>> container.is_registered("unknown")
            False
        """
        with self._lock:
            return name in self._services

    # ==================== LLM Client Helpers ====================

    def get_llm_client(self, model_id: Optional[str] = None) -> Any:
        """
        Get LLM client from container.

        This convenience method retrieves or auto-creates an LLM client.
        It first checks for model-specific registration, then falls back
        to default registration, and finally auto-creates if needed.

        Args:
            model_id: Optional model identifier for model-specific client

        Returns:
            LLM client instance

        Example:
            >>> client = container.get_llm_client("Qwen3.5-35B")
            >>> response = client.chat("Hello")
        """
        # Try specific model registration first
        if model_id:
            service_name = f"llm_client:{model_id}"
            if self.is_registered(service_name):
                return self.resolve(service_name)

        # Fall back to default
        if self.is_registered("llm_client"):
            return self.resolve("llm_client")

        # Auto-create default client
        try:
            from gaia.llm.lemonade_client import LemonadeClient
            client = LemonadeClient(model=model_id or "Qwen3.5-35B-A3B-GGUF")
            self.register_singleton("llm_client", LemonadeClient, model=model_id)
            return client
        except (ImportError, TypeError):
            raise ServiceResolutionError(
                "LLM client not registered and LemonadeClient not available. "
                "Please register an LLM client manually."
            )

    # ==================== Scope Management ====================

    @asynccontextmanager
    async def enter_scope(self, scope_id: Optional[str] = None):
        """
        Enter a new service scope.

        This async context manager creates an isolated scope for scoped
        services. When the context exits, all scoped services are cleaned up.

        Args:
            scope_id: Optional scope identifier (auto-generated UUID if not provided)

        Yields:
            Scope identifier string

        Example:
            >>> async with container.enter_scope("request-123"):
            ...     session = container.resolve("session")
            ...     # Use session
            >>> # Session automatically cleaned up
        """
        scope_id = scope_id or str(uuid.uuid4())

        async with self._async_lock:
            self._scopes[scope_id] = {}
            old_scope = self._current_scope
            self._current_scope = scope_id
            logger.debug(f"Entered scope: {scope_id}")

        try:
            yield scope_id
        finally:
            async with self._async_lock:
                # Clean up scoped services
                if scope_id in self._scopes:
                    # Call cleanup on scoped services if they have it
                    for name, service in self._scopes[scope_id].items():
                        if hasattr(service, 'cleanup'):
                            try:
                                service.cleanup()
                            except Exception as e:
                                logger.error(f"Error cleaning up {name}: {e}")
                    del self._scopes[scope_id]

                self._current_scope = old_scope
                logger.debug(f"Exited scope: {scope_id}")

    def get_current_scope(self) -> Optional[str]:
        """
        Get current scope identifier.

        Returns:
            Current scope ID or None if no active scope

        Example:
            >>> container.get_current_scope()
            None
            >>> async with container.enter_scope("test"):
            ...     print(container.get_current_scope())
            'test'
        """
        return self._current_scope

    # ==================== Introspection ====================

    def get_registered_services(self) -> Dict[str, ServiceLifetime]:
        """
        Get all registered services.

        Returns:
            Dictionary mapping service names to their lifetimes

        Example:
            >>> container.register_singleton("config", Config)
            >>> container.register_transient("logger", Logger)
            >>> services = container.get_registered_services()
            >>> services["config"]
            ServiceLifetime.SINGLETON
        """
        with self._lock:
            return {
                name: descriptor.lifetime
                for name, descriptor in self._services.items()
            }

    def get_service_info(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed information about a service.

        Args:
            name: Service name

        Returns:
            Service information dictionary or None if not found

        Example:
            >>> container.register_singleton("config", ConfigManager, config_path="config.yaml")
            >>> info = container.get_service_info("config")
            >>> info["lifetime"]
            ServiceLifetime.SINGLETON
            >>> info["init_kwargs"]
            {'config_path': 'config.yaml'}
        """
        with self._lock:
            if name not in self._services:
                return None

            descriptor = self._services[name]
            return {
                "name": name,
                "type": descriptor.service_type.__name__ if hasattr(descriptor.service_type, '__name__') else str(descriptor.service_type),
                "lifetime": descriptor.lifetime.value,
                "dependencies": list(descriptor.dependencies),
                "init_kwargs": dict(descriptor.init_kwargs),
            }

    def get_singleton_count(self) -> int:
        """
        Get count of instantiated singletons.

        Returns:
            Number of singleton instances currently cached

        Example:
            >>> container.register_singleton("a", A)
            >>> container.register_singleton("b", B)
            >>> container.resolve("a")
            >>> container.get_singleton_count()
            1
        """
        with self._lock:
            return len(self._singletons)

    def get_scope_service_count(self, scope_id: Optional[str] = None) -> int:
        """
        Get count of services in a scope.

        Args:
            scope_id: Scope ID to check (current scope if None)

        Returns:
            Number of services in the scope

        Example:
            >>> async with container.enter_scope("test"):
            ...     container.resolve("service1")
            ...     container.resolve("service2")
            ...     print(container.get_scope_service_count())
            2
        """
        with self._lock:
            if scope_id is None:
                scope_id = self._current_scope
            if scope_id is None or scope_id not in self._scopes:
                return 0
            return len(self._scopes[scope_id])

    def __repr__(self) -> str:
        """Return string representation of container."""
        with self._lock:
            singleton_count = len(self._singletons)
            service_count = len(self._services)
            scope_count = len(self._scopes)
            return (
                f"DIContainer(services={service_count}, "
                f"singletons={singleton_count}, scopes={scope_count})"
            )


# Module version
__version__ = "1.0.0"


def get_version() -> str:
    """Return the module version."""
    return __version__
