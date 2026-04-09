# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for ComponentLoader."""

import pytest
import tempfile
import os
from pathlib import Path
from gaia.utils.component_loader import ComponentLoader, ComponentLoaderError


class TestComponentLoaderInit:
    """Test ComponentLoader initialization."""

    def test_init_default_framework_dir(self):
        """Test initialization with default framework directory."""
        loader = ComponentLoader()
        assert loader.framework_dir == Path("component-framework")

    def test_init_custom_framework_dir(self, tmp_path):
        """Test initialization with custom framework directory."""
        custom_dir = tmp_path / "custom-framework"
        custom_dir.mkdir()
        loader = ComponentLoader(framework_dir=custom_dir)
        assert loader.framework_dir == custom_dir

    def test_init_creates_empty_cache(self):
        """Test that initialization creates empty component cache."""
        loader = ComponentLoader()
        assert loader._loaded_components == {}


class TestLoadComponent:
    """Test load_component method."""

    def test_load_component_success(self, tmp_path):
        """Test loading a valid component."""
        content = "---\n"
        content += "template_id: test-component\n"
        content += "template_type: memory\n"
        content += "version: 1.0.0\n"
        content += "description: Test component\n"
        content += "---\n\n"
        content += "# Test Component\n\n"
        content += "This is test content.\n"

        component_file = tmp_path / "test-component.md"
        component_file.write_text(content)

        loader = ComponentLoader(framework_dir=tmp_path)
        result = loader.load_component("test-component.md")

        assert result["path"] == "test-component.md"
        assert result["frontmatter"]["template_id"] == "test-component"
        assert result["frontmatter"]["template_type"] == "memory"
        assert result["frontmatter"]["version"] == "1.0.0"
        assert "Test Component" in result["content"]

    def test_load_component_missing(self, tmp_path):
        """Test loading non-existent component raises ComponentLoaderError."""
        loader = ComponentLoader(framework_dir=tmp_path)

        with pytest.raises(ComponentLoaderError) as exc_info:
            loader.load_component("nonexistent.md")

        assert "Component not found" in str(exc_info.value)
        assert "nonexistent.md" in str(exc_info.value)

    def test_load_component_no_frontmatter(self, tmp_path):
        """Test loading component without frontmatter raises ComponentLoaderError."""
        component_file = tmp_path / "no-frontmatter.md"
        component_file.write_text("# No Frontmatter\n\nJust content.")

        loader = ComponentLoader(framework_dir=tmp_path)

        with pytest.raises(ComponentLoaderError) as exc_info:
            loader.load_component("no-frontmatter.md")

        assert "Missing frontmatter delimiter" in str(exc_info.value)

    def test_load_component_invalid_yaml(self, tmp_path):
        """Test loading component with invalid YAML raises ComponentLoaderError."""
        content = "---\n"
        content += "template_id: test\n"
        content += "template_type: memory\n"
        content += "  invalid indentation: [\n"
        content += "---\n\nContent.\n"

        component_file = tmp_path / "invalid-yaml.md"
        component_file.write_text(content)

        loader = ComponentLoader(framework_dir=tmp_path)

        with pytest.raises(ComponentLoaderError) as exc_info:
            loader.load_component("invalid-yaml.md")

        assert "Invalid YAML" in str(exc_info.value) or "Frontmatter must be a YAML dictionary" in str(exc_info.value)

    def test_load_component_with_subdirectory(self, tmp_path):
        """Test loading component from subdirectory."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        content = "---\n"
        content += "template_id: working-memory\n"
        content += "template_type: memory\n"
        content += "version: 1.0.0\n"
        content += "description: Working memory template\n"
        content += "---\n\n"
        content += "# Working Memory\n\nContent here.\n"

        component_file = memory_dir / "working-memory.md"
        component_file.write_text(content)

        loader = ComponentLoader(framework_dir=tmp_path)
        result = loader.load_component("memory/working-memory.md")

        assert result["frontmatter"]["template_id"] == "working-memory"

    def test_load_component_caches_result(self, tmp_path):
        """Test that loaded components are cached."""
        content = "---\n"
        content += "template_id: cached\n"
        content += "template_type: memory\n"
        content += "version: 1.0.0\n"
        content += "description: Cached component\n"
        content += "---\n\nContent.\n"

        component_file = tmp_path / "cached-component.md"
        component_file.write_text(content)

        loader = ComponentLoader(framework_dir=tmp_path)

        result1 = loader.load_component("cached-component.md")
        result2 = loader.load_component("cached-component.md")

        assert result1 is result2
        assert len(loader._loaded_components) == 1

    def test_load_component_utf8_encoding(self, tmp_path):
        """Test loading component with UTF-8 content."""
        content = "---\n"
        content += "template_id: utf8-component\n"
        content += "template_type: documents\n"
        content += "version: 1.0.0\n"
        content += "description: UTF-8 test component\n"
        content += "---\n\n"
        content += "# UTF-8 Content\n\n"
        content += "Special characters: cafe, naive, resume\n"

        component_file = tmp_path / "utf8-component.md"
        component_file.write_text(content)

        loader = ComponentLoader(framework_dir=tmp_path)
        result = loader.load_component("utf8-component.md")

        assert "cafe" in result["content"]
        assert "UTF-8" in result["content"]

    def test_load_component_path_is_directory(self, tmp_path):
        """Test loading when path is a directory raises ComponentLoaderError."""
        dir_path = tmp_path / "directory"
        dir_path.mkdir()

        loader = ComponentLoader(framework_dir=tmp_path)

        with pytest.raises(ComponentLoaderError) as exc_info:
            loader.load_component("directory")

        assert "not a file" in str(exc_info.value)


class TestSaveComponent:
    """Test save_component method."""

    def test_save_component_success(self, tmp_path):
        """Test saving a component with frontmatter."""
        loader = ComponentLoader(framework_dir=tmp_path)
        content = "# Test Component\n\nThis is test content."
        frontmatter = {
            "template_id": "saved-component",
            "template_type": "tasks",
            "version": "1.0.0",
            "description": "Test saved component"
        }

        result_path = loader.save_component(
            "saved-component.md",
            content,
            frontmatter
        )

        assert result_path.endswith("saved-component.md")
        assert Path(result_path).exists()

        # Verify content
        saved_content = Path(result_path).read_text()
        assert "template_id: saved-component" in saved_content
        assert "template_type: tasks" in saved_content
        assert "# Test Component" in saved_content

    def test_save_component_creates_directories(self, tmp_path):
        """Test that save_component creates parent directories."""
        loader = ComponentLoader(framework_dir=tmp_path)
        content = "# Nested Component"
        frontmatter = {
            "template_id": "nested-component",
            "template_type": "memory",
            "version": "1.0.0",
            "description": "Nested test"
        }

        result_path = loader.save_component(
            "subdir/nested/component.md",
            content,
            frontmatter
        )

        assert Path(result_path).exists()
        # Use Path for cross-platform comparison
        assert Path(result_path).name == "component.md"
        assert "subdir" in str(result_path)
        assert "nested" in str(result_path)

    def test_save_component_without_frontmatter(self, tmp_path):
        """Test saving component without frontmatter."""
        loader = ComponentLoader(framework_dir=tmp_path)
        content = "# No Frontmatter\n\nJust content."

        result_path = loader.save_component(
            "no-frontmatter.md",
            content
        )

        assert Path(result_path).exists()
        saved_content = Path(result_path).read_text()
        assert "# No Frontmatter" in saved_content

    def test_save_component_missing_required_field(self, tmp_path):
        """Test saving component with missing required frontmatter field."""
        loader = ComponentLoader(framework_dir=tmp_path)
        content = "# Test"
        frontmatter = {
            "template_id": "incomplete-component",
            # Missing template_type, version, description
        }

        with pytest.raises(ComponentLoaderError) as exc_info:
            loader.save_component(
                "incomplete.md",
                content,
                frontmatter
            )

        assert "Missing required frontmatter field" in str(exc_info.value)

    def test_save_component_overwrites_existing(self, tmp_path):
        """Test that save_component overwrites existing file."""
        loader = ComponentLoader(framework_dir=tmp_path)

        # First save
        loader.save_component(
            "overwrite-test.md",
            "# Version 1",
            {"template_id": "overwrite", "template_type": "tasks", "version": "1.0.0", "description": "Test"}
        )

        # Overwrite
        loader.save_component(
            "overwrite-test.md",
            "# Version 2",
            {"template_id": "overwrite", "template_type": "tasks", "version": "2.0.0", "description": "Updated"}
        )

        # Verify overwrite
        saved_content = Path(tmp_path / "overwrite-test.md").read_text()
        assert "# Version 2" in saved_content
        assert "version: 2.0.0" in saved_content


class TestRenderComponent:
    """Test render_component method."""

    def test_render_component_variables(self, tmp_path):
        """Test variable substitution in template."""
        content = "---\n"
        content += "template_id: render-test\n"
        content += "template_type: tasks\n"
        content += "version: 1.0.0\n"
        content += "description: Render test\n"
        content += "---\n\n"
        content += "# Task: {{TASK_NAME}}\n\n"
        content += "**Owner:** {{OWNER}}\n\n"
        content += "**Date:** {{DATE}}\n"

        component_file = tmp_path / "render-test.md"
        component_file.write_text(content)

        loader = ComponentLoader(framework_dir=tmp_path)
        rendered = loader.render_component(
            "render-test.md",
            {
                "TASK_NAME": "Implementation",
                "OWNER": "John Doe",
                "DATE": "2026-04-07"
            }
        )

        assert "Task: Implementation" in rendered
        assert "**Owner:** John Doe" in rendered
        assert "**Date:** 2026-04-07" in rendered
        assert "{{" not in rendered

    def test_render_component_partial_variables(self, tmp_path):
        """Test rendering with only some variables provided."""
        content = "---\n"
        content += "template_id: partial\n"
        content += "template_type: tasks\n"
        content += "version: 1.0.0\n"
        content += "description: Partial render test\n"
        content += "---\n\n"
        content += "# {{TASK_NAME}}\n\n"
        content += "**Owner:** {{OWNER}}\n\n"
        content += "**Status:** {{STATUS}}\n"

        component_file = tmp_path / "partial-render.md"
        component_file.write_text(content)

        loader = ComponentLoader(framework_dir=tmp_path)
        rendered = loader.render_component(
            "partial-render.md",
            {"TASK_NAME": "Test Task"}
        )

        assert "Test Task" in rendered
        assert "{{OWNER}}" in rendered
        assert "{{STATUS}}" in rendered

    def test_render_component_with_braces_in_variables(self, tmp_path):
        """Test rendering with variables that include braces."""
        content = "---\n"
        content += "template_id: braces-test\n"
        content += "template_type: memory\n"
        content += "version: 1.0.0\n"
        content += "description: Braces test\n"
        content += "---\n\n"
        content += "Agent: {{AGENT_ID}}\n"

        component_file = tmp_path / "braces-test.md"
        component_file.write_text(content)

        loader = ComponentLoader(framework_dir=tmp_path)
        rendered = loader.render_component(
            "braces-test.md",
            {"{{AGENT_ID}}": "test-agent-1"}
        )

        assert "Agent: test-agent-1" in rendered

    def test_render_component_missing_file(self, tmp_path):
        """Test rendering non-existent component raises ComponentLoaderError."""
        loader = ComponentLoader(framework_dir=tmp_path)

        with pytest.raises(ComponentLoaderError):
            loader.render_component("nonexistent.md", {})

    def test_render_component_non_string_values(self, tmp_path):
        """Test rendering with non-string variable values."""
        content = "---\n"
        content += "template_id: non-string\n"
        content += "template_type: tasks\n"
        content += "version: 1.0.0\n"
        content += "description: Non-string test\n"
        content += "---\n\n"
        content += "Count: {{COUNT}}\n"
        content += "Score: {{SCORE}}\n"

        component_file = tmp_path / "non-string.md"
        component_file.write_text(content)

        loader = ComponentLoader(framework_dir=tmp_path)
        rendered = loader.render_component(
            "non-string.md",
            {"COUNT": 42, "SCORE": 3.14}
        )

        assert "Count: 42" in rendered
        assert "Score: 3.14" in rendered


class TestListComponents:
    """Test list_components method."""

    def test_list_components_empty_directory(self, tmp_path):
        """Test listing components in empty directory."""
        loader = ComponentLoader(framework_dir=tmp_path)
        components = loader.list_components()
        assert components == []

    def test_list_components_all_components(self, tmp_path):
        """Test listing all components."""
        for name in ["comp1.md", "comp2.md", "comp3.md"]:
            content = "---\n"
            content += "template_id: test\n"
            content += "template_type: memory\n"
            content += "version: 1.0.0\n"
            content += "description: Test\n"
            content += "---\n\nContent.\n"

            component_file = tmp_path / name
            component_file.write_text(content)

        loader = ComponentLoader(framework_dir=tmp_path)
        components = loader.list_components()

        assert len(components) == 3
        assert components == ["comp1.md", "comp2.md", "comp3.md"]

    def test_list_components_by_type(self, tmp_path):
        """Test listing components filtered by type."""
        for dir_name in ["memory", "knowledge", "tasks"]:
            dir_path = tmp_path / dir_name
            dir_path.mkdir()

            content = "---\n"
            content += f"template_id: {dir_name}-test\n"
            content += f"template_type: {dir_name}\n"
            content += "version: 1.0.0\n"
            content += "description: Test\n"
            content += "---\n\nContent.\n"

            component_file = dir_path / f"{dir_name}-template.md"
            component_file.write_text(content)

        loader = ComponentLoader(framework_dir=tmp_path)

        all_components = loader.list_components()
        assert len(all_components) == 3

        memory_components = loader.list_components("memory")
        assert memory_components == ["memory/memory-template.md"]

        knowledge_components = loader.list_components("knowledge")
        assert knowledge_components == ["knowledge/knowledge-template.md"]

    def test_list_components_invalid_type(self, tmp_path):
        """Test listing with invalid component type raises ComponentLoaderError."""
        loader = ComponentLoader(framework_dir=tmp_path)

        with pytest.raises(ComponentLoaderError) as exc_info:
            loader.list_components("invalid_type")

        assert "Invalid component_type" in str(exc_info.value)
        assert "invalid_type" in str(exc_info.value)

    def test_list_components_nonexistent_framework_dir(self, tmp_path):
        """Test listing when framework directory doesn't exist."""
        nonexistent = tmp_path / "nonexistent"
        loader = ComponentLoader(framework_dir=nonexistent)
        components = loader.list_components()
        assert components == []

    def test_list_components_nested_directories(self, tmp_path):
        """Test listing components in nested directories."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        sub_dir = memory_dir / "subdir"
        sub_dir.mkdir()

        root_content = "---\n"
        root_content += "template_id: root\n"
        root_content += "template_type: memory\n"
        root_content += "version: 1.0.0\n"
        root_content += "description: Root\n"
        root_content += "---\n\nContent"
        (memory_dir / "root.md").write_text(root_content)

        nested_content = "---\n"
        nested_content += "template_id: nested\n"
        nested_content += "template_type: memory\n"
        nested_content += "version: 1.0.0\n"
        nested_content += "description: Nested\n"
        nested_content += "---\n\nContent"
        (sub_dir / "nested.md").write_text(nested_content)

        loader = ComponentLoader(framework_dir=tmp_path)
        components = loader.list_components("memory")

        assert len(components) == 2
        assert "memory/root.md" in components
        assert "memory/subdir/nested.md" in components


class TestValidateComponent:
    """Test validate_component method."""

    def test_validate_component_valid(self, tmp_path):
        """Test validation of valid component."""
        content = "---\n"
        content += "template_id: valid-component\n"
        content += "template_type: memory\n"
        content += "version: 1.0.0\n"
        content += "description: A valid component\n"
        content += "---\n\n"
        content += "# Valid Component\n\nSome content here.\n"

        component_file = tmp_path / "valid.md"
        component_file.write_text(content)

        loader = ComponentLoader(framework_dir=tmp_path)
        errors = loader.validate_component("valid.md")

        assert errors == []

    def test_validate_component_missing_fields(self, tmp_path):
        """Test validation catches missing required fields."""
        content = "---\n"
        content += "template_id: incomplete\n"
        content += "---\n\nContent.\n"

        component_file = tmp_path / "missing-fields.md"
        component_file.write_text(content)

        loader = ComponentLoader(framework_dir=tmp_path)
        errors = loader.validate_component("missing-fields.md")

        assert len(errors) >= 3
        assert any("template_type" in e for e in errors)
        assert any("version" in e for e in errors)
        assert any("description" in e for e in errors)

    def test_validate_component_invalid_type(self, tmp_path):
        """Test validation catches invalid template_type."""
        content = "---\n"
        content += "template_id: test\n"
        content += "template_type: invalid_type\n"
        content += "version: 1.0.0\n"
        content += "description: Test\n"
        content += "---\n\nContent.\n"

        component_file = tmp_path / "invalid-type.md"
        component_file.write_text(content)

        loader = ComponentLoader(framework_dir=tmp_path)
        errors = loader.validate_component("invalid-type.md")

        assert any("Invalid template_type" in e for e in errors)

    def test_validate_component_invalid_version(self, tmp_path):
        """Test validation catches invalid version format."""
        content = "---\n"
        content += "template_id: test\n"
        content += "template_type: memory\n"
        content += "version: invalid\n"
        content += "description: Test\n"
        content += "---\n\nContent.\n"

        component_file = tmp_path / "invalid-version.md"
        component_file.write_text(content)

        loader = ComponentLoader(framework_dir=tmp_path)
        errors = loader.validate_component("invalid-version.md")

        assert any("semver" in e for e in errors)

    def test_validate_component_invalid_template_id(self, tmp_path):
        """Test validation catches invalid template_id format."""
        content = "---\n"
        content += "template_id: Invalid_ID_With_Uppercase\n"
        content += "template_type: memory\n"
        content += "version: 1.0.0\n"
        content += "description: Test\n"
        content += "---\n\nContent.\n"

        component_file = tmp_path / "invalid-id.md"
        component_file.write_text(content)

        loader = ComponentLoader(framework_dir=tmp_path)
        errors = loader.validate_component("invalid-id.md")

        assert any("template_id" in e for e in errors)

    def test_validate_component_empty_content(self, tmp_path):
        """Test validation catches empty content."""
        content = "---\n"
        content += "template_id: empty\n"
        content += "template_type: memory\n"
        content += "version: 1.0.0\n"
        content += "description: Empty content test\n"
        content += "---\n\n"

        component_file = tmp_path / "empty-content.md"
        component_file.write_text(content)

        loader = ComponentLoader(framework_dir=tmp_path)
        errors = loader.validate_component("empty-content.md")

        assert any("empty" in e.lower() for e in errors)

    def test_validate_component_missing_file(self, tmp_path):
        """Test validation of non-existent component."""
        loader = ComponentLoader(framework_dir=tmp_path)
        errors = loader.validate_component("nonexistent.md")

        assert len(errors) == 1
        assert "Component not found" in errors[0]

    def test_validate_component_empty_template_id(self, tmp_path):
        """Test validation catches empty template_id."""
        content = "---\n"
        content += "template_id: \"\"\n"
        content += "template_type: memory\n"
        content += "version: 1.0.0\n"
        content += "description: Test\n"
        content += "---\n\nContent.\n"

        component_file = tmp_path / "empty-id.md"
        component_file.write_text(content)

        loader = ComponentLoader(framework_dir=tmp_path)
        errors = loader.validate_component("empty-id.md")

        assert any("empty" in e.lower() for e in errors)


class TestGetComponentMetadata:
    """Test get_component_metadata method."""

    def test_get_component_metadata_success(self, tmp_path):
        """Test getting metadata from valid component."""
        content = "---\n"
        content += "template_id: metadata-test\n"
        content += "template_type: knowledge\n"
        content += "version: 2.0.0\n"
        content += "description: Test description\n"
        content += "---\n\n"
        content += "# Content\n\nThis is content.\n"

        component_file = tmp_path / "metadata-test.md"
        component_file.write_text(content)

        loader = ComponentLoader(framework_dir=tmp_path)
        metadata = loader.get_component_metadata("metadata-test.md")

        assert metadata["template_id"] == "metadata-test"
        assert metadata["template_type"] == "knowledge"
        assert metadata["version"] == "2.0.0"
        assert metadata["description"] == "Test description"
        assert metadata["path"] == "metadata-test.md"

    def test_get_component_metadata_missing_fields(self, tmp_path):
        """Test getting metadata with missing optional fields."""
        content = "---\n"
        content += "template_id: minimal\n"
        content += "template_type: memory\n"
        content += "version: 1.0.0\n"
        content += "description: Minimal\n"
        content += "---\n\nContent.\n"

        component_file = tmp_path / "minimal.md"
        component_file.write_text(content)

        loader = ComponentLoader(framework_dir=tmp_path)
        metadata = loader.get_component_metadata("minimal.md")

        assert metadata["template_id"] == "minimal"


class TestClearCache:
    """Test clear_cache method."""

    def test_clear_cache(self, tmp_path):
        """Test clearing the component cache."""
        content = "---\n"
        content += "template_id: cache-test\n"
        content += "template_type: memory\n"
        content += "version: 1.0.0\n"
        content += "description: Cache test\n"
        content += "---\n\nContent.\n"

        component_file = tmp_path / "cache-test.md"
        component_file.write_text(content)

        loader = ComponentLoader(framework_dir=tmp_path)

        loader.load_component("cache-test.md")
        assert len(loader._loaded_components) == 1

        loader.clear_cache()
        assert len(loader._loaded_components) == 0


class TestGetStats:
    """Test get_stats method."""

    def test_get_stats_empty_cache(self, tmp_path):
        """Test stats with empty cache."""
        loader = ComponentLoader(framework_dir=tmp_path)
        stats = loader.get_stats()

        assert stats["total_loaded"] == 0
        assert stats["by_type"] == {}

    def test_get_stats_with_components(self, tmp_path):
        """Test stats with loaded components."""
        for dir_name in ["memory", "knowledge"]:
            dir_path = tmp_path / dir_name
            dir_path.mkdir()
            for i in range(2):
                content = "---\n"
                content += f"template_id: comp{i}\n"
                content += f"template_type: {dir_name}\n"
                content += "version: 1.0.0\n"
                content += "description: Test\n"
                content += "---\n\nContent.\n"

                component_file = dir_path / f"comp{i}.md"
                component_file.write_text(content)

        loader = ComponentLoader(framework_dir=tmp_path)

        loader.load_component("memory/comp0.md")
        loader.load_component("memory/comp1.md")
        loader.load_component("knowledge/comp0.md")

        stats = loader.get_stats()

        assert stats["total_loaded"] == 3
        assert stats["by_type"]["memory"] == 2
        assert stats["by_type"]["knowledge"] == 1


class TestComponentLoaderError:
    """Test ComponentLoaderError exception."""

    def test_error_message_only(self):
        """Test error with just message."""
        error = ComponentLoaderError("Something went wrong")
        assert str(error) == "Something went wrong"
        assert error.component_path is None
        assert error.cause is None

    def test_error_with_component_path(self):
        """Test error with component path."""
        error = ComponentLoaderError("Failed to load", component_path="test.md")
        assert "Failed to load" in str(error)
        assert "test.md" in str(error)
        assert error.component_path == "test.md"

    def test_error_with_cause(self):
        """Test error with cause exception."""
        cause = ValueError("Original error")
        error = ComponentLoaderError("Failed", cause=cause)
        assert "Failed" in str(error)
        assert "Original error" in str(error)
        assert error.cause is cause

    def test_error_with_all_details(self):
        """Test error with all details."""
        cause = FileNotFoundError("File not found")
        error = ComponentLoaderError(
            "Load failed",
            component_path="memory/test.md",
            cause=cause
        )
        assert "Load failed" in str(error)
        assert "memory/test.md" in str(error)
        assert "File not found" in str(error)
        assert error.component_path == "memory/test.md"
        assert error.cause is cause


class TestComponentLoaderIntegration:
    """Integration tests for ComponentLoader with actual templates."""

    def test_load_all_template_types(self, tmp_path):
        """Test loading components of all valid types."""
        for template_type in ComponentLoader.VALID_TEMPLATE_TYPES:
            dir_path = tmp_path / template_type
            dir_path.mkdir()

            content = "---\n"
            content += f"template_id: {template_type}-template\n"
            content += f"template_type: {template_type}\n"
            content += "version: 1.0.0\n"
            content += f"description: {template_type} template\n"
            content += "---\n\n"
            content += f"# {template_type.title()} Template\n\n"
            content += f"Content for {template_type}.\n"

            component_file = dir_path / f"{template_type}.md"
            component_file.write_text(content)

        loader = ComponentLoader(framework_dir=tmp_path)

        for template_type in ComponentLoader.VALID_TEMPLATE_TYPES:
            component = loader.load_component(f"{template_type}/{template_type}.md")
            assert component["frontmatter"]["template_type"] == template_type
            assert component["frontmatter"]["version"] == "1.0.0"

    def test_validate_all_template_types(self, tmp_path):
        """Test validation passes for all valid template types."""
        for template_type in ComponentLoader.VALID_TEMPLATE_TYPES:
            dir_path = tmp_path / template_type
            dir_path.mkdir()

            content = "---\n"
            content += f"template_id: {template_type}-template\n"
            content += f"template_type: {template_type}\n"
            content += "version: 1.0.0\n"
            content += f"description: {template_type} template\n"
            content += "---\n\n"
            content += "# Content\n"

            component_file = dir_path / f"{template_type}.md"
            component_file.write_text(content)

        loader = ComponentLoader(framework_dir=tmp_path)

        for template_type in ComponentLoader.VALID_TEMPLATE_TYPES:
            errors = loader.validate_component(f"{template_type}/{template_type}.md")
            assert errors == [], f"Validation failed for {template_type}: {errors}"

    def test_roundtrip_load_and_render(self, tmp_path):
        """Test loading and rendering a component in one workflow."""
        content = "---\n"
        content += "template_id: roundtrip\n"
        content += "template_type: tasks\n"
        content += "version: 1.0.0\n"
        content += "description: Roundtrip test\n"
        content += "---\n\n"
        content += "# Task: {{TASK_NAME}}\n\n"
        content += "**Assigned to:** {{ASSIGNEE}}\n\n"
        content += "**Due:** {{DUE_DATE}}\n\n"
        content += "## Description\n\n{{DESCRIPTION}}\n"

        component_file = tmp_path / "roundtrip.md"
        component_file.write_text(content)

        loader = ComponentLoader(framework_dir=tmp_path)

        metadata = loader.get_component_metadata("roundtrip.md")
        assert metadata["template_id"] == "roundtrip"

        rendered = loader.render_component(
            "roundtrip.md",
            {
                "TASK_NAME": "Implement Feature",
                "ASSIGNEE": "Jane Smith",
                "DUE_DATE": "2026-04-15",
                "DESCRIPTION": "Implement the new feature per design doc."
            }
        )

        assert "# Task: Implement Feature" in rendered
        assert "**Assigned to:** Jane Smith" in rendered
        assert "**Due:** 2026-04-15" in rendered
        assert "Implement the new feature" in rendered
        assert "{{" not in rendered
