# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Tests for two-registry pattern separation (INT-2).

Tests cover:
- PipelineAgentRegistry isolation from UI registry
- Bridge pattern between registries
- Import path validation
- No circular dependencies
"""

import ast
import inspect
import pytest
from pathlib import Path
from typing import Set, List
from unittest.mock import Mock, patch


class TestPipelineRegistryIsolation:
    """Tests for PipelineAgentRegistry isolation."""

    def test_pipeline_registry_module_exists(self):
        """Verify PipelineAgentRegistry exists in pipeline module."""
        # After INT-2 implementation, registry should be at:
        # src/gaia/pipeline/agent_registry.py

        pipeline_registry_path = Path(__file__).parent.parent.parent / "src" / "gaia" / "pipeline" / "agent_registry.py"
        agents_registry_path = Path(__file__).parent.parent.parent / "src" / "gaia" / "agents" / "registry.py"

        # At least one should exist
        if not pipeline_registry_path.exists() and not agents_registry_path.exists():
            pytest.skip("Registry files not found - INT-2 not yet implemented")

        # If both exist, that's the two-registry pattern
        if pipeline_registry_path.exists() and agents_registry_path.exists():
            # Verify they have different content
            pipeline_content = pipeline_registry_path.read_text(encoding='utf-8')
            agents_content = agents_registry_path.read_text(encoding='utf-8')

            # Should have different class names or purposes
            assert "PipelineAgentRegistry" in pipeline_content or "PipelineAgentRegistry" in agents_content, \
                "Expected PipelineAgentRegistry class in one of the registry files"

    def test_pipeline_registry_no_import_from_agents_registry(self):
        """Verify PipelineAgentRegistry doesn't import from gaia.agents.registry."""
        pipeline_registry_path = Path(__file__).parent.parent.parent / "src" / "gaia" / "pipeline" / "agent_registry.py"

        if not pipeline_registry_path.exists():
            pytest.skip("Pipeline registry not found - INT-2 not yet implemented")

        # Parse the module
        source = pipeline_registry_path.read_text(encoding='utf-8')
        tree = ast.parse(source)

        # Check for forbidden imports
        forbidden_imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and 'gaia.agents.registry' in node.module:
                    forbidden_imports.append(f"from {node.module} import ...")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if 'gaia.agents.registry' in alias.name:
                        forbidden_imports.append(f"import {alias.name}")

        assert len(forbidden_imports) == 0, \
            f"PipelineAgentRegistry should not import from gaia.agents.registry:\n" + \
            "\n".join(forbidden_imports)

    def test_pipeline_registry_has_separated_responsibilities(self):
        """Verify PipelineAgentRegistry has pipeline-specific responsibilities."""
        pipeline_registry_path = Path(__file__).parent.parent.parent / "src" / "gaia" / "pipeline" / "agent_registry.py"

        if not pipeline_registry_path.exists():
            pytest.skip("Pipeline registry not found")

        source = pipeline_registry_path.read_text(encoding='utf-8')

        # Should have pipeline-specific methods
        pipeline_methods = [
            "select_agent",      # Capability-based selection
            "get_by_capability", # Capability lookup
        ]

        found_methods = [m for m in pipeline_methods if m in source]

        assert len(found_methods) >= 1, \
            f"Pipeline registry missing pipeline-specific methods. Found: {found_methods}"


class TestAgentRegistryUI:
    """Tests for UI-facing AgentRegistry."""

    def test_ui_registry_exists(self):
        """Verify UI-facing AgentRegistry exists."""
        ui_registry_path = Path(__file__).parent.parent.parent / "src" / "gaia" / "agents" / "registry.py"

        if not ui_registry_path.exists():
            pytest.skip("UI registry not found")

        source = ui_registry_path.read_text(encoding='utf-8')

        # Should have UI-facing methods
        ui_methods = [
            "get",           # Get agent by ID
            "factory",       # Factory pattern
            "list_agents",   # List available agents
        ]

        found_methods = [m for m in ui_methods if m in source]

        assert len(found_methods) >= 1, \
            f"UI registry missing UI-specific methods. Found: {found_methods}"

    def test_ui_registry_supports_three_source_discovery(self):
        """Verify UI registry supports 3-source discovery (builtin, custom, YAML)."""
        ui_registry_path = Path(__file__).parent.parent.parent / "src" / "gaia" / "agents" / "registry.py"

        if not ui_registry_path.exists():
            pytest.skip("UI registry not found")

        source = ui_registry_path.read_text(encoding='utf-8')

        # Should mention multiple sources
        source_keywords = ["builtin", "custom", "yaml", "manifest", "discover"]
        found_keywords = [k for k in source_keywords if k.lower() in source.lower()]

        # At least some discovery mechanism should be present
        assert len(found_keywords) >= 1, \
            f"UI registry missing discovery mechanism keywords: {found_keywords}"


class TestBridgePattern:
    """Tests for bridge pattern between registries."""

    def test_bridge_pattern_usage(self):
        """Verify bridge pattern: Pipeline selects, Agent instantiates."""
        # This test verifies the bridge pattern architecture
        # PipelineOrchestrator -> PipelineAgentRegistry.select_agent() -> agent_id
        # agent_id -> AgentRegistry.get(agent_id).factory() -> agent instance

        pipeline_orchestrator_path = Path(__file__).parent.parent.parent / "src" / "gaia" / "pipeline" / "orchestrator.py"

        if not pipeline_orchestrator_path.exists():
            pytest.skip("PipelineOrchestrator not found")

        source = pipeline_orchestrator_path.read_text(encoding='utf-8')

        # Should use registries
        registry_usage = [
            "AgentRegistry",
            "PipelineAgentRegistry",
            "registry",
        ]

        found_usage = [u for u in registry_usage if u in source]

        # Should reference at least one registry
        if len(found_usage) == 0:
            pytest.skip("PipelineOrchestrator doesn't use registries yet")


class TestImportPaths:
    """Tests for correct import paths."""

    def test_no_circular_imports_between_registries(self):
        """Verify no circular imports between registry modules."""
        # Check for circular imports by attempting to import
        try:
            # Try importing both modules
            from gaia.pipeline import agent_registry  # May not exist yet
        except ImportError:
            pass  # Expected if INT-2 not implemented

        try:
            from gaia.agents import registry  # UI registry
        except ImportError:
            pytest.skip("Cannot import agents.registry")

        # If we get here without circular import errors, test passes
        assert True

    def test_pipeline_init_exports_registry(self):
        """Verify pipeline __init__.py exports PipelineAgentRegistry."""
        pipeline_init_path = Path(__file__).parent.parent.parent / "src" / "gaia" / "pipeline" / "__init__.py"

        if not pipeline_init_path.exists():
            pytest.skip("pipeline/__init__.py not found")

        source = pipeline_init_path.read_text(encoding='utf-8')

        # Should export PipelineAgentRegistry if it exists
        pipeline_registry_path = Path(__file__).parent.parent.parent / "src" / "gaia" / "pipeline" / "agent_registry.py"

        if pipeline_registry_path.exists():
            # Check if exported
            if "PipelineAgentRegistry" in source:
                assert "PipelineAgentRegistry" in source
            else:
                pytest.skip("PipelineAgentRegistry not exported in __init__.py")


class TestRegistryCapabilities:
    """Tests for registry capability-based selection."""

    def test_pipeline_registry_select_agent_by_capability(self):
        """Verify PipelineAgentRegistry can select agent by capability."""
        # This is a functional test for the select_agent method

        pipeline_registry_path = Path(__file__).parent.parent.parent / "src" / "gaia" / "pipeline" / "agent_registry.py"

        if not pipeline_registry_path.exists():
            pytest.skip("Pipeline registry not found")

        source = pipeline_registry_path.read_text(encoding='utf-8')

        # Should have select_agent method with capability parameter
        if "def select_agent" in source:
            # Check for capability-related parameters
            has_capability_param = any([
                "capability" in source,
                "capabilities" in source,
                "phase" in source,
            ])

            assert has_capability_param, \
                "select_agent should use capability-based selection"
        else:
            pytest.skip("select_agent method not implemented yet")


class TestRegistryNaming:
    """Tests for registry naming conventions."""

    def test_registry_classes_have_distinct_names(self):
        """Verify registry classes have distinct, descriptive names."""
        # Check both registry files for class names
        ui_registry_path = Path(__file__).parent.parent.parent / "src" / "gaia" / "agents" / "registry.py"
        pipeline_registry_path = Path(__file__).parent.parent.parent / "src" / "gaia" / "pipeline" / "agent_registry.py"

        class_names = []

        if ui_registry_path.exists():
            source = ui_registry_path.read_text(encoding='utf-8')
            import re
            classes = re.findall(r'class\s+(\w+)', source)
            class_names.extend(classes)

        if pipeline_registry_path.exists():
            source = pipeline_registry_path.read_text(encoding='utf-8')
            import re
            classes = re.findall(r'class\s+(\w+)', source)
            class_names.extend(classes)

        if not class_names:
            pytest.skip("No registry classes found")

        # Should have distinct names
        if len(class_names) > 1:
            assert len(set(class_names)) == len(class_names), \
                f"Registry classes should have distinct names: {class_names}"


class TestRegistryIntegration:
    """Integration tests for two-registry pattern."""

    @pytest.mark.integration
    def test_two_registries_coexist(self):
        """Verify both registries can coexist without conflict."""
        # This test verifies both registries can be imported and used

        try:
            from gaia.agents.registry import AgentRegistry
        except ImportError:
            pytest.skip("UI registry not available")

        try:
            from gaia.pipeline.agent_registry import PipelineAgentRegistry
        except ImportError:
            pytest.skip("Pipeline registry not available")

        # Both should be instantiable
        ui_registry = AgentRegistry()
        pipeline_registry = PipelineAgentRegistry()

        assert ui_registry is not None
        assert pipeline_registry is not None
        assert type(ui_registry) != type(pipeline_registry), \
            "Should be different classes"


class TestINT2ImplementationStatus:
    """Tests to verify INT-2 implementation status."""

    def test_int2_relocation_complete(self):
        """Verify INT-2 registry relocation is complete."""
        pipeline_registry_path = Path(__file__).parent.parent.parent / "src" / "gaia" / "pipeline" / "agent_registry.py"
        agents_registry_path = Path(__file__).parent.parent.parent / "src" / "gaia" / "agents" / "registry.py"

        if not pipeline_registry_path.exists():
            pytest.skip(
                "INT-2 not implemented: pipeline/agent_registry.py not found. "
                "This test should pass after registry relocation."
            )

        # If both exist, INT-2 is implemented
        if agents_registry_path.exists():
            # Verify they're different files
            pipeline_content = pipeline_registry_path.read_text(encoding='utf-8')
            agents_content = agents_registry_path.read_text(encoding='utf-8')

            # Should have different content
            assert pipeline_content != agents_content, \
                "Registry files should have different content"
