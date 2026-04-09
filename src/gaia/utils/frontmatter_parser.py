"""
Markdown Frontmatter Parser for GAIA Agent Definitions

This module provides utilities for parsing Markdown files with YAML frontmatter,
used for agent definitions in the multi-stage pipeline system.

Example Markdown file with frontmatter:
```markdown
---
id: analytical-thinker
name: Analytical Thinker
description: Domain analysis specialist
tools: [rag, file_search, mcp]
---

# Agent Prompt

You are an analytical thinker...
```

The parser extracts both the frontmatter (for machine parsing) and the body
(for prompts and human-readable documentation).
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


@dataclass
class ParsedMarkdown:
    """Result of parsing a Markdown file with frontmatter."""

    # Raw content
    raw_content: str

    # Frontmatter (YAML metadata)
    frontmatter: Dict[str, Any]

    # Body (Markdown content after frontmatter)
    body: str

    # File information
    file_path: Optional[str] = None

    # Parsing metadata
    has_frontmatter: bool = True
    frontmatter_format: str = "yaml"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "frontmatter": self.frontmatter,
            "body": self.body,
            "file_path": self.file_path,
            "has_frontmatter": self.has_frontmatter,
            "frontmatter_format": self.frontmatter_format,
        }


class FrontmatterParser:
    """
    Parser for Markdown files with YAML frontmatter.

    The parser extracts YAML frontmatter delimited by `---` markers
    at the beginning of the file, and separates it from the Markdown body.

    Example:
        >>> parser = FrontmatterParser()
        >>> content = '''---
        ... id: test-agent
        ... name: Test Agent
        ... ---
        ... # Prompt
        ... You are a test agent...'''
        >>> result = parser.parse(content)
        >>> result.frontmatter["id"]
        'test-agent'
        >>> result.body.startswith('# Prompt')
        True
    """

    # Pattern to match frontmatter delimiters
    FRONTMATTER_DELIMITER = "---"

    # Regex pattern for frontmatter extraction
    FRONTMATTER_PATTERN = re.compile(
        r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL | re.MULTILINE
    )

    def __init__(self, require_frontmatter: bool = False):
        """
        Initialize parser.

        Args:
            require_frontmatter: If True, raise error if no frontmatter found
        """
        self.require_frontmatter = require_frontmatter

        if yaml is None:
            raise ImportError(
                "PyYAML is required for frontmatter parsing. "
                "Install with: pip install pyyaml"
            )

    def parse(
        self, content: str, file_path: Optional[str] = None
    ) -> ParsedMarkdown:
        """
        Parse Markdown content with frontmatter.

        Args:
            content: Markdown content to parse
            file_path: Optional file path for metadata

        Returns:
            ParsedMarkdown with frontmatter and body

        Raises:
            FrontmatterParsingError: If parsing fails and require_frontmatter is True
        """
        content = content.strip()

        # Try to extract frontmatter
        match = self.FRONTMATTER_PATTERN.match(content)

        if match:
            frontmatter_str = match.group(1)
            body_start = match.end()
            body = content[body_start:].strip()

            try:
                frontmatter = yaml.safe_load(frontmatter_str) or {}
            except yaml.YAMLError as e:
                raise FrontmatterParsingError(
                    f"Failed to parse YAML frontmatter: {e}",
                    file_path=file_path,
                )

            return ParsedMarkdown(
                raw_content=content,
                frontmatter=frontmatter,
                body=body,
                file_path=file_path,
                has_frontmatter=True,
                frontmatter_format="yaml",
            )

        # No frontmatter found
        if self.require_frontmatter:
            raise FrontmatterParsingError(
                "No frontmatter found in file",
                file_path=file_path,
            )

        # Return content as body with empty frontmatter
        return ParsedMarkdown(
            raw_content=content,
            frontmatter={},
            body=content,
            file_path=file_path,
            has_frontmatter=False,
            frontmatter_format="none",
        )

    def parse_file(self, file_path: Union[str, Path]) -> ParsedMarkdown:
        """
        Parse a Markdown file with frontmatter.

        Args:
            file_path: Path to Markdown file

        Returns:
            ParsedMarkdown with frontmatter and body

        Raises:
            FileNotFoundError: If file doesn't exist
            FrontmatterParsingError: If parsing fails
        """
        file_path = Path(file_path)

        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        return self.parse(content, file_path=str(file_path))

    def parse_file_safe(
        self, file_path: Union[str, Path], default_frontmatter: Optional[Dict] = None
    ) -> ParsedMarkdown:
        """
        Parse a Markdown file, returning defaults on failure.

        Args:
            file_path: Path to Markdown file
            default_frontmatter: Default frontmatter if parsing fails

        Returns:
            ParsedMarkdown with frontmatter and body
        """
        try:
            return self.parse_file(file_path)
        except (FrontmatterParsingError, yaml.YAMLError, FileNotFoundError):
            return ParsedMarkdown(
                raw_content="",
                frontmatter=default_frontmatter or {},
                body="",
                file_path=str(file_path),
                has_frontmatter=False,
                frontmatter_format="none",
            )


class FrontmatterParsingError(Exception):
    """Exception raised when frontmatter parsing fails."""

    def __init__(
        self,
        message: str,
        file_path: Optional[str] = None,
        original_error: Optional[Exception] = None,
    ):
        self.message = message
        self.file_path = file_path
        self.original_error = original_error

        error_msg = message
        if file_path:
            error_msg = f"Failed to parse {file_path}: {message}"

        super().__init__(error_msg)


# Convenience functions for common use cases

def parse_markdown_frontmatter(
    content: str, require_frontmatter: bool = False
) -> Tuple[Dict[str, Any], str]:
    """
    Parse Markdown frontmatter and return (frontmatter, body) tuple.

    This is a convenience function for simple parsing needs.

    Args:
        content: Markdown content to parse
        require_frontmatter: If True, raise error if no frontmatter

    Returns:
        Tuple of (frontmatter_dict, body_string)

    Raises:
        FrontmatterParsingError: If parsing fails and require_frontmatter is True

    Example:
        >>> content = '''---
        ... id: test
        ... name: Test Agent
        ... ---
        ... # Body content
        ... This is the body.'''
        >>> frontmatter, body = parse_markdown_frontmatter(content)
        >>> frontmatter["id"]
        'test'
        >>> "Body content" in body
        True
    """
    parser = FrontmatterParser(require_frontmatter=require_frontmatter)
    result = parser.parse(content)
    return result.frontmatter, result.body


def parse_agent_markdown(file_path: Union[str, Path]) -> Dict[str, Any]:
    """
    Parse an agent definition Markdown file.

    This function extracts the frontmatter and converts it to an
    AgentDefinition-compatible dictionary.

    Args:
        file_path: Path to agent Markdown file

    Returns:
        Dictionary suitable for AgentDefinition.from_dict()

    Example:
        >>> agent_data = parse_agent_markdown("agents/analytical_thinker.md")
        >>> agent_data["agent"]["id"]
        'analytical-thinker'
    """
    parser = FrontmatterParser()
    result = parser.parse_file(file_path)

    # Extract agent data from frontmatter
    frontmatter = result.frontmatter

    # Handle both direct and nested 'agent' key formats
    if "agent" not in frontmatter:
        # Wrap in 'agent' key for compatibility with AgentDefinition.from_dict()
        frontmatter = {"agent": frontmatter}

    # Add body as system_prompt if not already specified
    agent_data = frontmatter.get("agent", frontmatter)
    if "system_prompt" not in agent_data and result.body:
        agent_data["system_prompt"] = result.body

    return frontmatter


def extract_frontmatter_only(content: str) -> Dict[str, Any]:
    """
    Extract only the frontmatter from Markdown content.

    Args:
        content: Markdown content

    Returns:
        Frontmatter dictionary

    Example:
        >>> content = '''---
        ... id: test
        ... name: Test
        ... ---
        ... # Body'''
        >>> fm = extract_frontmatter_only(content)
        >>> fm["id"]
        'test'
    """
    parser = FrontmatterParser()
    result = parser.parse(content)
    return result.frontmatter


def extract_body_only(content: str) -> str:
    """
    Extract only the body from Markdown content.

    Args:
        content: Markdown content

    Returns:
        Body string (without frontmatter)

    Example:
        >>> content = '''---
        ... id: test
        ... ---
        ... # Body content'''
        >>> body = extract_body_only(content)
        >>> body.startswith('# Body content')
        True
    """
    parser = FrontmatterParser()
    result = parser.parse(content)
    return result.body


# Module-level parser instance for convenience
_default_parser: Optional[FrontmatterParser] = None


def get_parser(require_frontmatter: bool = False) -> FrontmatterParser:
    """
    Get or create a parser instance.

    Args:
        require_frontmatter: Whether to require frontmatter

    Returns:
        FrontmatterParser instance
    """
    global _default_parser
    if _default_parser is None or _default_parser.require_frontmatter != require_frontmatter:
        _default_parser = FrontmatterParser(require_frontmatter=require_frontmatter)
    return _default_parser


def parse(content: str, file_path: Optional[str] = None) -> ParsedMarkdown:
    """
    Parse Markdown content using the default parser.

    Args:
        content: Markdown content
        file_path: Optional file path

    Returns:
        ParsedMarkdown result
    """
    return get_parser().parse(content, file_path)


def parse_file(file_path: Union[str, Path]) -> ParsedMarkdown:
    """
    Parse a Markdown file using the default parser.

    Args:
        file_path: Path to Markdown file

    Returns:
        ParsedMarkdown result
    """
    return get_parser().parse_file(file_path)
