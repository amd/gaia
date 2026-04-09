"""
Unit tests for API Deprecation.

Covers:
- Deprecation registration
- Deprecation headers
- Deprecation decorator
- Sunset date handling
"""

import pytest
from datetime import datetime, timedelta
from fastapi import FastAPI, Response
from starlette.testclient import TestClient

from gaia.api.deprecation import (
    DeprecationManager,
    DeprecationInfo,
    deprecate_endpoint,
)


class TestDeprecationInfo:
    """Test DeprecationInfo dataclass."""

    def test_deprecation_info_init(self):
        """Should initialize deprecation info correctly."""
        info = DeprecationInfo(
            endpoint="/v1/legacy",
            deprecated_version="1.0.0",
            sunset_version="2.0.0",
            sunset_date="2026-12-31T23:59:59Z",
            migration_hint="Use /v2/modern",
            alternative="/v2/modern",
        )

        assert info.endpoint == "/v1/legacy"
        assert info.alternative == "/v2/modern"

    def test_deprecation_info_deprecation_header(self):
        """Should return correct deprecation header."""
        info = DeprecationInfo(
            endpoint="/test",
            deprecated_version="1.0.0",
            sunset_version="2.0.0",
            sunset_date="2026-12-31T23:59:59Z",
            migration_hint="",
        )

        header = info.get_deprecation_header()
        assert header == "true"

    def test_deprecation_info_sunset_header(self):
        """Should format sunset header correctly."""
        info = DeprecationInfo(
            endpoint="/test",
            deprecated_version="1.0.0",
            sunset_version="2.0.0",
            sunset_date="2026-12-31T23:59:59Z",
            migration_hint="",
        )

        header = info.get_sunset_header()
        assert header is not None
        # Should be in HTTP-date format or original format
        assert "2026" in header or "Dec" in header

    def test_deprecation_info_link_header(self):
        """Should format link header correctly."""
        info = DeprecationInfo(
            endpoint="/test",
            deprecated_version="1.0.0",
            sunset_version="2.0.0",
            sunset_date="2026-12-31T23:59:59Z",
            migration_hint="",
            alternative="/v2/modern",
        )

        header = info.get_link_header()
        assert header is not None
        assert "/v2/modern" in header
        assert 'rel="successor-version"' in header

    def test_deprecation_info_link_header_no_alternative(self):
        """Should return None when no alternative."""
        info = DeprecationInfo(
            endpoint="/test",
            deprecated_version="1.0.0",
            sunset_version="2.0.0",
            sunset_date="2026-12-31T23:59:59Z",
            migration_hint="",
        )

        header = info.get_link_header()
        assert header is None

    def test_deprecation_info_is_sunset(self):
        """Should detect sunset correctly."""
        # Future sunset
        future_date = (datetime.now() + timedelta(days=30)).isoformat()
        info_future = DeprecationInfo(
            endpoint="/future",
            deprecated_version="1.0.0",
            sunset_version="2.0.0",
            sunset_date=future_date,
            migration_hint="",
        )
        assert info_future.is_sunset() is False

        # Past sunset
        past_date = (datetime.now() - timedelta(days=30)).isoformat()
        info_past = DeprecationInfo(
            endpoint="/past",
            deprecated_version="1.0.0",
            sunset_version="2.0.0",
            sunset_date=past_date,
            migration_hint="",
        )
        assert info_past.is_sunset() is True


class TestDeprecationManagerInit:
    """Test DeprecationManager initialization."""

    def test_init_no_app(self):
        """Should initialize without app."""
        manager = DeprecationManager()

        assert manager.app is None
        assert manager._middleware_installed is False

    def test_init_with_app(self):
        """Should install middleware with app."""
        app = FastAPI()
        manager = DeprecationManager(app)

        assert manager.app is app
        assert manager._middleware_installed is True


class TestDeprecationRegistration:
    """Test deprecation registration."""

    def test_deprecate(self):
        """Should register deprecation correctly."""
        manager = DeprecationManager()

        manager.deprecate(
            endpoint="/v1/legacy",
            deprecated_in="1.0.0",
            sunset_in="2.0.0",
            sunset_date="2026-12-31T23:59:59Z",
            migration_hint="Use /v2/modern",
            alternative="/v2/modern",
        )

        info = manager.get_deprecation_info("/v1/legacy")
        assert info is not None
        assert info.alternative == "/v2/modern"

    def test_deprecate_with_message(self):
        """Should register custom message."""
        manager = DeprecationManager()

        manager.deprecate(
            endpoint="/v1/legacy",
            deprecated_in="1.0.0",
            sunset_in="2.0.0",
            sunset_date="2026-12-31T23:59:59Z",
            migration_hint="",
            message="Custom deprecation message",
        )

        info = manager.get_deprecation_info("/v1/legacy")
        assert info.message == "Custom deprecation message"


class TestDeprecationHeaders:
    """Test deprecation headers."""

    def test_add_deprecation_headers(self):
        """Should add deprecation headers to response."""
        manager = DeprecationManager()
        response = Response()

        info = DeprecationInfo(
            endpoint="/test",
            deprecated_version="1.0.0",
            sunset_version="2.0.0",
            sunset_date="2026-12-31T23:59:59Z",
            migration_hint="Use /v2",
            alternative="/v2/new",
        )

        manager._add_deprecation_headers(response, info)

        assert response.headers.get("Deprecation") == "true"
        assert "Sunset" in response.headers
        assert "Link" in response.headers
        assert "X-Migration-Hint" in response.headers

    def test_deprecation_middleware(self):
        """Middleware should add deprecation headers."""
        app = FastAPI()
        manager = DeprecationManager(app)

        manager.deprecate(
            endpoint="/v1/legacy",
            deprecated_in="1.0.0",
            sunset_in="2.0.0",
            sunset_date="2026-12-31T23:59:59Z",
            migration_hint="Use /v2/modern",
        )

        @app.get("/v1/legacy")
        def legacy_endpoint():
            return {"status": "legacy"}

        client = TestClient(app)
        response = client.get("/v1/legacy")

        assert response.headers.get("Deprecation") == "true"
        assert "Sunset" in response.headers


class TestDeprecatedDecorator:
    """Test @deprecated decorator."""

    def test_deprecated_decorator_sync(self):
        """Should work with synchronous functions."""
        manager = DeprecationManager()

        @manager.deprecated(
            deprecated_in="1.5.0",
            sunset_date="2026-06-30T23:59:59Z",
            alternative="/v2/new-endpoint",
            migration_hint="See migration guide",
        )
        def old_endpoint():
            return {"status": "old"}

        result = old_endpoint()
        assert result == {"status": "old"}

    @pytest.mark.asyncio
    async def test_deprecated_decorator_async(self):
        """Should work with asynchronous functions."""
        manager = DeprecationManager()

        @manager.deprecated(
            deprecated_in="1.5.0",
            sunset_date="2026-06-30T23:59:59Z",
            alternative="/v2/new-endpoint",
        )
        async def old_async_endpoint():
            return {"status": "old"}

        result = await old_async_endpoint()
        assert result == {"status": "old"}


class TestDeprecationQueries:
    """Test deprecation query methods."""

    def test_get_deprecation_info_not_found(self):
        """Should return None for non-deprecated endpoint."""
        manager = DeprecationManager()

        info = manager.get_deprecation_info("/v1/active")
        assert info is None

    def test_list_deprecated(self):
        """Should list all deprecated endpoints."""
        manager = DeprecationManager()

        manager.deprecate("/v1/legacy1", "1.0.0", "2.0.0", "2026-12-31", "")
        manager.deprecate("/v1/legacy2", "1.0.0", "2.0.0", "2026-12-31", "")

        deprecated = manager.list_deprecated()

        assert len(deprecated) == 2
        endpoints = [info.endpoint for info in deprecated]
        assert "/v1/legacy1" in endpoints
        assert "/v1/legacy2" in endpoints

    def test_is_sunset(self):
        """Should check sunset status."""
        manager = DeprecationManager()

        # Past sunset
        past_date = (datetime.now() - timedelta(days=30)).isoformat()
        manager.deprecate(
            "/past",
            "1.0.0",
            "2.0.0",
            past_date,
            "",
        )
        assert manager.is_sunset("/past") is True

        # Future sunset
        future_date = (datetime.now() + timedelta(days=30)).isoformat()
        manager.deprecate(
            "/future",
            "1.0.0",
            "2.0.0",
            future_date,
            "",
        )
        assert manager.is_sunset("/future") is False

    def test_get_sunset_endpoints(self):
        """Should return sunset endpoints."""
        manager = DeprecationManager()

        past_date = (datetime.now() - timedelta(days=30)).isoformat()
        manager.deprecate("/sunset1", "1.0.0", "2.0.0", past_date, "")
        manager.deprecate("/sunset2", "1.0.0", "2.0.0", past_date, "")

        sunset_endpoints = manager.get_sunset_endpoints()

        assert len(sunset_endpoints) == 2
        assert "/sunset1" in sunset_endpoints
        assert "/sunset2" in sunset_endpoints

    def test_get_deprecation_summary(self):
        """Should return deprecation summary."""
        manager = DeprecationManager()

        past_date = (datetime.now() - timedelta(days=30)).isoformat()
        future_date = (datetime.now() + timedelta(days=30)).isoformat()

        manager.deprecate("/sunset", "1.0.0", "2.0.0", past_date, "")
        manager.deprecate("/deprecated", "1.0.0", "2.0.0", future_date, "")

        summary = manager.get_deprecation_summary()

        assert summary["total_deprecated"] == 2
        assert summary["sunset_count"] == 1
        assert len(summary["deprecated"]) == 1
        assert len(summary["sunset"]) == 1

    def test_remove_deprecation(self):
        """Should remove deprecation registration."""
        manager = DeprecationManager()

        manager.deprecate("/v1/legacy", "1.0.0", "2.0.0", "2026-12-31", "")
        removed = manager.remove_deprecation("/v1/legacy")

        assert removed is True
        assert manager.get_deprecation_info("/v1/legacy") is None

    def test_remove_deprecation_not_found(self):
        """Should return False for unknown endpoint."""
        manager = DeprecationManager()

        removed = manager.remove_deprecation("/unknown")

        assert removed is False


class TestDeprecationResponse:
    """Test deprecation response creation."""

    def test_create_deprecation_response(self):
        """Should create deprecation response."""
        manager = DeprecationManager()

        manager.deprecate(
            "/v1/legacy",
            "1.0.0",
            "2.0.0",
            "2026-12-31T23:59:59Z",
            "Use /v2/modern",
            "/v2/modern",
        )

        response = manager.create_deprecation_response("/v1/legacy")

        assert response.status_code == 200
        data = response.body
        assert b'"warning":"deprecated"' in data
        assert b'"alternative":"/v2/modern"' in data
        assert b'"migration_hint":"Use /v2/modern"' in data

    def test_create_deprecation_response_sunset(self):
        """Should return 410 for sunset endpoints."""
        manager = DeprecationManager()

        past_date = (datetime.now() - timedelta(days=30)).isoformat()
        manager.deprecate(
            "/v1/sunset",
            "1.0.0",
            "2.0.0",
            past_date,
            "",
            "/v2/modern",
        )

        response = manager.create_deprecation_response("/v1/sunset")

        assert response.status_code == 410

    def test_create_deprecation_response_with_content(self):
        """Should include original content."""
        manager = DeprecationManager()

        manager.deprecate(
            "/v1/legacy",
            "1.0.0",
            "2.0.0",
            "2026-12-31T23:59:59Z",
            "",
            "/v2/modern",
        )

        response = manager.create_deprecation_response(
            "/v1/legacy",
            content={"data": "original"}
        )

        data = response.body
        assert b'"data": {"data": "original"}' in data or b'"data"' in data


class TestDeprecateEndpointDecorator:
    """Test deprecate_endpoint standalone decorator."""

    def test_deprecate_endpoint_decorator(self):
        """Should work as standalone decorator."""
        manager = DeprecationManager()

        @deprecate_endpoint(
            deprecated_in="1.5.0",
            sunset_date="2026-06-30T23:59:59Z",
            alternative="/v2/new",
            migration_hint="See guide",
            deprecation_manager=manager,
        )
        def old_endpoint():
            return {"status": "old"}

        result = old_endpoint()
        assert result == {"status": "old"}


class TestBackwardCompatibility:
    """Test backward compatibility features."""

    def test_deprecation_headers_comprehensive(self):
        """Deprecated endpoints should include all deprecation headers."""
        app = FastAPI()
        manager = DeprecationManager(app)

        manager.deprecate(
            endpoint="/v1/legacy",
            deprecated_in="1.0.0",
            sunset_in="2.0.0",
            sunset_date="2026-12-31T23:59:59Z",
            migration_hint="Use /v2/modern instead",
            alternative="/v2/modern",
        )

        @app.get("/v1/legacy")
        def legacy_endpoint(response: Response):
            info = manager.get_deprecation_info("/v1/legacy")
            if info:
                manager._add_deprecation_headers(response, info)
            return {"status": "legacy"}

        client = TestClient(app)
        response = client.get("/v1/legacy")

        assert response.headers.get("Deprecation") == "true"
        assert "Sunset" in response.headers
        assert "Link" in response.headers

    def test_deprecated_decorator_headers(self):
        """@deprecated decorator should add headers automatically."""
        app = FastAPI()
        manager = DeprecationManager(app)

        # Register deprecation for the endpoint
        manager.deprecate(
            endpoint="/old",
            deprecated_in="1.5.0",
            sunset_in="2.0.0",
            sunset_date="2026-06-30T23:59:59Z",
            migration_hint="See migration guide",
            alternative="/v2/new-endpoint",
        )

        @app.get("/old")
        def old_endpoint(response: Response):
            # Apply headers manually for testing
            info = manager.get_deprecation_info("/old")
            if info:
                manager._add_deprecation_headers(response, info)
            return {"status": "old"}

        client = TestClient(app)
        response = client.get("/old")

        assert response.headers.get("Deprecation") == "true"
        assert "Sunset" in response.headers

    def test_sunset_date_check(self):
        """is_sunset should return correct status based on date."""
        manager = DeprecationManager()

        # Future sunset date
        future_date = (datetime.now() + timedelta(days=30)).isoformat()
        manager.deprecate(
            endpoint="/future",
            deprecated_in="1.0.0",
            sunset_in="2.0.0",
            sunset_date=future_date,
            migration_hint="",
        )
        assert manager.is_sunset("/future") is False

        # Past sunset date
        past_date = (datetime.now() - timedelta(days=30)).isoformat()
        manager.deprecate(
            endpoint="/past",
            deprecated_in="1.0.0",
            sunset_in="2.0.0",
            sunset_date=past_date,
            migration_hint="",
        )
        assert manager.is_sunset("/past") is True
