"""
API versioning for GAIA API.

This module provides API versioning support with multiple strategies:
- URL versioning: /v1/resource, /v2/resource
- Header versioning: X-API-Version: 1
- Accept header versioning: Accept: application/vnd.gaia.v1+json

Example:
    >>> from fastapi import FastAPI
    >>> from gaia.api.versioning import APIVersioning, VersionStrategy
    >>>
    >>> app = FastAPI()
    >>> versioning = APIVersioning(
    ...     app,
    ...     default_version=1,
    ...     strategy=VersionStrategy.URL,
    ... )
    >>>
    >>> # Register versioned routers
    >>> v1_router = versioning.create_router(version=1)
    >>> v2_router = versioning.create_router(version=2)
    >>>
    >>> @v1_router.get("/chat")
    ... def get_chat_v1():
    ...     return {"version": "v1"}
    >>>
    >>> @v2_router.get("/chat")
    ... def get_chat_v2():
    ...     return {"version": "v2"}
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple, Any
from fastapi import FastAPI, Request, APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
import re


class VersionStrategy(Enum):
    """
    API versioning strategy.

    Members:
        URL: URL path versioning (/v1/resource, /v2/resource)
        HEADER: Custom header versioning (X-API-Version: 1)
        ACCEPT: Accept header versioning (Accept: application/vnd.gaia.v1+json)

    Example:
        >>> VersionStrategy.URL.value
        'url'
    """

    URL = "url"
    HEADER = "header"
    ACCEPT = "accept"


@dataclass
class VersionConfig:
    """
    Version configuration.

    Attributes:
        version: Version number
        prefix: URL prefix (e.g., "/v1")
        status: Version status ('current', 'stable', 'deprecated', 'sunset')
        sunset_date: ISO 8601 sunset date for deprecated versions
        description: Optional version description

    Example:
        >>> config = VersionConfig(
        ...     version=1,
        ...     prefix="/v1",
        ...     status="deprecated",
        ...     sunset_date="2026-12-31T23:59:59Z",
        ...     description="Initial API version"
        ... )
    """

    version: int
    prefix: str
    status: str = "stable"
    sunset_date: Optional[str] = None
    description: str = ""

    def __post_init__(self) -> None:
        """Validate version config."""
        valid_statuses = {"current", "stable", "deprecated", "sunset", "beta"}
        if self.status not in valid_statuses:
            raise ValueError(f"Invalid status '{self.status}'. Must be one of: {valid_statuses}")

    def is_deprecated(self) -> bool:
        """Check if version is deprecated."""
        return self.status in ("deprecated", "sunset")

    def is_sunset(self) -> bool:
        """Check if version has reached sunset."""
        if self.status == "sunset":
            return True
        if self.sunset_date:
            try:
                sunset = datetime.fromisoformat(self.sunset_date.replace("Z", "+00:00"))
                return datetime.now(sunset.tzinfo) >= sunset
            except ValueError:
                return False
        return False


class APIVersioning:
    """
    API versioning manager with multiple strategy support.

    Supports:
    - URL versioning: /v1/chat, /v2/chat
    - Header versioning: X-API-Version: 1
    - Accept header versioning: Accept: application/vnd.gaia.v1+json

    Example:
        >>> from fastapi import FastAPI, APIRouter
        >>> from gaia.api.versioning import APIVersioning, VersionStrategy
        >>>
        >>> app = FastAPI()
        >>> versioning = APIVersioning(
        ...     app,
        ...     default_version=1,
        ...     strategy=VersionStrategy.URL,
        ... )
        >>>
        >>> # Register versioned routers
        >>> v1_router = versioning.create_router(version=1)
        >>> v2_router = versioning.create_router(version=2)
        >>>
        >>> @v1_router.get("/chat")
        >>> def get_chat_v1(): ...
        >>>
        >>> @v2_router.get("/chat")
        >>> def get_chat_v2(): ...
    """

    def __init__(
        self,
        app: FastAPI,
        default_version: int = 1,
        strategy: VersionStrategy = VersionStrategy.URL,
        versions: Optional[List[VersionConfig]] = None,
    ) -> None:
        """
        Initialize API versioning.

        Args:
            app: FastAPI application
            default_version: Default API version
            strategy: Version resolution strategy
            versions: List of version configurations

        Example:
            >>> app = FastAPI()
            >>> versioning = APIVersioning(
            ...     app,
            ...     default_version=1,
            ...     strategy=VersionStrategy.URL,
            ...     versions=[
            ...         VersionConfig(1, "/v1", "deprecated"),
            ...         VersionConfig(2, "/v2", "current"),
            ...     ]
            ... )
        """
        self.app = app
        self.default_version = default_version
        self.strategy = strategy
        self.versions: Dict[int, VersionConfig] = {}
        self._routers: Dict[int, APIRouter] = {}
        self._version_middleware_installed = False

        # Register provided versions
        if versions:
            for config in versions:
                self.register_version(
                    config.version,
                    config.prefix,
                    config.status,
                    config.sunset_date,
                    config.description,
                )

    def create_router(self, version: int, **kwargs: Any) -> APIRouter:
        """
        Create versioned API router.

        Args:
            version: API version number
            **kwargs: Additional APIRouter arguments

        Returns:
            Configured APIRouter

        Example:
            >>> versioning = APIVersioning(app)
            >>> v1_router = versioning.create_router(1, tags=["v1"])
            >>>
            >>> @v1_router.get("/resource")
            ... def get_resource():
            ...     return {"version": "v1"}
        """
        if version not in self._routers:
            prefix = f"/v{version}"
            if version in self.versions:
                prefix = self.versions[version].prefix

            router = APIRouter(prefix=prefix, **kwargs)
            self._routers[version] = router

            # Register version if not already registered
            if version not in self.versions:
                self.register_version(version, prefix)

        return self._routers[version]

    def get_version_from_request(self, request: Request) -> int:
        """
        Extract API version from request.

        Args:
            request: FastAPI request

        Returns:
            Resolved version number

        Raises:
            HTTPException: If version cannot be determined

        Example:
            >>> versioning = APIVersioning(app, strategy=VersionStrategy.HEADER)
            >>> # Simulated request with X-API-Version header
            >>> version = versioning.get_version_from_request(request)
        """
        if self.strategy == VersionStrategy.URL:
            return self._get_version_from_url(request)
        elif self.strategy == VersionStrategy.HEADER:
            return self._get_version_from_header(request)
        elif self.strategy == VersionStrategy.ACCEPT:
            return self._get_version_from_accept(request)
        return self.default_version

    def _get_version_from_url(self, request: Request) -> int:
        """Extract version from URL path."""
        path = request.url.path

        # Match /v{number} pattern
        match = re.match(r"^/v(\d+)", path)
        if match:
            version = int(match.group(1))
            if version in self.versions or version in self._routers:
                return version

        # Check if any registered prefix matches
        for version, config in self.versions.items():
            if path.startswith(config.prefix):
                return version

        return self.default_version

    def _get_version_from_header(self, request: Request) -> int:
        """Extract version from X-API-Version header."""
        version_header = request.headers.get("X-API-Version")

        if version_header:
            try:
                return int(version_header)
            except ValueError:
                pass

        # Also check X-GAIA-API-Version
        gaia_header = request.headers.get("X-GAIA-API-Version")
        if gaia_header:
            try:
                return int(gaia_header)
            except ValueError:
                pass

        return self.default_version

    def _get_version_from_accept(self, request: Request) -> int:
        """Extract version from Accept header."""
        accept_header = request.headers.get("accept", "")

        # Match application/vnd.gaia.v{number}+json pattern
        match = re.search(r"application/vnd\.gaia\.v(\d+)\+json", accept_header)
        if match:
            return int(match.group(1))

        # Also check for version parameter
        match = re.search(r"version=(\d+)", accept_header)
        if match:
            return int(match.group(1))

        return self.default_version

    def register_version(
        self,
        version: int,
        prefix: str,
        status: str = "stable",
        sunset_date: Optional[str] = None,
        description: str = "",
    ) -> None:
        """
        Register a new API version.

        Args:
            version: Version number
            prefix: URL prefix (e.g., "/v1")
            status: Version status
            sunset_date: ISO 8601 sunset date for deprecated versions
            description: Optional version description

        Example:
            >>> versioning = APIVersioning(app)
            >>> versioning.register_version(
            ...     version=1,
            ...     prefix="/v1",
            ...     status="deprecated",
            ...     sunset_date="2026-12-31T23:59:59Z",
            ...     description="Legacy API"
            ... )
        """
        self.versions[version] = VersionConfig(
            version=version,
            prefix=prefix,
            status=status,
            sunset_date=sunset_date,
            description=description,
        )

        # Include router in app if it exists
        if version in self._routers:
            self.app.include_router(self._routers[version])

    def get_current_version(self) -> int:
        """
        Get current (latest stable) API version.

        Returns:
            Current version number

        Example:
            >>> versioning = APIVersioning(app)
            >>> versioning.register_version(1, "/v1", "deprecated")
            >>> versioning.register_version(2, "/v2", "current")
            >>> versioning.get_current_version()
            2
        """
        # Find highest version with 'current' status
        for version, config in sorted(self.versions.items(), reverse=True):
            if config.status == "current":
                return version

        # Fall back to highest registered version
        if self.versions:
            return max(self.versions.keys())

        return self.default_version

    def get_deprecated_versions(self) -> List[int]:
        """
        Get list of deprecated versions.

        Returns:
            List of deprecated version numbers

        Example:
            >>> versioning = APIVersioning(app)
            >>> versioning.register_version(1, "/v1", "deprecated")
            >>> versioning.register_version(2, "/v2", "current")
            >>> versioning.get_deprecated_versions()
            [1]
        """
        return [
            version for version, config in self.versions.items()
            if config.is_deprecated()
        ]

    def get_version_info(self, version: int) -> Optional[VersionConfig]:
        """
        Get version configuration.

        Args:
            version: Version number

        Returns:
            Version configuration or None

        Example:
            >>> versioning = APIVersioning(app)
            >>> versioning.register_version(1, "/v1", "stable")
            >>> info = versioning.get_version_info(1)
            >>> info.status
            'stable'
        """
        return self.versions.get(version)

    def get_available_versions(self) -> List[int]:
        """
        Get list of all available versions.

        Returns:
            Sorted list of version numbers

        Example:
            >>> versioning = APIVersioning(app)
            >>> versioning.register_version(1, "/v1")
            >>> versioning.register_version(2, "/v2")
            >>> versioning.get_available_versions()
            [1, 2]
        """
        return sorted(self.versions.keys())

    def get_version_status(self, version: int) -> str:
        """
        Get status of a specific version.

        Args:
            version: Version number

        Returns:
            Version status string

        Example:
            >>> versioning = APIVersioning(app)
            >>> versioning.register_version(1, "/v1", "deprecated")
            >>> versioning.get_version_status(1)
            'deprecated'
        """
        config = self.versions.get(version)
        return config.status if config else "unknown"

    def add_version_header_middleware(self) -> None:
        """
        Add middleware to inject version headers in responses.

        This middleware adds X-API-Version and Deprecation headers
        based on the resolved version.

        Example:
            >>> versioning = APIVersioning(app)
            >>> versioning.add_version_header_middleware()
        """
        @self.app.middleware("http")
        async def version_header_middleware(request: Request, call_next):
            response = await call_next(request)

            # Get version from request
            version = self.get_version_from_request(request)
            response.headers["X-API-Version"] = str(version)

            # Add deprecation headers if version is deprecated
            config = self.versions.get(version)
            if config and config.is_deprecated():
                response.headers["Deprecation"] = "true"
                if config.sunset_date:
                    response.headers["Sunset"] = config.sunset_date

            return response

        self._version_middleware_installed = True

    def get_sunset_versions(self) -> List[int]:
        """
        Get list of versions that have reached sunset.

        Returns:
            List of sunset version numbers

        Example:
            >>> versioning = APIVersioning(app)
            >>> past_date = "2020-01-01T00:00:00Z"
            >>> versioning.register_version(1, "/v1", "deprecated", past_date)
            >>> versioning.get_sunset_versions()
            [1]
        """
        return [
            version for version, config in self.versions.items()
            if config.is_sunset()
        ]


def versioned_route(
    versioning: APIVersioning,
    versions: List[int],
    path: str,
    **kwargs: Any,
) -> Callable:
    """
    Decorator for creating version-specific route handlers.

    Args:
        versioning: APIVersioning instance
        versions: List of versions this route applies to
        path: Route path (without version prefix)
        **kwargs: Additional route arguments

    Returns:
        Decorator function

    Example:
        >>> versioning = APIVersioning(app)
        >>>
        >>> @versioned_route(versioning, [1, 2], "/resource")
        ... def get_resource(request: Request):
        ...     version = versioning.get_version_from_request(request)
        ...     return {"version": f"v{version}"}
    """
    def decorator(func: Callable) -> Callable:
        for version in versions:
            router = versioning.create_router(version)

            # Determine HTTP method from function name or kwargs
            methods = kwargs.get("methods", ["GET"])

            # Register route for each version
            for method in methods:
                route_kwargs = {k: v for k, v in kwargs.items() if k != "methods"}
                router.add_api_route(
                    path,
                    func,
                    methods=[method],
                    **route_kwargs,
                )

            # Include router in app
            if router not in versioning.app.routes:
                versioning.app.include_router(router)

        return func

    return decorator


def require_version(
    versioning: APIVersioning,
    min_version: Optional[int] = None,
    max_version: Optional[int] = None,
    allowed_versions: Optional[List[int]] = None,
) -> Callable:
    """
    Create dependency for version validation.

    Args:
        versioning: APIVersioning instance
        min_version: Minimum allowed version
        max_version: Maximum allowed version
        allowed_versions: List of explicitly allowed versions

    Returns:
        FastAPI dependency function

    Raises:
        HTTPException: If version is not allowed

    Example:
        >>> versioning = APIVersioning(app)
        >>>
        >>> @app.get("/resource")
        ... def get_resource(
        ...     _: int = Depends(require_version(versioning, min_version=2))
        ... ):
        ...     return {"data": "v2+"}
    """
    async def version_validator(request: Request) -> int:
        version = versioning.get_version_from_request(request)

        if allowed_versions and version not in allowed_versions:
            raise HTTPException(
                status_code=400,
                detail=f"API version {version} is not supported. Allowed: {allowed_versions}",
            )

        if min_version and version < min_version:
            raise HTTPException(
                status_code=400,
                detail=f"API version {version} is too old. Minimum required: {min_version}",
            )

        if max_version and version > max_version:
            raise HTTPException(
                status_code=400,
                detail=f"API version {version} is too new. Maximum supported: {max_version}",
            )

        return version

    return version_validator
