# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for pipeline template API endpoints."""

import json
import tempfile
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from gaia.ui.server import create_app
from gaia.ui.services.template_service import TemplateService


@pytest.fixture
def temp_templates_dir():
    """Create a temporary directory for template files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_template_service(temp_templates_dir):
    """Create a mock template service with temp directory."""
    return TemplateService(templates_dir=temp_templates_dir)


@pytest.fixture
def client_with_templates(temp_templates_dir, mock_template_service):
    """Create test client with mocked template service dependency."""
    app = create_app(db_path=":memory:")

    # Import router module to access its dependency
    from gaia.ui.routers import pipeline as pipeline_router

    # Override the dependency
    def override_get_service():
        return mock_template_service

    app.dependency_overrides[pipeline_router.get_template_service] = (
        override_get_service
    )

    with TestClient(app) as client:
        yield client

    # Clean up overrides
    app.dependency_overrides.clear()


@pytest.fixture
def sample_template():
    """Sample template data for API tests."""
    return {
        "name": "api-test-template",
        "description": "Template created via API",
        "quality_threshold": 0.88,
        "max_iterations": 7,
        "agent_categories": {
            "planning": ["planner"],
            "development": ["developer"],
            "quality": ["reviewer"],
        },
        "routing_rules": [
            {
                "condition": "defect_type == 'security'",
                "route_to": "security-auditor",
                "priority": 1,
                "loop_back": True,
                "guidance": "Fix security issues first",
            }
        ],
        "quality_weights": {
            "code_quality": 0.25,
            "requirements_coverage": 0.25,
            "testing": 0.25,
            "documentation": 0.15,
            "best_practices": 0.10,
        },
    }


@pytest.fixture
def created_template(client_with_templates, sample_template, temp_templates_dir):
    """Create a template file for testing."""
    template_path = temp_templates_dir / "existing-template.yaml"
    with open(template_path, "w", encoding="utf-8") as f:
        yaml.dump(
            {
                "name": "existing-template",
                "description": "Pre-existing template",
                "quality_threshold": 0.90,
                "max_iterations": 10,
            },
            f,
        )
    return template_path


class TestListTemplates:
    """Test GET /api/v1/pipeline/templates endpoint."""

    def test_list_templates_empty(self, client_with_templates):
        """Test listing templates when directory is empty."""
        response = client_with_templates.get("/api/v1/pipeline/templates")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["templates"] == []

    def test_list_templates_with_data(self, client_with_templates, temp_templates_dir):
        """Test listing templates with existing files."""
        # Create template files
        for name in ["template-a", "template-b"]:
            template_path = temp_templates_dir / f"{name}.yaml"
            with open(template_path, "w", encoding="utf-8") as f:
                yaml.dump(
                    {
                        "name": name,
                        "description": f"Test {name}",
                        "quality_threshold": 0.90,
                        "max_iterations": 10,
                    },
                    f,
                )

        response = client_with_templates.get("/api/v1/pipeline/templates")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        names = [t["name"] for t in data["templates"]]
        assert "template-a" in names
        assert "template-b" in names


class TestGetTemplate:
    """Test GET /api/v1/pipeline/templates/{name} endpoint."""

    def test_get_template_success(self, client_with_templates, created_template):
        """Test getting an existing template."""
        response = client_with_templates.get(
            "/api/v1/pipeline/templates/existing-template"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "existing-template"
        assert data["description"] == "Pre-existing template"

    def test_get_template_not_found(self, client_with_templates):
        """Test getting non-existent template."""
        response = client_with_templates.get("/api/v1/pipeline/templates/nonexistent")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    def test_get_template_raw(self, client_with_templates, created_template):
        """Test getting raw YAML content."""
        response = client_with_templates.get(
            "/api/v1/pipeline/templates/existing-template/raw"
        )
        assert response.status_code == 200
        assert "text/yaml" in response.headers.get("content-type", "")
        assert "existing-template" in response.text


class TestCreateTemplate:
    """Test POST /api/v1/pipeline/templates endpoint."""

    def test_create_template_success(self, client_with_templates, sample_template):
        """Test creating a new template."""
        response = client_with_templates.post(
            "/api/v1/pipeline/templates",
            json=sample_template,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "api-test-template"
        assert data["description"] == "Template created via API"
        assert data["quality_threshold"] == 0.88

    def test_create_template_duplicate(
        self, client_with_templates, created_template, sample_template
    ):
        """Test creating duplicate template."""
        sample_template["name"] = "existing-template"
        response = client_with_templates.post(
            "/api/v1/pipeline/templates",
            json=sample_template,
        )
        assert response.status_code == 400
        assert "already exists" in response.json()["detail"]

    def test_create_template_invalid_name(self, client_with_templates):
        """Test creating template with invalid name."""
        response = client_with_templates.post(
            "/api/v1/pipeline/templates",
            json={
                "name": "invalid@name",
                "description": "Bad name",
            },
        )
        assert response.status_code == 400

    def test_create_template_invalid_weights(self, client_with_templates):
        """Test creating template with invalid weights."""
        response = client_with_templates.post(
            "/api/v1/pipeline/templates",
            json={
                "name": "bad-weights",
                "quality_weights": {"a": 0.3, "b": 0.3},  # Sum != 1.0
            },
        )
        assert response.status_code == 400

    def test_create_template_invalid_threshold(self, client_with_templates):
        """Test creating template with invalid threshold."""
        response = client_with_templates.post(
            "/api/v1/pipeline/templates",
            json={
                "name": "bad-threshold",
                "quality_threshold": 2.0,
            },
        )
        # Pydantic validation returns 422 for invalid values
        assert response.status_code == 422


class TestUpdateTemplate:
    """Test PUT /api/v1/pipeline/templates/{name} endpoint."""

    def test_update_template_success(self, client_with_templates, created_template):
        """Test updating a template."""
        response = client_with_templates.put(
            "/api/v1/pipeline/templates/existing-template",
            json={
                "description": "Updated description",
                "quality_threshold": 0.95,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["description"] == "Updated description"
        assert data["quality_threshold"] == 0.95

    def test_update_template_not_found(self, client_with_templates):
        """Test updating non-existent template."""
        response = client_with_templates.put(
            "/api/v1/pipeline/templates/nonexistent",
            json={"description": "New description"},
        )
        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    def test_update_template_partial(self, client_with_templates, created_template):
        """Test partial update preserves other fields."""
        response = client_with_templates.put(
            "/api/v1/pipeline/templates/existing-template",
            json={"description": "Only changing description"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["description"] == "Only changing description"
        assert data["max_iterations"] == 10  # Unchanged


class TestDeleteTemplate:
    """Test DELETE /api/v1/pipeline/templates/{name} endpoint."""

    def test_delete_template_success(self, client_with_templates, created_template):
        """Test deleting a template."""
        response = client_with_templates.delete(
            "/api/v1/pipeline/templates/existing-template"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["deleted"] is True
        assert data["template"] == "existing-template"
        assert not created_template.exists()

    def test_delete_template_not_found(self, client_with_templates):
        """Test deleting non-existent template."""
        response = client_with_templates.delete(
            "/api/v1/pipeline/templates/nonexistent"
        )
        assert response.status_code == 404


class TestValidateTemplate:
    """Test GET /api/v1/pipeline/templates/{name}/validate endpoint."""

    def test_validate_valid_template(self, client_with_templates, created_template):
        """Test validating a valid template."""
        response = client_with_templates.get(
            "/api/v1/pipeline/templates/existing-template/validate"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert data["errors"] == []

    def test_validate_template_not_found(self, client_with_templates):
        """Test validating non-existent template."""
        response = client_with_templates.get(
            "/api/v1/pipeline/templates/nonexistent/validate"
        )
        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    def test_validate_invalid_yaml(self, client_with_templates, temp_templates_dir):
        """Test validating invalid YAML."""
        invalid_path = temp_templates_dir / "invalid.yaml"
        invalid_path.write_text("invalid: yaml: content: [")

        response = client_with_templates.get(
            "/api/v1/pipeline/templates/invalid/validate"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert any("YAML" in err for err in data["errors"])


class TestTemplateSecurity:
    """Test security aspects of template API."""

    def test_path_traversal_prevention(self, client_with_templates):
        """Test path traversal is prevented."""
        response = client_with_templates.get(
            "/api/v1/pipeline/templates/../../../etc/passwd"
        )
        # Should either 404 (not found) or 400 (invalid name)
        assert response.status_code in [400, 404]

    def test_special_characters_in_name(self, client_with_templates):
        """Test special characters in template name."""
        # URL-encoded null byte and other special chars should be rejected
        response = client_with_templates.get("/api/v1/pipeline/templates/test%00null")
        # Should return 400 (bad request) for invalid name
        assert response.status_code == 400
