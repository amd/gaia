# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for pipeline template service."""

import tempfile
from pathlib import Path

import pytest
import yaml

from gaia.ui.schemas.pipeline_templates import PipelineTemplateSchema
from gaia.ui.services.template_service import (
    TemplateService,
    TemplateValidationError,
)


@pytest.fixture
def temp_templates_dir():
    """Create a temporary directory for template files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def template_service(temp_templates_dir):
    """Create a template service with temporary directory."""
    return TemplateService(templates_dir=temp_templates_dir)


@pytest.fixture
def sample_template_data():
    """Sample template data for testing."""
    return {
        "name": "test-template",
        "description": "Test template for unit tests",
        "quality_threshold": 0.85,
        "max_iterations": 8,
        "agent_categories": {
            "planning": ["planning-agent"],
            "development": ["dev-agent"],
            "quality": ["quality-agent"],
        },
        "routing_rules": [
            {
                "condition": "defect_type == 'security'",
                "route_to": "security-auditor",
                "priority": 1,
                "loop_back": True,
            }
        ],
        "quality_weights": {
            "code_quality": 0.30,
            "requirements_coverage": 0.25,
            "testing": 0.25,
            "documentation": 0.10,
            "best_practices": 0.10,
        },
    }


@pytest.fixture
def created_template_file(temp_templates_dir, sample_template_data):
    """Create a sample template file for testing."""
    template_path = temp_templates_dir / "test-template.yaml"
    with open(template_path, "w", encoding="utf-8") as f:
        yaml.dump(sample_template_data, f)
    return template_path


class TestTemplateServiceInit:
    """Test TemplateService initialization."""

    def test_init_with_default_dir(self):
        """Test initialization with default directory."""
        service = TemplateService()
        assert service.templates_dir is not None
        assert service.templates_dir.exists()

    def test_init_with_custom_dir(self, temp_templates_dir):
        """Test initialization with custom directory."""
        service = TemplateService(templates_dir=temp_templates_dir)
        assert service.templates_dir == temp_templates_dir


class TestSanitizeTemplateName:
    """Test template name sanitization."""

    def test_valid_name_alphanumeric(self, template_service):
        """Test valid alphanumeric name."""
        result = template_service._sanitize_template_name("test123")
        assert result == "test123"

    def test_valid_name_with_underscore(self, template_service):
        """Test valid name with underscore."""
        result = template_service._sanitize_template_name("test_template")
        assert result == "test_template"

    def test_valid_name_with_hyphen(self, template_service):
        """Test valid name with hyphen."""
        result = template_service._sanitize_template_name("test-template")
        assert result == "test-template"

    def test_empty_name_raises(self, template_service):
        """Test empty name raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            template_service._sanitize_template_name("")

    def test_invalid_characters_raises(self, template_service):
        """Test invalid characters raise ValueError."""
        with pytest.raises(ValueError, match="only contain letters"):
            template_service._sanitize_template_name("test@template")

    def test_path_traversal_raises(self, template_service):
        """Test path traversal attempt raises ValueError."""
        with pytest.raises(ValueError, match="can only contain letters"):
            template_service._sanitize_template_name("../etc/passwd")

    def test_slash_in_name_raises(self, template_service):
        """Test slash in name raises ValueError."""
        with pytest.raises(ValueError, match="can only contain letters"):
            template_service._sanitize_template_name("test/template")


class TestListTemplates:
    """Test list_templates method."""

    def test_list_empty_directory(self, template_service):
        """Test listing from empty directory."""
        templates = template_service.list_templates()
        assert templates == []

    def test_list_with_templates(self, template_service, sample_template_data):
        """Test listing multiple templates."""
        # Create multiple template files
        for name in ["template1", "template2", "template3"]:
            data = sample_template_data.copy()
            data["name"] = name
            template_path = template_service.templates_dir / f"{name}.yaml"
            with open(template_path, "w", encoding="utf-8") as f:
                yaml.dump(data, f)

        templates = template_service.list_templates()
        assert len(templates) == 3
        names = [t.name for t in templates]
        assert "template1" in names
        assert "template2" in names
        assert "template3" in names

    def test_list_skips_invalid_yaml(self, template_service):
        """Test that invalid YAML files are skipped."""
        invalid_path = template_service.templates_dir / "invalid.yaml"
        invalid_path.write_text("invalid: yaml: content: [")

        templates = template_service.list_templates()
        assert len(templates) == 0


class TestGetTemplate:
    """Test get_template method."""

    def test_get_existing_template(self, template_service, created_template_file):
        """Test getting an existing template."""
        template = template_service.get_template("test-template")
        assert template.name == "test-template"
        assert template.description == "Test template for unit tests"
        assert template.quality_threshold == 0.85
        assert template.max_iterations == 8

    def test_get_nonexistent_template_raises(self, template_service):
        """Test getting non-existent template raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="not found"):
            template_service.get_template("nonexistent")

    def test_get_template_raw(self, template_service, created_template_file):
        """Test getting raw YAML content."""
        raw = template_service.get_template_raw("test-template")
        assert isinstance(raw, str)
        assert "test-template" in raw
        assert "Test template for unit tests" in raw


class TestCreateTemplate:
    """Test create_template method."""

    def test_create_template_success(self, template_service, sample_template_data):
        """Test creating a template successfully."""
        schema = template_service.create_template(
            name="new-template",
            description="New test template",
            quality_threshold=0.90,
            max_iterations=10,
            agent_categories=sample_template_data["agent_categories"],
            routing_rules=sample_template_data["routing_rules"],
            quality_weights=sample_template_data["quality_weights"],
        )
        assert schema.name == "new-template"
        assert schema.description == "New test template"

        # Verify file was created
        template_path = template_service.templates_dir / "new-template.yaml"
        assert template_path.exists()

    def test_create_template_duplicate_raises(
        self, template_service, created_template_file
    ):
        """Test creating duplicate template raises ValueError."""
        with pytest.raises(ValueError, match="already exists"):
            template_service.create_template(
                name="test-template",
                description="Duplicate template",
            )

    def test_create_template_invalid_weights(self, template_service):
        """Test creating template with invalid weights."""
        with pytest.raises(TemplateValidationError, match="sum to"):
            template_service.create_template(
                name="bad-weights",
                quality_weights={"code_quality": 0.5, "testing": 0.3},  # Sum != 1.0
            )

    def test_create_template_invalid_threshold(self, template_service):
        """Test creating template with invalid quality threshold."""
        with pytest.raises(TemplateValidationError):
            template_service.create_template(
                name="bad-threshold",
                quality_threshold=1.5,  # Must be 0-1
            )


class TestUpdateTemplate:
    """Test update_template method."""

    def test_update_template_success(self, template_service, created_template_file):
        """Test updating a template successfully."""
        schema = template_service.update_template(
            name="test-template",
            description="Updated description",
            quality_threshold=0.95,
        )
        assert schema.description == "Updated description"
        assert schema.quality_threshold == 0.95
        # Verify other fields unchanged
        assert schema.max_iterations == 8

    def test_update_nonexistent_template_raises(self, template_service):
        """Test updating non-existent template raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="not found"):
            template_service.update_template(
                name="nonexistent",
                description="New description",
            )


class TestDeleteTemplate:
    """Test delete_template method."""

    def test_delete_template_success(self, template_service, created_template_file):
        """Test deleting a template successfully."""
        result = template_service.delete_template("test-template")
        assert result is True
        assert not created_template_file.exists()

    def test_delete_nonexistent_template(self, template_service):
        """Test deleting non-existent template returns False."""
        result = template_service.delete_template("nonexistent")
        assert result is False


class TestValidateTemplate:
    """Test validate_template method."""

    def test_validate_valid_template(self, template_service, created_template_file):
        """Test validating a valid template."""
        is_valid, errors, warnings = template_service.validate_template("test-template")
        assert is_valid is True
        assert errors == []

    def test_validate_nonexistent_template_raises(self, template_service):
        """Test validating non-existent template raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="not found"):
            template_service.validate_template("nonexistent")

    def test_validate_invalid_yaml(self, template_service):
        """Test validating invalid YAML."""
        invalid_path = template_service.templates_dir / "invalid.yaml"
        invalid_path.write_text("invalid: yaml: content: [")

        is_valid, errors, warnings = template_service.validate_template("invalid")
        assert is_valid is False
        assert any("YAML" in err for err in errors)

    def test_validate_missing_required_fields(self, template_service):
        """Test template with minimal fields still validates (uses defaults)."""
        minimal_path = template_service.templates_dir / "minimal.yaml"
        minimal_path.write_text("name: minimal\n")

        is_valid, errors, warnings = template_service.validate_template("minimal")
        # Template with just name is valid - uses defaults for other fields
        assert is_valid is True
        # No errors or warnings for minimal valid template
        assert errors == []

    def test_validate_invalid_threshold(self, template_service):
        """Test template with invalid threshold."""
        bad_path = template_service.templates_dir / "bad-threshold.yaml"
        bad_path.write_text("name: bad\nquality_threshold: 2.0\n")

        is_valid, errors, warnings = template_service.validate_template("bad-threshold")
        assert is_valid is False
        assert any("quality_threshold" in err for err in errors)

    def test_validate_invalid_weights_sum(self, template_service):
        """Test template with weights not summing to 1."""
        bad_path = template_service.templates_dir / "bad-weights.yaml"
        bad_path.write_text(
            "name: bad\nquality_weights:\n  code_quality: 0.5\n  testing: 0.3\n"
        )

        is_valid, errors, warnings = template_service.validate_template("bad-weights")
        assert is_valid is False
        assert any("sum to" in err for err in errors)

    def test_validate_invalid_routing_rules(self, template_service):
        """Test template with invalid routing rules."""
        bad_path = template_service.templates_dir / "bad-rules.yaml"
        bad_path.write_text("name: bad\nrouting_rules:\n  - invalid: structure\n")

        is_valid, errors, warnings = template_service.validate_template("bad-rules")
        assert is_valid is False
        assert any("condition" in err or "route_to" in err for err in errors)


class TestTemplateSchema:
    """Test PipelineTemplateSchema validation."""

    def test_schema_valid_template(self, sample_template_data):
        """Test schema with valid template data."""
        schema = PipelineTemplateSchema(**sample_template_data)
        assert schema.name == "test-template"
        assert len(schema.routing_rules) == 1

    def test_schema_invalid_threshold(self):
        """Test schema rejects invalid threshold."""
        with pytest.raises(ValueError, match="quality_threshold"):
            PipelineTemplateSchema(
                name="test",
                quality_threshold=1.5,
            )

    def test_schema_invalid_iterations(self):
        """Test schema rejects invalid max_iterations."""
        with pytest.raises(ValueError, match="max_iterations"):
            PipelineTemplateSchema(
                name="test",
                max_iterations=0,
            )

    def test_schema_invalid_weights(self):
        """Test schema rejects weights not summing to 1."""
        with pytest.raises(ValueError, match="sum to"):
            PipelineTemplateSchema(
                name="test",
                quality_weights={"a": 0.3, "b": 0.3},
            )
