"""
Unit tests for API Versioning.

Covers:
- URL versioning strategy
- Header versioning strategy
- Accept header versioning strategy
- Version registration
- Version negotiation
"""

import pytest
from fastapi import FastAPI, Request
from starlette.testclient import TestClient

from gaia.api.versioning import (
    APIVersioning,
    VersionStrategy,
    VersionConfig,
    versioned_route,
    require_version,
)


class TestVersionConfig:
    """Test VersionConfig dataclass."""

    def test_version_config_init(self):
        """Should initialize version config correctly."""
        config = VersionConfig(
            version=1,
            prefix="/v1",
            status="stable",
        )

        assert config.version == 1
        assert config.prefix == "/v1"
        assert config.status == "stable"

    def test_version_config_invalid_status(self):
        """Should raise on invalid status."""
        with pytest.raises(ValueError, match="Invalid status"):
            VersionConfig(version=1, prefix="/v1", status="invalid")

    def test_version_config_is_deprecated(self):
        """Should detect deprecated versions."""
        config = VersionConfig(version=1, prefix="/v1", status="deprecated")
        assert config.is_deprecated() is True

        config_stable = VersionConfig(version=2, prefix="/v2", status="stable")
        assert config_stable.is_deprecated() is False

    def test_version_config_is_sunset(self):
        """Should detect sunset versions."""
        config = VersionConfig(version=1, prefix="/v1", status="sunset")
        assert config.is_sunset() is True

        config_past = VersionConfig(
            version=1,
            prefix="/v1",
            status="deprecated",
            sunset_date="2020-01-01T00:00:00Z",
        )
        assert config_past.is_sunset() is True

        config_future = VersionConfig(
            version=1,
            prefix="/v1",
            status="deprecated",
            sunset_date="2099-01-01T00:00:00Z",
        )
        assert config_future.is_sunset() is False


class TestAPIVersioningInit:
    """Test APIVersioning initialization."""

    def test_init_default_values(self):
        """Should initialize with default values."""
        app = FastAPI()
        versioning = APIVersioning(app)

        assert versioning.default_version == 1
        assert versioning.strategy == VersionStrategy.URL

    def test_init_custom_values(self):
        """Should initialize with custom values."""
        app = FastAPI()
        versioning = APIVersioning(
            app,
            default_version=2,
            strategy=VersionStrategy.HEADER,
        )

        assert versioning.default_version == 2
        assert versioning.strategy == VersionStrategy.HEADER

    def test_init_with_versions(self):
        """Should register provided versions."""
        app = FastAPI()
        versions = [
            VersionConfig(1, "/v1", "deprecated"),
            VersionConfig(2, "/v2", "current"),
        ]
        versioning = APIVersioning(app, versions=versions)

        assert 1 in versioning.versions
        assert 2 in versioning.versions
        assert versioning.versions[1].status == "deprecated"
        assert versioning.versions[2].status == "current"


class TestURLVersioning:
    """Test URL versioning strategy."""

    def test_url_versioning_basic(self):
        """URL versioning should route to correct version."""
        app = FastAPI()
        versioning = APIVersioning(app, strategy=VersionStrategy.URL)

        v1 = versioning.create_router(1)
        v2 = versioning.create_router(2)

        @v1.get("/resource")
        def get_v1():
            return {"version": "v1"}

        @v2.get("/resource")
        def get_v2():
            return {"version": "v2"}

        app.include_router(v1)
        app.include_router(v2)

        client = TestClient(app)

        response = client.get("/v1/resource")
        assert response.json()["version"] == "v1"

        response = client.get("/v2/resource")
        assert response.json()["version"] == "v2"

    def test_get_version_from_url(self):
        """Should extract version from URL path."""
        app = FastAPI()
        versioning = APIVersioning(app, strategy=VersionStrategy.URL)

        @app.get("/v1/test")
        def test_v1(request: Request):
            version = versioning.get_version_from_request(request)
            return {"version": version}

        client = TestClient(app)
        response = client.get("/v1/test")

        assert response.json()["version"] == 1

    def test_get_version_from_url_default(self):
        """Should return default version when no version in URL."""
        app = FastAPI()
        versioning = APIVersioning(app, strategy=VersionStrategy.URL, default_version=2)

        @app.get("/test")
        def test_default(request: Request):
            version = versioning.get_version_from_request(request)
            return {"version": version}

        client = TestClient(app)
        response = client.get("/test")

        assert response.json()["version"] == 2


class TestHeaderVersioning:
    """Test header versioning strategy."""

    def test_header_versioning_basic(self):
        """Header versioning should extract version from header."""
        app = FastAPI()
        versioning = APIVersioning(app, strategy=VersionStrategy.HEADER)

        @app.get("/resource")
        def get_resource(request: Request):
            version = versioning.get_version_from_request(request)
            return {"version": f"v{version}"}

        client = TestClient(app)

        response = client.get("/resource", headers={"X-API-Version": "2"})
        assert response.json()["version"] == "v2"

    def test_header_versioning_gaia_header(self):
        """Should support X-GAIA-API-Version header."""
        app = FastAPI()
        versioning = APIVersioning(app, strategy=VersionStrategy.HEADER)

        @app.get("/resource")
        def get_resource(request: Request):
            version = versioning.get_version_from_request(request)
            return {"version": f"v{version}"}

        client = TestClient(app)

        response = client.get("/resource", headers={"X-GAIA-API-Version": "3"})
        assert response.json()["version"] == "v3"

    def test_header_versioning_default(self):
        """Should return default version when header missing."""
        app = FastAPI()
        versioning = APIVersioning(app, strategy=VersionStrategy.HEADER, default_version=1)

        @app.get("/resource")
        def get_resource(request: Request):
            version = versioning.get_version_from_request(request)
            return {"version": f"v{version}"}

        client = TestClient(app)
        response = client.get("/resource")

        assert response.json()["version"] == "v1"


class TestAcceptVersioning:
    """Test Accept header versioning strategy."""

    def test_accept_versioning_basic(self):
        """Accept versioning should parse version from Accept header."""
        app = FastAPI()
        versioning = APIVersioning(app, strategy=VersionStrategy.ACCEPT)

        @app.get("/resource")
        def get_resource(request: Request):
            version = versioning.get_version_from_request(request)
            return {"version": f"v{version}"}

        client = TestClient(app)

        response = client.get(
            "/resource",
            headers={"Accept": "application/vnd.gaia.v2+json"}
        )
        assert response.json()["version"] == "v2"

    def test_accept_versioning_version_param(self):
        """Should support version parameter in Accept."""
        from starlette.requests import Request
        from starlette.datastructures import Headers

        app = FastAPI()
        versioning = APIVersioning(app, strategy=VersionStrategy.ACCEPT)

        @app.get("/resource")
        def get_resource(request: Request):
            version = versioning.get_version_from_request(request)
            return {"version": f"v{version}"}

        # Simulate request with version parameter
        class MockRequest:
            def __init__(self, accept: str):
                self.headers = Headers({"accept": accept})

        mock_request = MockRequest("application/json; version=3")
        version = versioning.get_version_from_request(mock_request)
        assert version == 3

    def test_accept_versioning_default(self):
        """Should return default version when Accept has no version."""
        app = FastAPI()
        versioning = APIVersioning(app, strategy=VersionStrategy.ACCEPT, default_version=1)

        @app.get("/resource")
        def get_resource(request: Request):
            version = versioning.get_version_from_request(request)
            return {"version": f"v{version}"}

        client = TestClient(app)
        response = client.get("/resource", headers={"Accept": "application/json"})

        assert response.json()["version"] == "v1"


class TestVersionRegistration:
    """Test version registration."""

    def test_register_version(self):
        """Should register version correctly."""
        app = FastAPI()
        versioning = APIVersioning(app)

        versioning.register_version(1, "/v1", status="stable")

        assert 1 in versioning.versions
        assert versioning.versions[1].prefix == "/v1"
        assert versioning.versions[1].status == "stable"

    def test_register_version_with_sunset(self):
        """Should register version with sunset date."""
        app = FastAPI()
        versioning = APIVersioning(app)

        versioning.register_version(
            1,
            "/v1",
            status="deprecated",
            sunset_date="2026-12-31T23:59:59Z",
        )

        assert versioning.versions[1].sunset_date == "2026-12-31T23:59:59Z"

    def test_get_current_version(self):
        """Should return current version."""
        app = FastAPI()
        versioning = APIVersioning(app)

        versioning.register_version(1, "/v1", status="deprecated")
        versioning.register_version(2, "/v2", status="current")
        versioning.register_version(3, "/v3", status="beta")

        assert versioning.get_current_version() == 2

    def test_get_current_version_no_current(self):
        """Should return highest version when no 'current'."""
        app = FastAPI()
        versioning = APIVersioning(app)

        versioning.register_version(1, "/v1", status="stable")
        versioning.register_version(2, "/v2", status="stable")
        versioning.register_version(3, "/v3", status="stable")

        assert versioning.get_current_version() == 3

    def test_get_deprecated_versions(self):
        """Should return deprecated versions."""
        app = FastAPI()
        versioning = APIVersioning(app)

        versioning.register_version(1, "/v1", status="deprecated")
        versioning.register_version(2, "/v2", status="current")
        versioning.register_version(3, "/v3", status="sunset")

        deprecated = versioning.get_deprecated_versions()

        assert 1 in deprecated
        assert 3 in deprecated

    def test_get_version_info(self):
        """Should return version info."""
        app = FastAPI()
        versioning = APIVersioning(app)

        versioning.register_version(1, "/v1", status="stable", description="v1 API")

        info = versioning.get_version_info(1)

        assert info is not None
        assert info.description == "v1 API"

    def test_get_version_info_not_found(self):
        """Should return None for unknown version."""
        app = FastAPI()
        versioning = APIVersioning(app)

        info = versioning.get_version_info(99)

        assert info is None

    def test_get_available_versions(self):
        """Should return sorted available versions."""
        app = FastAPI()
        versioning = APIVersioning(app)

        versioning.register_version(3, "/v3")
        versioning.register_version(1, "/v1")
        versioning.register_version(2, "/v2")

        versions = versioning.get_available_versions()

        assert versions == [1, 2, 3]

    def test_get_version_status(self):
        """Should return version status."""
        app = FastAPI()
        versioning = APIVersioning(app)

        versioning.register_version(1, "/v1", status="deprecated")

        status = versioning.get_version_status(1)

        assert status == "deprecated"


class TestCreateRouter:
    """Test router creation."""

    def test_create_router(self):
        """Should create router for version."""
        app = FastAPI()
        versioning = APIVersioning(app)

        router = versioning.create_router(1)

        assert router is not None
        assert router.prefix == "/v1"

    def test_create_router_cached(self):
        """Should return cached router."""
        app = FastAPI()
        versioning = APIVersioning(app)

        router1 = versioning.create_router(1)
        router2 = versioning.create_router(1)

        assert router1 is router2

    def test_create_router_with_kwargs(self):
        """Should pass kwargs to router."""
        app = FastAPI()
        versioning = APIVersioning(app)

        router = versioning.create_router(1, tags=["v1"])

        assert "v1" in router.tags


class TestVersionedRoute:
    """Test versioned_route decorator."""

    def test_versioned_route_basic(self):
        """Should register route for multiple versions."""
        app = FastAPI()
        versioning = APIVersioning(app, strategy=VersionStrategy.HEADER)

        @versioned_route(versioning, [1, 2], "/resource")
        def get_resource(request: Request):
            version = versioning.get_version_from_request(request)
            return {"version": f"v{version}"}

        client = TestClient(app)

        response = client.get("/resource", headers={"X-API-Version": "1"})
        assert response.status_code == 200

        response = client.get("/resource", headers={"X-API-Version": "2"})
        assert response.status_code == 200


class TestRequireVersion:
    """Test require_version dependency."""

    @pytest.mark.asyncio
    async def test_require_version_min(self):
        """Should reject versions below minimum."""
        app = FastAPI()
        versioning = APIVersioning(app, strategy=VersionStrategy.HEADER)

        from fastapi import Depends

        @app.get("/resource")
        def get_resource(
            version: int = Depends(require_version(versioning, min_version=2))
        ):
            return {"version": version}

        client = TestClient(app)

        # Should fail with version 1
        response = client.get("/resource", headers={"X-API-Version": "1"})
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_require_version_allowed(self):
        """Should allow specified versions."""
        app = FastAPI()
        versioning = APIVersioning(app, strategy=VersionStrategy.HEADER)

        from fastapi import Depends

        @app.get("/resource")
        def get_resource(
            version: int = Depends(require_version(versioning, allowed_versions=[2, 3]))
        ):
            return {"version": version}

        client = TestClient(app)

        response = client.get("/resource", headers={"X-API-Version": "2"})
        assert response.status_code == 200


class TestVersionHeaderMiddleware:
    """Test version header middleware."""

    def test_add_version_header_middleware(self):
        """Should add version headers to responses."""
        app = FastAPI()
        versioning = APIVersioning(app)
        versioning.add_version_header_middleware()

        @app.get("/test")
        def test_endpoint():
            return {"status": "ok"}

        client = TestClient(app)
        response = client.get("/v1/test")

        assert "X-API-Version" in response.headers

    def test_deprecation_headers(self):
        """Should add deprecation headers for deprecated versions."""
        app = FastAPI()
        versioning = APIVersioning(app)

        versioning.register_version(
            1,
            "/v1",
            status="deprecated",
            sunset_date="2026-12-31T23:59:59Z",
        )
        versioning.add_version_header_middleware()

        @app.get("/test")
        def test_endpoint():
            return {"status": "ok"}

        client = TestClient(app)
        response = client.get("/v1/test")

        assert response.headers.get("Deprecation") == "true"
        assert "Sunset" in response.headers
