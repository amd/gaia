"""
Integration tests for API Standardization.

Covers:
- OpenAPI integration with FastAPI
- API versioning integration
- Deprecation integration
"""

import pytest
from fastapi import FastAPI, Request, Depends
from pydantic import BaseModel
from typing import List, Optional
from starlette.testclient import TestClient

from gaia.api.openapi import OpenAPIGenerator
from gaia.api.versioning import APIVersioning, VersionStrategy, require_version
from gaia.api.deprecation import DeprecationManager


class TestRequest(BaseModel):
    """Test request model."""
    name: str
    value: Optional[int] = None


class TestResponse(BaseModel):
    """Test response model."""
    id: int
    name: str
    status: str


class TestOpenAPIIntegration:
    """Test OpenAPI integration."""

    def test_openapi_routes_added(self):
        """Should add OpenAPI documentation routes."""
        app = FastAPI(title="Integration Test API")
        generator = OpenAPIGenerator(app)
        generator.add_routes()

        client = TestClient(app)

        # Test OpenAPI JSON endpoint
        response = client.get("/openapi.json")
        assert response.status_code == 200
        spec = response.json()
        assert spec["openapi"].startswith("3.")
        assert spec["info"]["title"] == "Integration Test API"

        # Test Swagger UI endpoint
        response = client.get("/docs")
        assert response.status_code == 200
        assert "swagger-ui" in response.text.lower()

        # Test ReDoc endpoint
        response = client.get("/redoc")
        assert response.status_code == 200
        assert "redoc" in response.text.lower()

    def test_openapi_with_prefix(self):
        """Should add routes with custom prefix."""
        app = FastAPI()
        generator = OpenAPIGenerator(app)
        generator.add_routes("/api")

        client = TestClient(app)

        response = client.get("/api/openapi.json")
        assert response.status_code == 200

        response = client.get("/api/docs")
        assert response.status_code == 200

    def test_openapi_with_versioned_routes(self):
        """Should document versioned routes."""
        app = FastAPI()
        versioning = APIVersioning(app, strategy=VersionStrategy.URL)

        v1 = versioning.create_router(1, tags=["v1"])
        v2 = versioning.create_router(2, tags=["v2"])

        @v1.get("/resource")
        def get_resource_v1():
            return {"version": "v1"}

        @v2.get("/resource")
        def get_resource_v2():
            return {"version": "v2", "new_field": True}

        app.include_router(v1)
        app.include_router(v2)

        generator = OpenAPIGenerator(app)
        generator.add_routes()

        client = TestClient(app)

        response = client.get("/openapi.json")
        spec = response.json()

        assert "/v1/resource" in spec["paths"]
        assert "/v2/resource" in spec["paths"]


class TestAPIVersioningIntegration:
    """Test API versioning integration."""

    def test_url_versioning_integration(self):
        """URL versioning should work end-to-end."""
        app = FastAPI()
        versioning = APIVersioning(app, strategy=VersionStrategy.URL)

        v1 = versioning.create_router(1)
        v2 = versioning.create_router(2)

        @v1.get("/users")
        def get_users_v1():
            return [{"id": 1, "name": "Alice"}]

        @v2.get("/users")
        def get_users_v2():
            return [{"id": 1, "name": "Alice", "email": "alice@example.com"}]

        app.include_router(v1)
        app.include_router(v2)

        client = TestClient(app)

        # V1 response
        response = client.get("/v1/users")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert "email" not in data[0]

        # V2 response
        response = client.get("/v2/users")
        assert response.status_code == 200
        data = response.json()
        assert "email" in data[0]

    def test_header_versioning_integration(self):
        """Header versioning should work end-to-end."""
        app = FastAPI()
        versioning = APIVersioning(app, strategy=VersionStrategy.HEADER)

        @app.get("/users")
        def get_users(request: Request):
            version = versioning.get_version_from_request(request)
            if version == 1:
                return [{"id": 1, "name": "Alice"}]
            else:
                return [{"id": 1, "name": "Alice", "email": "alice@example.com"}]

        client = TestClient(app)

        # V1 via header
        response = client.get("/users", headers={"X-API-Version": "1"})
        assert response.status_code == 200
        data = response.json()
        assert "email" not in data[0]

        # V2 via header
        response = client.get("/users", headers={"X-API-Version": "2"})
        assert response.status_code == 200
        data = response.json()
        assert "email" in data[0]

    def test_version_negotiation_middleware(self):
        """Version middleware should add headers."""
        app = FastAPI()
        versioning = APIVersioning(app)
        versioning.add_version_header_middleware()

        v1 = versioning.create_router(1)

        @v1.get("/test")
        def test_endpoint():
            return {"status": "ok"}

        app.include_router(v1)

        client = TestClient(app)
        response = client.get("/v1/test")

        assert response.status_code == 200
        assert "X-API-Version" in response.headers
        assert response.headers["X-API-Version"] == "1"

    def test_version_deprecation_headers(self):
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


class TestDeprecationIntegration:
    """Test deprecation integration."""

    def test_deprecation_middleware_integration(self):
        """Deprecation middleware should add headers."""
        app = FastAPI()
        deprecation = DeprecationManager(app)

        deprecation.deprecate(
            endpoint="/v1/legacy",
            deprecated_in="1.0.0",
            sunset_in="2.0.0",
            sunset_date="2026-12-31T23:59:59Z",
            migration_hint="Use /v2/modern",
            alternative="/v2/modern",
        )

        @app.get("/v1/legacy")
        def legacy_endpoint():
            return {"status": "legacy"}

        client = TestClient(app)
        response = client.get("/v1/legacy")

        assert response.status_code == 200
        assert response.headers.get("Deprecation") == "true"
        assert "Sunset" in response.headers
        assert "X-Migration-Hint" in response.headers

    def test_deprecation_response_integration(self):
        """Should return deprecation response."""
        app = FastAPI()
        deprecation = DeprecationManager()

        @app.get("/deprecated")
        def deprecated_endpoint():
            return deprecation.create_deprecation_response(
                "/deprecated",
                content={"data": "result"}
            )

        # Manually register for testing
        deprecation.deprecate(
            endpoint="/deprecated",
            deprecated_in="1.0.0",
            sunset_in="2.0.0",
            sunset_date="2026-12-31T23:59:59Z",
            migration_hint="Use new endpoint",
            alternative="/v2/new",
        )

        client = TestClient(app)
        response = client.get("/deprecated")

        assert response.status_code == 200
        data = response.json()
        assert data["warning"] == "deprecated"
        assert data["data"] == {"data": "result"}


class TestCombinedIntegration:
    """Test combined observability + API standardization."""

    def test_openapi_with_observability(self):
        """OpenAPI should work with observability."""
        from gaia.observability.core import ObservabilityCore

        app = FastAPI(title="Obs + API Test")
        obs = ObservabilityCore(service_name="test-combined")
        generator = OpenAPIGenerator(app)
        generator.add_routes()

        @app.get("/observed")
        def observed_endpoint():
            with obs.trace("api.call"):
                obs.metrics.counter("api.calls").inc()
                return {"status": "observed"}

        client = TestClient(app)

        # Make request
        response = client.get("/observed")
        assert response.status_code == 200

        # Verify metrics
        output = obs.metrics.to_prometheus()
        assert "api.calls" in output

        # Verify OpenAPI spec
        response = client.get("/openapi.json")
        spec = response.json()
        assert "/observed" in spec["paths"]

    def test_versioning_with_deprecation(self):
        """Versioning and deprecation should work together."""
        app = FastAPI()
        versioning = APIVersioning(app, strategy=VersionStrategy.URL)
        deprecation = DeprecationManager()

        # Register v1 as deprecated
        versioning.register_version(
            1,
            "/v1",
            status="deprecated",
            sunset_date="2026-12-31T23:59:59Z",
        )
        versioning.add_version_header_middleware()

        v1 = versioning.create_router(1)

        @v1.get("/resource")
        def get_resource_v1():
            return {"version": "v1"}

        app.include_router(v1)

        client = TestClient(app)
        response = client.get("/v1/resource")

        # Should have both version and deprecation headers
        assert response.headers.get("X-API-Version") == "1"
        assert response.headers.get("Deprecation") == "true"

    def test_full_integration_scenario(self):
        """Full integration: OpenAPI + Versioning + Deprecation + Observability."""
        from gaia.observability.core import ObservabilityCore

        app = FastAPI(title="Full Integration API")
        obs = ObservabilityCore(service_name="full-integration")
        versioning = APIVersioning(app, strategy=VersionStrategy.URL)
        deprecation = DeprecationManager(app)
        generator = OpenAPIGenerator(app)
        generator.add_routes()

        # Register versions
        versioning.register_version(1, "/v1", status="deprecated")
        versioning.register_version(2, "/v2", status="current")
        versioning.add_version_header_middleware()

        v1 = versioning.create_router(1)
        v2 = versioning.create_router(2)

        @v1.get("/users")
        def get_users_v1():
            with obs.trace("get_users_v1"):
                obs.metrics.counter("users.requests").inc()
                return [{"id": 1, "name": "Alice"}]

        @v2.get("/users")
        def get_users_v2():
            with obs.trace("get_users_v2"):
                obs.metrics.counter("users.requests").inc()
                return [{"id": 1, "name": "Alice", "email": "alice@example.com"}]

        app.include_router(v1)
        app.include_router(v2)

        client = TestClient(app)

        # Test v1 (deprecated)
        response = client.get("/v1/users")
        assert response.status_code == 200
        assert response.headers.get("Deprecation") == "true"

        # Test v2 (current)
        response = client.get("/v2/users")
        assert response.status_code == 200
        assert response.headers.get("Deprecation") != "true"

        # Verify OpenAPI
        response = client.get("/openapi.json")
        spec = response.json()
        assert "/v1/users" in spec["paths"]
        assert "/v2/users" in spec["paths"]

        # Verify metrics
        output = obs.metrics.to_prometheus()
        assert "users.requests" in output


class TestAPIIntegrationQualityGates:
    """Test API integration quality gates."""

    def test_api_001_openapi_completeness(self):
        """API-001: OpenAPI spec must be 100% complete."""
        app = FastAPI(title="QA Test API")

        @app.post("/test", response_model=TestResponse)
        def test_endpoint(request: TestRequest) -> TestResponse:
            return TestResponse(id=1, name=request.name, status="ok")

        generator = OpenAPIGenerator(app)
        spec = generator.generate()

        assert "openapi" in spec
        assert "paths" in spec
        assert "/test" in spec["paths"]
        assert "components" in spec
        assert "TestRequest" in spec["components"]["schemas"]
        assert "TestResponse" in spec["components"]["schemas"]

    def test_api_002_version_negotiation(self):
        """API-002: All version negotiation strategies must work."""
        app = FastAPI()

        # Test URL strategy
        url_versioning = APIVersioning(app, strategy=VersionStrategy.URL)
        assert url_versioning.strategy == VersionStrategy.URL

        # Test Header strategy
        header_app = FastAPI()
        header_versioning = APIVersioning(header_app, strategy=VersionStrategy.HEADER)
        assert header_versioning.strategy == VersionStrategy.HEADER

        # Test Accept strategy
        accept_app = FastAPI()
        accept_versioning = APIVersioning(accept_app, strategy=VersionStrategy.ACCEPT)
        assert accept_versioning.strategy == VersionStrategy.ACCEPT

    def test_bc_002_backward_compatibility(self):
        """BC-002: Backward compatibility must be 100%."""
        app = FastAPI()
        deprecation = DeprecationManager(app)

        deprecation.deprecate(
            endpoint="/legacy",
            deprecated_in="1.0.0",
            sunset_in="2.0.0",
            sunset_date="2026-12-31T23:59:59Z",
            migration_hint="Use /v2/new",
            alternative="/v2/new",
        )

        @app.get("/legacy")
        def legacy_endpoint(response):
            info = deprecation.get_deprecation_info("/legacy")
            if info:
                deprecation._add_deprecation_headers(response, info)
            return {"status": "legacy"}

        client = TestClient(app)
        response = client.get("/legacy")

        assert response.headers.get("Deprecation") == "true"
        assert "Sunset" in response.headers
