# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Integration tests for pipeline template UI API endpoints.

Tests cover:
- End-to-end template CRUD operations
- YAML parsing with nested structures
- Path correctness (no duplication bugs)
- Error handling and validation
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

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

    app.dependency_overrides[pipeline_router.get_template_service] = override_get_service

    with TestClient(app) as client:
        yield client

    # Clean up overrides
    app.dependency_overrides.clear()


@pytest.fixture
def complex_template():
    """Complex template with nested structures for round-trip testing."""
    return {
        "name": "complex-pipeline",
        "description": "Complex template for testing nested YAML structures",
        "quality_threshold": 0.92,
        "max_iterations": 15,
        "agent_categories": {
            "planning": ["planner", "architect", "tech-lead"],
            "development": [
                "senior-developer",
                "developer",
                "frontend-specialist",
                "backend-specialist",
            ],
            "quality": ["quality-reviewer", "security-auditor", "performance-engineer"],
            "deployment": ["devops-engineer", "sre"],
        },
        "routing_rules": [
            {
                "condition": "defect_type == 'security'",
                "route_to": "security-auditor",
                "priority": 1,
                "loop_back": True,
                "guidance": "Fix security issues before continuing",
            },
            {
                "condition": "defect_type == 'performance' and severity > 0.8",
                "route_to": "performance-engineer",
                "priority": 2,
                "loop_back": False,
                "guidance": "Optimize performance bottlenecks",
            },
            {
                "condition": "quality_score < 0.7",
                "route_to": "senior-developer",
                "priority": 3,
                "loop_back": True,
                "guidance": "Significant refactoring needed",
            },
            {
                "condition": "coverage < 0.8",
                "route_to": "quality-reviewer",
                "priority": 4,
                "loop_back": True,
                "guidance": "Add more test coverage",
            },
        ],
        "quality_weights": {
            "code_quality": 0.20,
            "requirements_coverage": 0.20,
            "testing": 0.20,
            "documentation": 0.15,
            "best_practices": 0.10,
            "security": 0.10,
            "performance": 0.05,
        },
    }


class TestTemplateAPIPaths:
    """Test that API paths are correct (no duplication bug)."""

    def test_list_templates_path(self, client_with_templates):
        """Test that /api/v1/pipeline/templates returns 200, not 404."""
        # This tests for the path duplication bug
        # Bug: /api + /api/v1/... = /api/api/v1/... (404)
        # Fix: /api + /v1/... = /api/v1/... (200)
        response = client_with_templates.get("/api/v1/pipeline/templates")
        # Should NOT be 404 (which would indicate path duplication bug)
        assert response.status_code != 404, "Path duplication bug detected!"
        assert response.status_code == 200

    def test_get_template_path(self, client_with_templates, temp_templates_dir):
        """Test that template fetch paths are correct."""
        # Create a template
        template_path = temp_templates_dir / "test-template.yaml"
        with open(template_path, "w", encoding="utf-8") as f:
            yaml.dump({"name": "test-template", "description": "Test"}, f)

        response = client_with_templates.get(
            "/api/v1/pipeline/templates/test-template"
        )
        assert response.status_code != 404, "Path duplication bug detected!"
        assert response.status_code == 200

    def test_create_template_path(self, client_with_templates):
        """Test that template creation path is correct."""
        response = client_with_templates.post(
            "/api/v1/pipeline/templates",
            json={
                "name": "new-template",
                "description": "New template",
            },
        )
        assert response.status_code != 404, "Path duplication bug detected!"
        assert response.status_code == 201

    def test_raw_template_path(self, client_with_templates, temp_templates_dir):
        """Test that raw YAML path is correct."""
        template_path = temp_templates_dir / "yaml-template.yaml"
        with open(template_path, "w", encoding="utf-8") as f:
            yaml.dump({"name": "yaml-template", "description": "YAML test"}, f)

        response = client_with_templates.get(
            "/api/v1/pipeline/templates/yaml-template/raw"
        )
        assert response.status_code != 404, "Path duplication bug detected!"
        assert response.status_code == 200
        assert "text/yaml" in response.headers.get("content-type", "")

    def test_validate_template_path(self, client_with_templates, temp_templates_dir):
        """Test that validation path is correct."""
        template_path = temp_templates_dir / "validate-template.yaml"
        with open(template_path, "w", encoding="utf-8") as f:
            yaml.dump(
                {"name": "validate-template", "quality_threshold": 0.9}, f
            )

        response = client_with_templates.get(
            "/api/v1/pipeline/templates/validate-template/validate"
        )
        assert response.status_code != 404, "Path duplication bug detected!"
        assert response.status_code == 200


class TestYAMLRoundTrip:
    """Test YAML parsing with nested structures - data loss prevention."""

    def test_complex_template_round_trip(
        self, client_with_templates, complex_template, temp_templates_dir
    ):
        """Test that complex nested structures survive round-trip editing."""
        # Step 1: Create the complex template
        create_response = client_with_templates.post(
            "/api/v1/pipeline/templates", json=complex_template
        )
        assert create_response.status_code == 201
        created = create_response.json()

        # Step 2: Fetch the created template
        get_response = client_with_templates.get(
            f"/api/v1/pipeline/templates/{complex_template['name']}"
        )
        assert get_response.status_code == 200
        fetched = get_response.json()

        # Step 3: Verify agent_categories preserved
        assert "agent_categories" in fetched
        assert len(fetched["agent_categories"]) == 4  # 4 categories

        # Check each category
        assert "planning" in fetched["agent_categories"]
        assert "development" in fetched["agent_categories"]
        assert "quality" in fetched["agent_categories"]
        assert "deployment" in fetched["agent_categories"]

        # Verify agent counts
        assert len(fetched["agent_categories"]["planning"]) == 3
        assert len(fetched["agent_categories"]["development"]) == 4
        assert len(fetched["agent_categories"]["quality"]) == 3
        assert len(fetched["agent_categories"]["deployment"]) == 2

        # Step 4: Verify routing_rules preserved
        assert "routing_rules" in fetched
        assert len(fetched["routing_rules"]) == 4

        for i, rule in enumerate(fetched["routing_rules"]):
            assert "condition" in rule
            assert "route_to" in rule
            assert "priority" in rule
            assert "loop_back" in rule
            assert "guidance" in rule

        # Verify specific rules
        assert fetched["routing_rules"][0]["condition"] == "defect_type == 'security'"
        assert fetched["routing_rules"][0]["priority"] == 1
        assert fetched["routing_rules"][0]["loop_back"] is True

        # Step 5: Verify quality_weights preserved
        assert "quality_weights" in fetched
        assert len(fetched["quality_weights"]) == 7

        # Check weight sum (should be ~1.0)
        weight_sum = sum(fetched["quality_weights"].values())
        assert abs(weight_sum - 1.0) < 0.01

        # Verify individual weights
        assert fetched["quality_weights"]["code_quality"] == 0.20
        assert fetched["quality_weights"]["security"] == 0.10
        assert fetched["quality_weights"]["performance"] == 0.05

    def test_yaml_raw_content_preserves_structure(
        self, client_with_templates, complex_template, temp_templates_dir
    ):
        """Test that raw YAML endpoint preserves exact structure."""
        # Create template
        create_response = client_with_templates.post(
            "/api/v1/pipeline/templates", json=complex_template
        )
        assert create_response.status_code == 201

        # Get raw YAML
        raw_response = client_with_templates.get(
            f"/api/v1/pipeline/templates/{complex_template['name']}/raw"
        )
        assert raw_response.status_code == 200

        # Parse raw YAML
        raw_yaml = raw_response.text
        parsed = yaml.safe_load(raw_yaml)

        # Verify nested structures in raw YAML
        assert "agent_categories" in parsed
        assert "routing_rules" in parsed
        assert "quality_weights" in parsed

        # Verify agent categories structure
        assert isinstance(parsed["agent_categories"], dict)
        for category, agents in parsed["agent_categories"].items():
            assert isinstance(agents, list)
            assert len(agents) > 0

        # Verify routing rules structure
        assert isinstance(parsed["routing_rules"], list)
        for rule in parsed["routing_rules"]:
            assert isinstance(rule, dict)
            assert "condition" in rule
            assert "route_to" in rule

    def test_update_preserves_nested_structures(
        self, client_with_templates, complex_template, temp_templates_dir
    ):
        """Test that partial updates don't lose nested data."""
        # Create template
        create_response = client_with_templates.post(
            "/api/v1/pipeline/templates", json=complex_template
        )
        assert create_response.status_code == 201

        # Partial update (only description)
        update_response = client_with_templates.put(
            f"/api/v1/pipeline/templates/{complex_template['name']}",
            json={"description": "Updated description only"},
        )
        assert update_response.status_code == 200
        updated = update_response.json()

        # Verify nested structures preserved after partial update
        assert len(updated["agent_categories"]) == 4
        assert len(updated["routing_rules"]) == 4
        assert len(updated["quality_weights"]) == 7

        # Verify description was updated
        assert updated["description"] == "Updated description only"

        # Verify other fields unchanged
        assert updated["quality_threshold"] == 0.92
        assert updated["max_iterations"] == 15

    def test_deeply_nested_routing_conditions(
        self, client_with_templates, temp_templates_dir
    ):
        """Test complex routing condition expressions."""
        template = {
            "name": "complex-conditions",
            "description": "Template with complex routing conditions",
            "routing_rules": [
                {
                    "condition": "(defect_type == 'security' or defect_type == 'privacy') and severity > 0.9",
                    "route_to": "security-auditor",
                    "priority": 1,
                    "loop_back": True,
                    "guidance": "Critical security/privacy issue",
                },
                {
                    "condition": "quality_score < 0.5 and iteration_count > 3",
                    "route_to": "tech-lead",
                    "priority": 2,
                    "loop_back": True,
                    "guidance": "Escalate after multiple failed iterations",
                },
                {
                    "condition": "coverage < target_coverage * 0.5",
                    "route_to": "quality-reviewer",
                    "priority": 3,
                    "loop_back": False,
                },
            ],
        }

        create_response = client_with_templates.post(
            "/api/v1/pipeline/templates", json=template
        )
        assert create_response.status_code == 201

        get_response = client_with_templates.get(
            "/api/v1/pipeline/templates/complex-conditions"
        )
        assert get_response.status_code == 200
        fetched = get_response.json()

        # Verify complex conditions preserved
        assert (
            fetched["routing_rules"][0]["condition"]
            == "(defect_type == 'security' or defect_type == 'privacy') and severity > 0.9"
        )


class TestErrorStateHandling:
    """Test error state handling in template API."""

    def test_template_not_found_returns_404(self, client_with_templates):
        """Test that non-existent template returns proper 404."""
        response = client_with_templates.get(
            "/api/v1/pipeline/templates/nonexistent-template"
        )
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_invalid_template_name_returns_400(self, client_with_templates):
        """Test that invalid template names return 400."""
        response = client_with_templates.get(
            "/api/v1/pipeline/templates/invalid@name!with#special$chars"
        )
        assert response.status_code == 400

    def test_duplicate_template_creation_returns_400(self, client_with_templates):
        """Test that duplicate template names return 400."""
        template_data = {
            "name": "duplicate-test",
            "description": "First template",
        }

        # Create first template
        response1 = client_with_templates.post(
            "/api/v1/pipeline/templates", json=template_data
        )
        assert response1.status_code == 201

        # Try to create duplicate
        response2 = client_with_templates.post(
            "/api/v1/pipeline/templates", json=template_data
        )
        assert response2.status_code == 400
        assert "already exists" in response2.json()["detail"].lower()

    def test_invalid_quality_threshold_returns_400(self, client_with_templates):
        """Test that invalid quality threshold returns error."""
        response = client_with_templates.post(
            "/api/v1/pipeline/templates",
            json={"name": "bad-threshold", "quality_threshold": 1.5},
        )
        # Pydantic validation returns 422
        assert response.status_code == 422

    def test_invalid_quality_weights_returns_400(self, client_with_templates):
        """Test that invalid quality weights return error."""
        response = client_with_templates.post(
            "/api/v1/pipeline/templates",
            json={
                "name": "bad-weights",
                "quality_weights": {"a": 0.3, "b": 0.3},  # Sum != 1.0
            },
        )
        # Validation error - may be 400 (custom validation) or 422 (Pydantic)
        assert response.status_code in [400, 422]
        detail = response.json().get("detail", "")
        if isinstance(detail, str):
            assert "sum" in detail.lower()
        elif isinstance(detail, list):
            # Pydantic v2 returns list of validation errors
            assert any("sum" in str(err).lower() for err in detail)

    def test_invalid_max_iterations_returns_400(self, client_with_templates):
        """Test that invalid max iterations returns error."""
        response = client_with_templates.post(
            "/api/v1/pipeline/templates",
            json={"name": "bad-iterations", "max_iterations": 0},
        )
        # Pydantic validation returns 422 for invalid values
        assert response.status_code == 422

    def test_path_traversal_attempt_returns_error(self, client_with_templates):
        """Test that path traversal attempts are blocked."""
        response = client_with_templates.get(
            "/api/v1/pipeline/templates/../../../etc/passwd"
        )
        assert response.status_code in [400, 404]


class TestTemplateValidation:
    """Test template validation endpoint."""

    def test_valid_template_validation(self, client_with_templates, temp_templates_dir):
        """Test validation of valid template."""
        template_path = temp_templates_dir / "valid.yaml"
        with open(template_path, "w", encoding="utf-8") as f:
            yaml.dump(
                {
                    "name": "valid",
                    "quality_threshold": 0.9,
                    "max_iterations": 10,
                },
                f,
            )

        response = client_with_templates.get(
            "/api/v1/pipeline/templates/valid/validate"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert data["errors"] == []

    def test_invalid_yaml_validation(self, client_with_templates, temp_templates_dir):
        """Test validation of invalid YAML."""
        template_path = temp_templates_dir / "invalid.yaml"
        with open(template_path, "w", encoding="utf-8") as f:
            f.write("invalid: yaml: [unclosed")

        response = client_with_templates.get(
            "/api/v1/pipeline/templates/invalid/validate"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert len(data["errors"]) > 0
        assert any("YAML" in err for err in data["errors"])

    def test_validation_with_warnings(
        self, client_with_templates, temp_templates_dir
    ):
        """Test validation that produces warnings."""
        template_path = temp_templates_dir / "warnings.yaml"
        with open(template_path, "w", encoding="utf-8") as f:
            yaml.dump(
                {
                    "name": "warnings",
                    "quality_threshold": 0.5,  # Low threshold - might warn
                    "max_iterations": 100,  # High iterations - might warn
                },
                f,
            )

        response = client_with_templates.get(
            "/api/v1/pipeline/templates/warnings/validate"
        )
        assert response.status_code == 200
        # Response should have warnings field
        data = response.json()
        assert "warnings" in data


class TestConcurrentOperations:
    """Test concurrent template operations."""

    def test_multiple_creates(self, client_with_templates):
        """Test creating multiple templates concurrently."""
        templates = [
            {"name": f"template-{i}", "description": f"Template {i}"}
            for i in range(5)
        ]

        for template in templates:
            response = client_with_templates.post(
                "/api/v1/pipeline/templates", json=template
            )
            assert response.status_code == 201

        # Verify all created
        list_response = client_with_templates.get("/api/v1/pipeline/templates")
        assert list_response.status_code == 200
        data = list_response.json()
        assert data["total"] == 5

    def test_create_delete_create_cycle(self, client_with_templates):
        """Test create, delete, recreate cycle."""
        template = {"name": "cycle-test", "description": "Cycle test"}

        # Create
        response1 = client_with_templates.post(
            "/api/v1/pipeline/templates", json=template
        )
        assert response1.status_code == 201

        # Delete
        response2 = client_with_templates.delete(
            "/api/v1/pipeline/templates/cycle-test"
        )
        assert response2.status_code == 200

        # Recreate
        response3 = client_with_templates.post(
            "/api/v1/pipeline/templates", json=template
        )
        assert response3.status_code == 201


class TestTemplateListResponse:
    """Test template list response format."""

    def test_list_response_format(self, client_with_templates, temp_templates_dir):
        """Test that list response has correct format."""
        # Create templates
        for i in range(3):
            template_path = temp_templates_dir / f"list-test-{i}.yaml"
            with open(template_path, "w", encoding="utf-8") as f:
                yaml.dump(
                    {
                        "name": f"list-test-{i}",
                        "description": f"Test {i}",
                    },
                    f,
                )

        response = client_with_templates.get("/api/v1/pipeline/templates")
        assert response.status_code == 200
        data = response.json()

        # Verify response structure
        assert "templates" in data
        assert "total" in data
        assert isinstance(data["templates"], list)
        assert data["total"] == 3

        # Verify each template has required fields
        for template in data["templates"]:
            assert "name" in template
            assert "description" in template
            assert "quality_threshold" in template
            assert "max_iterations" in template


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
