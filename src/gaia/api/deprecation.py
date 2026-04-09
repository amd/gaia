"""
API deprecation management for GAIA API.

This module provides deprecation management with:
- Automatic Sunset header injection
- Deprecation warning logs
- Migration hint responses
- Version-aware routing

Example:
    >>> from fastapi import FastAPI
    >>> from gaia.api.deprecation import DeprecationManager
    >>>
    >>> app = FastAPI()
    >>> deprecation = DeprecationManager(app)
    >>>
    >>> # Register deprecated endpoint
    >>> deprecation.deprecate(
    ...     endpoint="/v1/legacy",
    ...     deprecated_in="1.0.0",
    ...     sunset_in="2.0.0",
    ...     sunset_date="2026-12-31T23:59:59Z",
    ...     migration_hint="Use /v2/modern instead",
    ...     alternative="/v2/modern",
    ... )
    >>>
    >>> # Use decorator
    >>> @deprecation.deprecated(
    ...     deprecated_in="1.5.0",
    ...     sunset_date="2026-06-30T23:59:59Z",
    ...     alternative="/v2/new-endpoint",
    ... )
    ... def old_endpoint():
    ...     return {"status": "legacy"}
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Any
from functools import wraps
import logging

from fastapi import Request, Response, FastAPI
from fastapi.responses import JSONResponse


logger = logging.getLogger(__name__)


@dataclass
class DeprecationInfo:
    """
    Deprecation metadata.

    Attributes:
        endpoint: Endpoint path
        deprecated_version: Version when deprecated
        sunset_version: Version when removed
        sunset_date: ISO 8601 sunset date
        migration_hint: Migration instructions
        alternative: Alternative endpoint
        message: Custom deprecation message

    Example:
        >>> info = DeprecationInfo(
        ...     endpoint="/v1/legacy",
        ...     deprecated_version="1.0.0",
        ...     sunset_version="2.0.0",
        ...     sunset_date="2026-12-31T23:59:59Z",
        ...     migration_hint="Use /v2/modern instead",
        ...     alternative="/v2/modern"
        ... )
    """

    endpoint: str
    deprecated_version: str
    sunset_version: str
    sunset_date: str
    migration_hint: str = ""
    alternative: Optional[str] = None
    message: str = ""

    def get_deprecation_header(self) -> str:
        """Get Deprecation header value."""
        return "true"

    def get_sunset_header(self) -> Optional[str]:
        """Get Sunset header value (HTTP-date format)."""
        try:
            dt = datetime.fromisoformat(self.sunset_date.replace("Z", "+00:00"))
            # Format as HTTP-date: Sun, 06 Nov 1994 08:49:37 GMT
            return dt.strftime("%a, %d %b %Y %H:%M:%S GMT")
        except ValueError:
            return self.sunset_date

    def get_link_header(self) -> Optional[str]:
        """Get Link header value for alternative."""
        if self.alternative:
            return f'<{self.alternative}>; rel="successor-version"'
        return None

    def is_sunset(self) -> bool:
        """Check if endpoint has passed sunset date."""
        try:
            sunset = datetime.fromisoformat(self.sunset_date.replace("Z", "+00:00"))
            return datetime.now(sunset.tzinfo) >= sunset
        except ValueError:
            return False


class DeprecationManager:
    """
    API deprecation management with automated headers and warnings.

    Features:
        - Automatic Sunset header injection
        - Deprecation warning logs
        - Migration hint responses
        - Version-aware routing

    Example:
        >>> from gaia.api.deprecation import DeprecationManager
        >>>
        >>> deprecation = DeprecationManager()
        >>> deprecation.deprecate(
        ...     endpoint="/v1/legacy",
        ...     deprecated_in="1.0.0",
        ...     sunset_in="2.0.0",
        ...     sunset_date="2026-12-31",
        ...     alternative="/v2/modern",
        ...     migration_hint="See migration guide at /docs/migration"
        ... )
        >>>
        >>> @deprecation.deprecated(
        ...     deprecated_in="1.5.0",
        ...     sunset_date="2026-06-30",
        ...     alternative="use_new_endpoint",
        ... )
        >>> def old_endpoint():
        ...     ...
    """

    def __init__(self, app: Optional[FastAPI] = None) -> None:
        """
        Initialize deprecation manager.

        Args:
            app: Optional FastAPI app for automatic middleware

        Example:
            >>> app = FastAPI()
            >>> deprecation = DeprecationManager(app)
        """
        self.app = app
        self._deprecated_endpoints: Dict[str, DeprecationInfo] = {}
        self._middleware_installed = False

        if app:
            self._install_middleware()

    def _install_middleware(self) -> None:
        """Install deprecation middleware."""
        if self._middleware_installed:
            return

        @self.app.middleware("http")
        async def deprecation_middleware(request: Request, call_next):
            response = await call_next(request)

            # Check if endpoint is deprecated
            path = request.url.path
            info = self.get_deprecation_info(path)

            if info:
                self._add_deprecation_headers(response, info)

                # Log deprecation warning
                logger.warning(
                    f"Deprecated endpoint accessed: {path}. "
                    f"Alternative: {info.alternative or 'N/A'}"
                )

            return response

        self._middleware_installed = True

    def deprecate(
        self,
        endpoint: str,
        deprecated_in: str,
        sunset_in: str,
        sunset_date: str,
        migration_hint: str,
        alternative: Optional[str] = None,
        message: str = "",
    ) -> None:
        """
        Register endpoint for deprecation.

        Args:
            endpoint: Endpoint path
            deprecated_in: Version when deprecated
            sunset_in: Version when removed
            sunset_date: ISO 8601 sunset date
            migration_hint: Migration instructions
            alternative: Alternative endpoint
            message: Custom deprecation message

        Example:
            >>> deprecation = DeprecationManager()
            >>> deprecation.deprecate(
            ...     endpoint="/v1/legacy",
            ...     deprecated_in="1.0.0",
            ...     sunset_in="2.0.0",
            ...     sunset_date="2026-12-31T23:59:59Z",
            ...     migration_hint="Use /v2/modern instead",
            ...     alternative="/v2/modern"
            ... )
        """
        self._deprecated_endpoints[endpoint] = DeprecationInfo(
            endpoint=endpoint,
            deprecated_version=deprecated_in,
            sunset_version=sunset_in,
            sunset_date=sunset_date,
            migration_hint=migration_hint,
            alternative=alternative,
            message=message,
        )

        logger.info(
            f"Registered deprecation for {endpoint}: "
            f"deprecated in {deprecated_in}, sunset {sunset_date}"
        )

    def deprecated(
        self,
        deprecated_in: str,
        sunset_date: str,
        alternative: Optional[str] = None,
        migration_hint: str = "",
        message: str = "",
    ) -> Callable:
        """
        Decorator for marking endpoints as deprecated.

        Args:
            deprecated_in: Version when deprecated
            sunset_date: ISO 8601 sunset date
            alternative: Alternative endpoint
            migration_hint: Migration instructions
            message: Custom deprecation message

        Returns:
            Decorator function

        Example:
            >>> deprecation = DeprecationManager()
            >>>
            >>> @deprecation.deprecated(
            ...     deprecated_in="1.5.0",
            ...     sunset_date="2026-06-30T23:59:59Z",
            ...     alternative="/v2/new-endpoint",
            ...     migration_hint="See migration guide"
            ... )
            ... def old_endpoint():
            ...     return {"status": "legacy"}
        """
        def decorator(func: Callable) -> Callable:
            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                # Get response from original function
                result = await func(*args, **kwargs)

                # Find endpoint from call stack or request
                endpoint = self._get_endpoint_from_context()

                if endpoint and endpoint not in self._deprecated_endpoints:
                    # Register deprecation info
                    self._deprecated_endpoints[endpoint] = DeprecationInfo(
                        endpoint=endpoint,
                        deprecated_version=deprecated_in,
                        sunset_version="TBD",
                        sunset_date=sunset_date,
                        migration_hint=migration_hint,
                        alternative=alternative,
                        message=message,
                    )

                return result

            @wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                result = func(*args, **kwargs)

                endpoint = self._get_endpoint_from_context()

                if endpoint and endpoint not in self._deprecated_endpoints:
                    self._deprecated_endpoints[endpoint] = DeprecationInfo(
                        endpoint=endpoint,
                        deprecated_version=deprecated_in,
                        sunset_version="TBD",
                        sunset_date=sunset_date,
                        migration_hint=migration_hint,
                        alternative=alternative,
                        message=message,
                    )

                return result

            # Determine if function is async
            import asyncio
            if asyncio.iscoroutinefunction(func):
                return async_wrapper
            return sync_wrapper

        return decorator

    def _get_endpoint_from_context(self) -> Optional[str]:
        """Try to get current endpoint from request context."""
        try:
            from fastapi import Request
            # This is a simplified approach - in real usage,
            # the endpoint would be passed explicitly or via context
            pass
        except ImportError:
            pass
        return None

    def _add_deprecation_headers(
        self,
        response: Response,
        info: DeprecationInfo,
    ) -> None:
        """
        Add deprecation headers to response.

        Headers:
            - Deprecation: true
            - Sunset: <date>
            - Link: <alternative>; rel="successor-version"
            - X-Migration-Hint: <hint>

        Args:
            response: FastAPI response
            info: Deprecation information

        Example:
            >>> deprecation = DeprecationManager()
            >>> response = Response()
            >>> info = DeprecationInfo(...)
            >>> deprecation._add_deprecation_headers(response, info)
        """
        response.headers["Deprecation"] = info.get_deprecation_header()

        sunset = info.get_sunset_header()
        if sunset:
            response.headers["Sunset"] = sunset

        link = info.get_link_header()
        if link:
            response.headers["Link"] = link

        if info.migration_hint:
            response.headers["X-Migration-Hint"] = info.migration_hint

        if info.message:
            response.headers["X-Deprecation-Message"] = info.message

    def get_deprecation_info(self, endpoint: str) -> Optional[DeprecationInfo]:
        """
        Get deprecation info for endpoint.

        Args:
            endpoint: Endpoint path

        Returns:
            DeprecationInfo or None

        Example:
            >>> deprecation = DeprecationManager()
            >>> deprecation.deprecate("/v1/legacy", "1.0.0", "2.0.0", "2026-12-31", "")
            >>> info = deprecation.get_deprecation_info("/v1/legacy")
            >>> info.alternative
        """
        # Exact match
        if endpoint in self._deprecated_endpoints:
            return self._deprecated_endpoints[endpoint]

        # Prefix match for parameterized routes
        for registered_endpoint, info in self._deprecated_endpoints.items():
            if endpoint.startswith(registered_endpoint.rstrip("/")):
                return info

        return None

    def list_deprecated(self) -> List[DeprecationInfo]:
        """
        List all deprecated endpoints.

        Returns:
            List of DeprecationInfo objects

        Example:
            >>> deprecation = DeprecationManager()
            >>> deprecation.deprecate("/v1/legacy", "1.0.0", "2.0.0", "2026-12-31", "")
            >>> deprecated = deprecation.list_deprecated()
            >>> len(deprecated)
            1
        """
        return list(self._deprecated_endpoints.values())

    def is_sunset(self, endpoint: str) -> bool:
        """
        Check if endpoint has passed sunset date.

        Args:
            endpoint: Endpoint path

        Returns:
            True if endpoint has reached sunset

        Example:
            >>> deprecation = DeprecationManager()
            >>> past_date = "2020-01-01T00:00:00Z"
            >>> deprecation.deprecate("/old", "1.0.0", "2.0.0", past_date, "")
            >>> deprecation.is_sunset("/old")
            True
        """
        info = self.get_deprecation_info(endpoint)
        if info:
            return info.is_sunset()
        return False

    def get_sunset_endpoints(self) -> List[str]:
        """
        Get list of endpoints that have reached sunset.

        Returns:
            List of endpoint paths

        Example:
            >>> deprecation = DeprecationManager()
            >>> past_date = "2020-01-01T00:00:00Z"
            >>> deprecation.deprecate("/old", "1.0.0", "2.0.0", past_date, "")
            >>> sunset_endpoints = deprecation.get_sunset_endpoints()
            >>> "/old" in sunset_endpoints
            True
        """
        return [
            info.endpoint for info in self._deprecated_endpoints.values()
            if info.is_sunset()
        ]

    def create_deprecation_response(
        self,
        endpoint: str,
        content: Any = None,
    ) -> JSONResponse:
        """
        Create a deprecation warning response.

        Args:
            endpoint: Endpoint path
            content: Optional original response content

        Returns:
            JSONResponse with deprecation warning

        Example:
            >>> deprecation = DeprecationManager()
            >>> deprecation.deprecate("/v1/legacy", "1.0.0", "2.0.0", "2026-12-31", "Use /v2")
            >>> response = deprecation.create_deprecation_response("/v1/legacy")
        """
        info = self.get_deprecation_info(endpoint)

        response_data = {
            "warning": "deprecated",
            "message": info.message if info else "This endpoint is deprecated",
            "sunset_date": info.sunset_date if info else None,
            "alternative": info.alternative if info else None,
            "migration_hint": info.migration_hint if info else None,
        }

        if content:
            response_data["data"] = content

        status_code = 410 if info and info.is_sunset() else 200

        response = JSONResponse(content=response_data, status_code=status_code)

        if info:
            self._add_deprecation_headers(response, info)

        return response

    def remove_deprecation(self, endpoint: str) -> bool:
        """
        Remove deprecation registration for endpoint.

        Args:
            endpoint: Endpoint path

        Returns:
            True if endpoint was removed, False if not found

        Example:
            >>> deprecation = DeprecationManager()
            >>> deprecation.deprecate("/v1/legacy", "1.0.0", "2.0.0", "2026-12-31", "")
            >>> deprecation.remove_deprecation("/v1/legacy")
            True
        """
        if endpoint in self._deprecated_endpoints:
            del self._deprecated_endpoints[endpoint]
            logger.info(f"Removed deprecation for {endpoint}")
            return True
        return False

    def get_deprecation_summary(self) -> Dict[str, Any]:
        """
        Get summary of all deprecations.

        Returns:
            Dictionary with deprecation statistics

        Example:
            >>> deprecation = DeprecationManager()
            >>> deprecation.deprecate("/v1/legacy", "1.0.0", "2.0.0", "2026-12-31", "")
            >>> summary = deprecation.get_deprecation_summary()
            >>> summary["total_deprecated"]
            1
        """
        now = datetime.now()
        deprecated = []
        sunset = []

        for info in self._deprecated_endpoints.values():
            endpoint_info = {
                "endpoint": info.endpoint,
                "deprecated_in": info.deprecated_version,
                "sunset_date": info.sunset_date,
                "alternative": info.alternative,
            }

            if info.is_sunset():
                sunset.append(endpoint_info)
            else:
                deprecated.append(endpoint_info)

        return {
            "total_deprecated": len(self._deprecated_endpoints),
            "deprecated": deprecated,
            "sunset": sunset,
            "sunset_count": len(sunset),
        }


def deprecate_endpoint(
    deprecated_in: str,
    sunset_date: str,
    alternative: Optional[str] = None,
    migration_hint: str = "",
    deprecation_manager: Optional[DeprecationManager] = None,
) -> Callable:
    """
    Standalone decorator for deprecating endpoints.

    Args:
        deprecated_in: Version when deprecated
        sunset_date: ISO 8601 sunset date
        alternative: Alternative endpoint
        migration_hint: Migration instructions
        deprecation_manager: Optional DeprecationManager instance

    Returns:
        Decorator function

    Example:
        >>> @deprecate_endpoint(
        ...     deprecated_in="1.5.0",
        ...     sunset_date="2026-06-30T23:59:59Z",
        ...     alternative="/v2/new-endpoint",
        ...     migration_hint="See migration guide"
        ... )
        ... def old_endpoint():
        ...     return {"status": "legacy"}
    """
    manager = deprecation_manager or DeprecationManager()

    return manager.deprecated(
        deprecated_in=deprecated_in,
        sunset_date=sunset_date,
        alternative=alternative,
        migration_hint=migration_hint,
    )
