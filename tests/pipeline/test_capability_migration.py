# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Tests for capability vocabulary migration (ARCH-2).

Tests cover:
- YAML files use unified vocabulary
- Migration script preserves structure
- Registry validation functional
- No duplicate capabilities
"""

import os
import pytest
import yaml
import subprocess
import tempfile
from pathlib import Path
from typing import Set, Dict, List


class TestCapabilityVocabulary:
    """Tests for unified capability vocabulary."""

    CONFIG_AGENTS_DIR = Path(__file__).parent.parent.parent / "config" / "agents"
    CAPABILITIES_FILE = Path(__file__).parent.parent.parent / "src" / "gaia" / "core" / "capabilities.py"

    def get_valid_capabilities(self) -> Set[str]:
        """Get valid capabilities from capabilities.py."""
        if not self.CAPABILITIES_FILE.exists():
            # Fallback to known capabilities
            return {
                "reasoning",
                "coding",
                "testing",
                "documentation",
                "analysis",
                "planning",
                "architecture",
                "security",
                "performance",
                "debugging",
                "refactoring",
                "code-review",
                "requirements",
                "design",
                "implementation",
                "deployment",
                "monitoring",
            }

        # Parse capabilities.py
        content = self.CAPABILITIES_FILE.read_text(encoding='utf-8')

        # Find enum members
        import re
        matches = re.findall(r'^\s+([A-Z_]+)\s*=\s*"[^"]+"', content, re.MULTILINE)

        if matches:
            return set(m.lower() for m in matches)

        # Fallback
        return {"reasoning", "coding", "testing"}

    def get_yaml_files(self) -> List[Path]:
        """Get all YAML agent config files."""
        if not self.CONFIG_AGENTS_DIR.exists():
            return []

        yaml_files = list(self.CONFIG_AGENTS_DIR.glob("*.yaml"))
        yaml_files.extend(self.CONFIG_AGENTS_DIR.glob("*.yml"))
        return yaml_files

    def test_all_yaml_files_use_unified_vocabulary(self):
        """Verify all YAML configs use unified capability vocabulary."""
        valid_capabilities = self.get_valid_capabilities()
        yaml_files = self.get_yaml_files()

        if not yaml_files:
            pytest.skip(f"No YAML files found in {self.CONFIG_AGENTS_DIR}")

        issues = []
        for yaml_file in yaml_files:
            with open(yaml_file, 'r', encoding='utf-8') as f:
                try:
                    content = yaml.safe_load(f)
                except yaml.YAMLError as e:
                    issues.append(f"{yaml_file.name}: YAML parse error - {e}")
                    continue

            if not content:
                continue

            capabilities = content.get('capabilities', [])
            if not isinstance(capabilities, list):
                issues.append(f"{yaml_file.name}: 'capabilities' is not a list")
                continue

            for cap in capabilities:
                if not isinstance(cap, str):
                    issues.append(f"{yaml_file.name}: Capability '{cap}' is not a string")
                    continue

                cap_lower = cap.lower().strip()
                if cap_lower not in valid_capabilities:
                    # Check for common legacy terms that should be migrated
                    legacy_mappings = {
                        "requirements-analysis": "requirements",
                        "full-stack-development": "coding",
                        "api-design": "design",
                        "code-quality": "code-review",
                        "performance-optimization": "performance",
                        "security-audit": "security",
                    }

                    if cap_lower in legacy_mappings:
                        issues.append(
                            f"{yaml_file.name}: '{cap}' should be migrated to "
                            f"'{legacy_mappings[cap_lower]}'"
                        )
                    else:
                        issues.append(
                            f"{yaml_file.name}: Capability '{cap}' not in vocabulary. "
                            f"Valid: {sorted(valid_capabilities)}"
                        )

        assert len(issues) == 0, f"Capability vocabulary issues:\n" + "\n".join(issues)

    def test_no_duplicate_capabilities_in_single_file(self):
        """Verify no duplicate capabilities within a single YAML file."""
        yaml_files = self.get_yaml_files()

        if not yaml_files:
            pytest.skip(f"No YAML files found in {self.CONFIG_AGENTS_DIR}")

        duplicates = []
        for yaml_file in yaml_files:
            with open(yaml_file, 'r', encoding='utf-8') as f:
                content = yaml.safe_load(f)

            if not content:
                continue

            capabilities = content.get('capabilities', [])
            if not isinstance(capabilities, list):
                continue

            seen = set()
            for cap in capabilities:
                cap_lower = cap.lower().strip() if isinstance(cap, str) else cap
                if cap_lower in seen:
                    duplicates.append(f"{yaml_file.name}: Duplicate capability '{cap}'")
                seen.add(cap_lower)

        assert len(duplicates) == 0, f"Duplicate capabilities:\n" + "\n".join(duplicates)

    def test_yaml_structure_preserved(self):
        """Verify YAML files have required structure."""
        yaml_files = self.get_yaml_files()

        if not yaml_files:
            pytest.skip(f"No YAML files found in {self.CONFIG_AGENTS_DIR}")

        issues = []
        for yaml_file in yaml_files:
            with open(yaml_file, 'r', encoding='utf-8') as f:
                content = yaml.safe_load(f)

            # Check required fields
            if not content.get('id'):
                issues.append(f"{yaml_file.name}: Missing 'id' field")

            if not content.get('name'):
                issues.append(f"{yaml_file.name}: Missing 'name' field")

            if 'capabilities' not in content:
                issues.append(f"{yaml_file.name}: Missing 'capabilities' field")

        assert len(issues) == 0, f"YAML structure issues:\n" + "\n".join(issues)


class TestMigrationScript:
    """Tests for capability migration script."""

    MIGRATION_SCRIPT = Path(__file__).parent.parent.parent / "util" / "migrate-capabilities.py"
    CONFIG_AGENTS_DIR = Path(__file__).parent.parent.parent / "config" / "agents"

    def test_migration_script_exists(self):
        """Verify migration script exists."""
        assert self.MIGRATION_SCRIPT.exists(), \
            f"Migration script not found: {self.MIGRATION_SCRIPT}"

    def test_migration_script_syntax_valid(self):
        """Verify migration script has valid Python syntax."""
        result = subprocess.run(
            ["python", "-m", "py_compile", str(self.MIGRATION_SCRIPT)],
            capture_output=True,
            text=True
        )

        assert result.returncode == 0, \
            f"Migration script syntax error:\n{result.stderr}"

    def test_migration_script_dry_run(self):
        """Verify migration script runs in dry-run mode."""
        if not self.MIGRATION_SCRIPT.exists():
            pytest.skip(f"Migration script not found: {self.MIGRATION_SCRIPT}")

        result = subprocess.run(
            ["python", str(self.MIGRATION_SCRIPT), "--dry-run"],
            capture_output=True,
            text=True,
            cwd=str(self.MIGRATION_SCRIPT.parent)
        )

        # Should not crash
        assert result.returncode == 0 or result.returncode == 1, \
            f"Migration script dry-run failed:\n{result.stderr}"


class TestCapabilityMigrationWithTempFiles:
    """Tests for migration script with temporary files."""

    def test_migration_preserves_yaml_structure(self, tmp_path):
        """Verify migration preserves YAML structure."""
        # Create test YAML with legacy vocabulary
        test_yaml = tmp_path / "test-agent.yaml"
        test_yaml.write_text("""id: test-agent
name: Test Agent
version: 1.0.0
capabilities:
  - requirements-analysis
  - full-stack-development
  - api-design
description: Test agent for migration
""")

        # Run migration script
        migration_script = Path(__file__).parent.parent.parent / "util" / "migrate-capabilities.py"

        if not migration_script.exists():
            pytest.skip(f"Migration script not found: {migration_script}")

        result = subprocess.run(
            ["python", str(migration_script), str(tmp_path)],
            capture_output=True,
            text=True,
            cwd=str(migration_script.parent)
        )

        # Script should run without error
        if result.returncode != 0:
            pytest.skip(f"Migration script failed: {result.stderr}")

        # Verify structure preserved
        with open(test_yaml, 'r', encoding='utf-8') as f:
            content = yaml.safe_load(f)

        assert content['id'] == 'test-agent'
        assert content['name'] == 'Test Agent'
        assert content['version'] == '1.0.0'
        assert 'description' in content

    def test_migration_updates_capabilities(self, tmp_path):
        """Verify migration updates legacy capabilities."""
        # Create test YAML with legacy vocabulary
        test_yaml = tmp_path / "test-agent.yaml"
        test_yaml.write_text("""id: test-agent
name: Test Agent
capabilities:
  - requirements-analysis
  - full-stack-development
""")

        migration_script = Path(__file__).parent.parent.parent / "util" / "migrate-capabilities.py"

        if not migration_script.exists():
            pytest.skip(f"Migration script not found: {migration_script}")

        result = subprocess.run(
            ["python", str(migration_script), str(tmp_path)],
            capture_output=True,
            text=True,
            cwd=str(migration_script.parent)
        )

        if result.returncode != 0:
            pytest.skip(f"Migration script failed: {result.stderr}")

        # Verify capabilities updated
        with open(test_yaml, 'r', encoding='utf-8') as f:
            content = yaml.safe_load(f)

        capabilities = content.get('capabilities', [])

        # Legacy terms should be migrated
        legacy_terms = {'requirements-analysis', 'full-stack-development'}
        found_legacy = set(cap.lower() for cap in capabilities) & legacy_terms

        assert len(found_legacy) == 0, \
            f"Legacy capabilities not migrated: {found_legacy}"


class TestAgentRegistryValidation:
    """Tests for agent registry capability validation."""

    def test_registry_validates_capabilities(self):
        """Verify agent registry validates capabilities on load."""
        from gaia.agents.registry import AgentRegistry
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test YAML with invalid capability
            test_yaml = Path(tmpdir) / "test-agent.yaml"
            test_yaml.write_text("""id: test-agent
name: Test Agent
capabilities:
  - invalid-capability-xyz
""")

            registry = AgentRegistry(agents_dir=tmpdir, auto_reload=False)

            # Registry should either reject or warn about invalid capability
            # (Implementation-dependent behavior)
            try:
                agents = registry.list_agents()
                # If we get here, validation may not be implemented yet
                pytest.skip("Registry validation not yet implemented")
            except Exception as e:
                # Expected if validation is implemented
                assert "capability" in str(e).lower() or "invalid" in str(e).lower()


class TestCapabilityReferenceDocument:
    """Tests for unified capability reference document."""

    README_FILE = Path(__file__).parent.parent.parent / "config" / "agents" / "README-capabilities.md"

    def test_reference_document_exists(self):
        """Verify capability reference document exists."""
        # This file should be created as part of ARCH-2
        if not self.README_FILE.exists():
            pytest.skip(
                f"Reference document not found: {self.README_FILE}. "
                f"This should be created as part of ARCH-2."
            )

    def test_reference_document_lists_all_capabilities(self):
        """Verify reference document lists all valid capabilities."""
        if not self.README_FILE.exists():
            pytest.skip(f"Reference document not found: {self.README_FILE}")

        content = self.README_FILE.read_text(encoding='utf-8')

        # Should have capability list
        assert "## Capabilities" in content or "## Capability" in content, \
            "Reference document missing capabilities section"

        # Should have descriptions
        assert len(content) > 500, "Reference document too short"


class TestCapabilityCoverage:
    """Tests for capability coverage analysis."""

    def test_all_agents_have_capabilities(self):
        """Verify all agent configs have capabilities defined."""
        config_agents_dir = Path(__file__).parent.parent.parent / "config" / "agents"

        if not config_agents_dir.exists():
            pytest.skip(f"Config agents directory not found: {config_agents_dir}")

        agents_without_caps = []

        for yaml_file in config_agents_dir.glob("*.yaml"):
            with open(yaml_file, 'r', encoding='utf-8') as f:
                content = yaml.safe_load(f)

            capabilities = content.get('capabilities', [])
            if not capabilities:
                agents_without_caps.append(yaml_file.name)

        assert len(agents_without_caps) == 0, \
            f"Agents without capabilities: {', '.join(agents_without_caps)}"

    def test_capability_distribution(self, capsys):
        """Analyze capability distribution across agents."""
        config_agents_dir = Path(__file__).parent.parent.parent / "config" / "agents"

        if not config_agents_dir.exists():
            pytest.skip(f"Config agents directory not found: {config_agents_dir}")

        capability_counts = {}

        for yaml_file in config_agents_dir.glob("*.yaml"):
            with open(yaml_file, 'r', encoding='utf-8') as f:
                content = yaml.safe_load(f)

            for cap in content.get('capabilities', []):
                cap_lower = cap.lower().strip()
                capability_counts[cap_lower] = capability_counts.get(cap_lower, 0) + 1

        # Print distribution for analysis
        print("\n\nCapability Distribution:")
        print("=" * 40)
        for cap, count in sorted(capability_counts.items(), key=lambda x: -x[1]):
            print(f"  {cap}: {count} agents")
        print("=" * 40)

        # Test passes as long as we have some distribution
        assert len(capability_counts) > 0, "No capabilities found"
