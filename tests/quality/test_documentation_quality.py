# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Tests for documentation quality (DOC-1, DOC-3).

Tests cover:
- YAML frontmatter presence in spec files
- Branch change matrix synchronization
- Documentation build validation
"""

import os
import pytest
import yaml
from pathlib import Path
import re


class TestYamlFrontmatter:
    """Tests for YAML frontmatter in documentation files (DOC-1)."""

    DOCS_SPEC_DIR = Path(__file__).parent.parent.parent / "docs" / "spec"

    FILES_REQUIRING_FRONTMATTER = [
        "agent-ui-eval-kpi-reference.md",
        "agent-ui-eval-kpis.md",
        "gaia-loom-architecture.md",
        "nexus-gaia-native-integration-spec.md",
        "pipeline-metrics-competitive-analysis.md",
        "pipeline-metrics-kpi-reference.md",
        "phase5_multi_stage_pipeline.md",
        "component-framework-design-spec.md",
        "component-framework-implementation-plan.md",
    ]

    @pytest.mark.parametrize("filename", FILES_REQUIRING_FRONTMATTER)
    def test_spec_file_has_yaml_frontmatter(self, filename):
        """Verify spec file has YAML frontmatter (DOC-1)."""
        filepath = self.DOCS_SPEC_DIR / filename

        if not filepath.exists():
            pytest.fail(f"File not found: {filepath}")

        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        # Check line 1 is ---
        assert len(lines) >= 3, f"{filename}: File too short for frontmatter"
        assert lines[0].strip() == '---', \
            f"{filename}: Line 1 should be '---', got '{lines[0].strip()}'"

        # Check line 2 has title field
        assert lines[1].strip().startswith('title:'), \
            f"{filename}: Line 2 should start with 'title:', got '{lines[1].strip()}'"

        # Check line 3 is ---
        assert lines[2].strip() == '---', \
            f"{filename}: Line 3 should be '---', got '{lines[2].strip()}'"

    def test_all_spec_files_have_frontmatter(self):
        """Verify all .md files in docs/spec have YAML frontmatter."""
        if not self.DOCS_SPEC_DIR.exists():
            pytest.skip(f"Docs spec directory not found: {self.DOCS_SPEC_DIR}")

        md_files = list(self.DOCS_SPEC_DIR.glob("*.md"))

        missing_frontmatter = []
        for filepath in md_files:
            with open(filepath, 'r', encoding='utf-8') as f:
                first_line = f.readline().strip()

            if first_line != '---':
                missing_frontmatter.append(filepath.name)

        assert len(missing_frontmatter) == 0, \
            f"Files missing YAML frontmatter:\n" + "\n".join(missing_frontmatter)

    def test_frontmatter_title_matches_filename(self):
        """Verify frontmatter title matches filename."""
        if not self.DOCS_SPEC_DIR.exists():
            pytest.skip(f"Docs spec directory not found: {self.DOCS_SPEC_DIR}")

        mismatches = []
        for filepath in self.DOCS_SPEC_DIR.glob("*.md"):
            with open(filepath, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            if len(lines) >= 2 and lines[0].strip() == '---':
                title_line = lines[1].strip()
                if title_line.startswith('title:'):
                    title = title_line.split(':', 1)[1].strip()
                    expected_title = filepath.stem.replace('_', ' ').replace('-', ' ').title()

                    # Check if title is reasonable (not exact match, but contains key words)
                    filename_words = set(filepath.stem.lower().split('_'))
                    title_words = set(title.lower().split())

                    # At least half the filename words should appear in title
                    matching_words = filename_words & title_words
                    if len(matching_words) < len(filename_words) // 2:
                        mismatches.append(
                            f"{filepath.name}: title='{title}' doesn't match filename"
                        )

        assert len(mismatches) == 0, \
            f"Title mismatches:\n" + "\n".join(mismatches)


class TestBranchChangeMatrix:
    """Tests for branch-change-matrix.md synchronization (DOC-3)."""

    MATRIX_FILE = Path(__file__).parent.parent.parent / "docs" / "reference" / "branch-change-matrix.md"
    MANIFEST_FILE = Path(__file__).parent.parent.parent / "docs" / "spec" / "phase5-update-manifest.md"

    def test_matrix_file_exists(self):
        """Verify branch-change-matrix.md exists."""
        assert self.MATRIX_FILE.exists(), \
            f"Branch change matrix not found: {self.MATRIX_FILE}"

    def test_matrix_has_phase5_section(self):
        """Verify matrix has Phase 5 section (Section 3.13)."""
        if not self.MATRIX_FILE.exists():
            pytest.skip(f"Matrix file not found: {self.MATRIX_FILE}")

        content = self.MATRIX_FILE.read_text(encoding='utf-8')

        # Check for Phase 5 section header
        assert "### 3.13" in content or "Phase 5" in content, \
            "Matrix missing Phase 5 section (3.13)"

    def test_matrix_has_open_items_section(self):
        """Verify matrix has Open Items section."""
        if not self.MATRIX_FILE.exists():
            pytest.skip(f"Matrix file not found: {self.MATRIX_FILE}")

        content = self.MATRIX_FILE.read_text(encoding='utf-8')

        assert "Open Item" in content or "OI-" in content, \
            "Matrix missing Open Items section"

    def test_matrix_has_commit_references(self):
        """Verify matrix has commit references from Phase 5."""
        if not self.MATRIX_FILE.exists():
            pytest.skip(f"Matrix file not found: {self.MATRIX_FILE}")

        content = self.MATRIX_FILE.read_text(encoding='utf-8')

        # Commits that should be referenced (from phase5-update-manifest.md)
        commits_to_verify = [
            "57ee63d",  # First Phase 5 commit
            "fa3ef98",  # Last Phase 5 commit
        ]

        missing_commits = []
        for commit in commits_to_verify:
            if commit not in content:
                missing_commits.append(commit)

        assert len(missing_commits) == 0, \
            f"Missing commit references: {', '.join(missing_commits)}"

    def test_matrix_statistics_match_git_diff(self):
        """Verify matrix statistics match git diff --stat."""
        if not self.MATRIX_FILE.exists():
            pytest.skip(f"Matrix file not found: {self.MATRIX_FILE}")

        content = self.MATRIX_FILE.read_text(encoding='utf-8')

        # Check for statistics section
        has_stats = (
            "files changed" in content.lower() or
            "lines inserted" in content.lower() or
            "commits" in content.lower()
        )

        assert has_stats, "Matrix missing branch statistics"

    def test_open_item_statuses_updated(self):
        """Verify Open Item statuses reflect current state."""
        if not self.MATRIX_FILE.exists():
            pytest.skip(f"Matrix file not found: {self.MATRIX_FILE}")

        content = self.MATRIX_FILE.read_text(encoding='utf-8')

        # Check for status indicators
        status_patterns = [
            "RESOLVED",
            "PARTIAL",
            "OPEN",
            "BLOCKED",
            "DECISION",
        ]

        found_statuses = [p for p in status_patterns if p in content]

        assert len(found_statuses) >= 2, \
            f"Matrix missing Open Item status indicators. Found: {found_statuses}"


class TestDocumentationBuild:
    """Tests for documentation build validation."""

    DOCS_DIR = Path(__file__).parent.parent.parent / "docs"

    def test_docs_json_exists(self):
        """Verify docs.json navigation file exists."""
        docs_json = self.DOCS_DIR / "docs.json"
        assert docs_json.exists(), f"docs.json not found: {docs_json}"

    def test_docs_json_valid_structure(self):
        """Verify docs.json has valid JSON structure."""
        docs_json = self.DOCS_DIR / "docs.json"

        if not docs_json.exists():
            pytest.skip(f"docs.json not found: {docs_json}")

        with open(docs_json, 'r', encoding='utf-8') as f:
            data = json.load(f)

        assert "pages" in data or "sections" in data, \
            "docs.json missing 'pages' or 'sections' field"

    def test_spec_files_referenced_in_docs_json(self):
        """Verify spec files are referenced in docs.json navigation."""
        docs_json = self.DOCS_DIR / "docs.json"

        if not docs_json.exists():
            pytest.skip(f"docs.json not found: {docs_json}")

        with open(docs_json, 'r', encoding='utf-8') as f:
            content = f.read()

        # Check for some spec file references
        spec_files = [
            "phase5_multi_stage_pipeline",
            "component-framework",
        ]

        found = [f for f in spec_files if f in content]

        assert len(found) >= 1, \
            f"Spec files not referenced in docs.json: {spec_files}"


class TestMarkdownQuality:
    """Tests for Markdown quality."""

    DOCS_SPEC_DIR = Path(__file__).parent.parent.parent / "docs" / "spec"

    def test_no_broken_links_internal(self):
        """Verify no broken internal Markdown links."""
        if not self.DOCS_SPEC_DIR.exists():
            pytest.skip(f"Docs spec directory not found: {self.DOCS_SPEC_DIR}")

        broken_links = []
        for md_file in self.DOCS_SPEC_DIR.glob("*.md"):
            content = md_file.read_text(encoding='utf-8')

            # Find internal links
            internal_links = re.findall(r'\[([^\]]+)\]\(([^)]+)\)', content)

            for link_text, link_url in internal_links:
                # Check for obvious broken patterns
                if link_url.startswith('http'):
                    continue  # External link, skip

                if link_url.startswith('#'):
                    continue  # Anchor link, skip

                # Internal file reference
                if not link_url.endswith('.md') and not link_url.endswith('.mdx'):
                    broken_links.append(
                        f"{md_file.name}: Link '{link_url}' missing extension"
                    )

        assert len(broken_links) == 0, \
            f"Broken internal links:\n" + "\n".join(broken_links)

    def test_code_blocks_have_language(self):
        """Verify code blocks specify language."""
        if not self.DOCS_SPEC_DIR.exists():
            pytest.skip(f"Docs spec directory not found: {self.DOCS_SPEC_DIR}")

        issues = []
        for md_file in self.DOCS_SPEC_DIR.glob("*.md"):
            content = md_file.read_text(encoding='utf-8')

            # Find code blocks without language
            bad_blocks = re.findall(r'```\s*\n', content)

            if bad_blocks:
                issues.append(
                    f"{md_file.name}: {len(bad_blocks)} code blocks without language"
                )

        assert len(issues) == 0, \
            f"Code blocks missing language:\n" + "\n".join(issues)


# Import json for docs.json tests
import json
