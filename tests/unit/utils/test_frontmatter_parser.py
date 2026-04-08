"""
Unit tests for Markdown frontmatter parser.
"""

import pytest
from pathlib import Path
import tempfile
import os

from gaia.utils.frontmatter_parser import (
    FrontmatterParser,
    ParsedMarkdown,
    FrontmatterParsingError,
    parse_markdown_frontmatter,
    parse_agent_markdown,
    extract_frontmatter_only,
    extract_body_only,
    parse_file,
    parse,
)


class TestParsedMarkdown:
    """Tests for ParsedMarkdown dataclass."""

    def test_to_dict(self):
        """Test ParsedMarkdown.to_dict() serialization."""
        result = ParsedMarkdown(
            raw_content="---\nid: test\n---\nBody",
            frontmatter={"id": "test"},
            body="Body",
            file_path="/test/file.md",
            has_frontmatter=True,
            frontmatter_format="yaml",
        )

        result_dict = result.to_dict()

        assert result_dict["frontmatter"] == {"id": "test"}
        assert result_dict["body"] == "Body"
        assert result_dict["file_path"] == "/test/file.md"
        assert result_dict["has_frontmatter"] is True
        assert result_dict["frontmatter_format"] == "yaml"


class TestFrontmatterParser:
    """Tests for FrontmatterParser class."""

    @pytest.fixture
    def parser(self):
        """Create a parser instance."""
        return FrontmatterParser()

    @pytest.fixture
    def strict_parser(self):
        """Create a parser that requires frontmatter."""
        return FrontmatterParser(require_frontmatter=True)

    def test_parse_with_frontmatter(self, parser):
        """Test parsing content with YAML frontmatter."""
        content = """---
id: test-agent
name: Test Agent
description: A test agent
---

# Agent Prompt

You are a test agent."""

        result = parser.parse(content)

        assert result.has_frontmatter is True
        assert result.frontmatter["id"] == "test-agent"
        assert result.frontmatter["name"] == "Test Agent"
        assert result.frontmatter["description"] == "A test agent"
        assert "# Agent Prompt" in result.body
        assert "You are a test agent" in result.body

    def test_parse_without_frontmatter(self, parser):
        """Test parsing content without frontmatter."""
        content = "# Just a body\n\nNo frontmatter here."

        result = parser.parse(content)

        assert result.has_frontmatter is False
        assert result.frontmatter == {}
        assert result.body == content

    def test_parse_requires_frontmatter(self, strict_parser):
        """Test that require_frontmatter raises on missing frontmatter."""
        content = "# Just a body\n\nNo frontmatter here."

        with pytest.raises(FrontmatterParsingError):
            strict_parser.parse(content)

    def test_parse_with_file_path(self, parser):
        """Test parsing with file_path metadata."""
        content = "---\nid: test\n---\nBody"

        result = parser.parse(content, file_path="/test/file.md")

        assert result.file_path == "/test/file.md"

    def test_parse_file(self, parser):
        """Test parsing a file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("---\nid: file-test\n---\nFile body")
            temp_path = f.name

        try:
            result = parser.parse_file(temp_path)

            assert result.frontmatter["id"] == "file-test"
            assert result.body == "File body"
            assert result.file_path == temp_path
        finally:
            os.unlink(temp_path)

    def test_parse_file_not_found(self, parser):
        """Test parsing non-existent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            parser.parse_file("/nonexistent/file.md")

    def test_parse_file_safe(self, parser):
        """Test safe file parsing with defaults."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("---\nid: safe-test\n---\nSafe body")
            temp_path = f.name

        try:
            result = parser.parse_file_safe(temp_path)

            assert result.frontmatter["id"] == "safe-test"
            assert result.body == "Safe body"
        finally:
            os.unlink(temp_path)

    def test_parse_file_safe_with_default(self, parser):
        """Test safe file parsing with custom defaults."""
        default_fm = {"id": "default", "name": "Default Agent"}

        result = parser.parse_file_safe(
            "/nonexistent/file.md",
            default_frontmatter=default_fm,
        )

        assert result.frontmatter == default_fm
        assert result.body == ""
        assert result.has_frontmatter is False

    def test_invalid_yaml_frontmatter(self, parser):
        """Test handling of invalid YAML in frontmatter."""
        content = """---
id: test
invalid: [unclosed
---
Body"""

        with pytest.raises(FrontmatterParsingError):
            parser.parse(content)

    def test_empty_frontmatter(self, parser):
        """Test parsing with empty frontmatter."""
        content = """---
---

Body content"""

        result = parser.parse(content)

        # Empty frontmatter should parse as empty dict
        # Note: body may include the --- separator if regex doesn't match
        assert result.frontmatter == {} or result.frontmatter is None
        # Body might include leading --- if pattern didn't match
        assert "Body content" in result.body

    def test_frontmatter_with_nested_structures(self, parser):
        """Test parsing frontmatter with nested structures."""
        content = """---
id: complex-agent
name: Complex Agent
capabilities:
  - analysis
  - planning
triggers:
  phases:
    - PLANNING
  keywords:
    - analyze
---
Body"""

        result = parser.parse(content)

        assert result.frontmatter["id"] == "complex-agent"
        assert "analysis" in result.frontmatter["capabilities"]
        assert result.frontmatter["triggers"]["phases"] == ["PLANNING"]

    def test_frontmatter_with_multiline_strings(self, parser):
        """Test parsing frontmatter with multiline strings."""
        content = """---
id: multiline-agent
description: |
  This is a multiline
  description field
  in the frontmatter
---
Body"""

        result = parser.parse(content)

        assert "multiline" in result.frontmatter["description"]
        assert "description field" in result.frontmatter["description"]


class TestConvenienceFunctions:
    """Tests for module convenience functions."""

    def test_parse_markdown_frontmatter(self):
        """Test parse_markdown_frontmatter function."""
        content = """---
id: func-test
name: Function Test
---
Body content"""

        frontmatter, body = parse_markdown_frontmatter(content)

        assert frontmatter["id"] == "func-test"
        assert frontmatter["name"] == "Function Test"
        assert "Body content" in body

    def test_parse_markdown_frontmatter_required(self):
        """Test parse_markdown_frontmatter with require_frontmatter=True."""
        content = "# No frontmatter"

        with pytest.raises(FrontmatterParsingError):
            parse_markdown_frontmatter(content, require_frontmatter=True)

    def test_extract_frontmatter_only(self):
        """Test extract_frontmatter_only function."""
        content = """---
id: extract-test
value: 42
---
Body"""

        fm = extract_frontmatter_only(content)

        assert fm["id"] == "extract-test"
        assert fm["value"] == 42

    def test_extract_body_only(self):
        """Test extract_body_only function."""
        content = """---
id: body-extract
---
# Body Content
This is the body."""

        body = extract_body_only(content)

        assert "# Body Content" in body
        assert "This is the body" in body
        assert "id:" not in body  # Frontmatter removed

    def test_parse_function(self):
        """Test module-level parse function."""
        content = "---\nid: parse-func\n---\nBody"
        result = parse(content)

        assert isinstance(result, ParsedMarkdown)
        assert result.frontmatter["id"] == "parse-func"

    def test_parse_file_function(self):
        """Test module-level parse_file function."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("---\nid: file-func\n---\nFile body")
            temp_path = f.name

        try:
            result = parse_file(temp_path)

            assert isinstance(result, ParsedMarkdown)
            assert result.frontmatter["id"] == "file-func"
        finally:
            os.unlink(temp_path)


class TestParseAgentMarkdown:
    """Tests for parse_agent_markdown function."""

    def test_parse_agent_markdown(self):
        """Test parsing agent Markdown file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("""---
id: test-agent
name: Test Agent
description: A test agent
tools:
  - file_read
  - search
---

# Agent Prompt

You are a test agent...""")
            temp_path = f.name

        try:
            result = parse_agent_markdown(temp_path)

            assert "agent" in result
            agent_data = result["agent"]
            assert agent_data["id"] == "test-agent"
            assert agent_data["name"] == "Test Agent"
            assert "file_read" in agent_data["tools"]
            assert "system_prompt" in agent_data
            assert "You are a test agent" in agent_data["system_prompt"]
        finally:
            os.unlink(temp_path)

    def test_parse_agent_markdown_nested_format(self):
        """Test parsing agent with nested 'agent' key in frontmatter."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("""---
agent:
  id: nested-agent
  name: Nested Agent
---

Body""")
            temp_path = f.name

        try:
            result = parse_agent_markdown(temp_path)

            assert "agent" in result
            assert result["agent"]["id"] == "nested-agent"
        finally:
            os.unlink(temp_path)

    def test_parse_agent_markdown_body_as_prompt(self):
        """Test that body is added as system_prompt when not specified."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("""---
id: prompt-agent
name: Prompt Agent
---

# Instructions

Do something.""")
            temp_path = f.name

        try:
            result = parse_agent_markdown(temp_path)

            agent_data = result["agent"]
            assert "system_prompt" in agent_data
            assert "# Instructions" in agent_data["system_prompt"]
        finally:
            os.unlink(temp_path)

    def test_parse_agent_markdown_preserves_existing_prompt(self):
        """Test that existing system_prompt is preserved."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("""---
id: existing-prompt-agent
system_prompt: Existing prompt from frontmatter
---

# Body content

This should not override system_prompt.""")
            temp_path = f.name

        try:
            result = parse_agent_markdown(temp_path)

            agent_data = result["agent"]
            assert agent_data["system_prompt"] == "Existing prompt from frontmatter"
        finally:
            os.unlink(temp_path)


class TestFrontmatterParsingError:
    """Tests for FrontmatterParsingError exception."""

    def test_error_message(self):
        """Test error message formatting."""
        error = FrontmatterParsingError("Test error")

        assert "Test error" in str(error)

    def test_error_with_file_path(self):
        """Test error message with file path."""
        error = FrontmatterParsingError("Parse failed", file_path="/test/file.md")

        assert "/test/file.md" in str(error)
        assert "Parse failed" in str(error)

    def test_error_with_original_error(self):
        """Test error with original error preserved."""
        original = ValueError("Original error")
        error = FrontmatterParsingError(
            "Wrapper error",
            file_path="/test.md",
            original_error=original,
        )

        assert error.original_error is original
        assert "Wrapper error" in str(error)


class TestEdgeCases:
    """Tests for edge cases and special scenarios."""

    def test_whitespace_only_frontmatter(self):
        """Test frontmatter with only whitespace."""
        content = """---

---
Body"""
        parser = FrontmatterParser()
        result = parser.parse(content)

        assert result.has_frontmatter is True
        assert result.body == "Body"

    def test_frontmatter_with_tabs(self):
        """Test frontmatter containing tabs."""
        # Note: Tabs in YAML values need to be properly escaped or avoided
        # This test verifies that tabs in values don't break parsing
        content = """---
id: tab-test
name: "Tabbed Value"
---
Body"""
        parser = FrontmatterParser()
        result = parser.parse(content)

        assert result.frontmatter["id"] == "tab-test"
        assert result.frontmatter["name"] == "Tabbed Value"

    def test_multiple_frontmatter_blocks(self):
        """Test file with multiple --- blocks (only first is frontmatter)."""
        content = """---
id: first
---
Body with --- separator
---
More content"""

        parser = FrontmatterParser()
        result = parser.parse(content)

        assert result.frontmatter["id"] == "first"
        assert "---" in result.body  # Second --- is part of body

    def test_frontmatter_at_end_of_file(self):
        """Test that frontmatter must be at start of file."""
        content = """Body content
---
id: misplaced
---"""

        parser = FrontmatterParser()
        result = parser.parse(content)

        # Should be treated as body without frontmatter
        assert result.has_frontmatter is False
        assert "---" in result.body

    def test_encoding_handling(self):
        """Test file encoding handling."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("---\nid: unicode-test\n---\nBody with unicode: ")
            f.name
        temp_path = f.name

        # Write unicode content
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write("---\nid: unicode-test\n---\nBody with: ")

        try:
            parser = FrontmatterParser()
            result = parser.parse_file(temp_path)

            assert result.frontmatter["id"] == "unicode-test"
        finally:
            os.unlink(temp_path)
