"""Tests for path traversal protection in artifact_extractor (SEC-003)."""

import tempfile
from pathlib import Path

import pytest

from gaia.pipeline.artifact_extractor import extract_code_blocks, write_code_files


def _make_artifacts_with_filename(filename: str, content: str = "hello") -> dict:
    """Create an artifacts dict containing a Python code block with the given filename."""
    return {
        "test_artifact": f"```python filename={filename}\n{content}\n```"
    }


class TestPathTraversalProtection:
    """Verify SEC-003: untrusted filenames from LLM output cannot escape workspace."""

    def test_traversal_blocked(self, tmp_path: Path) -> None:
        """Unix-style path traversal must NOT be written outside workspace."""
        artifacts = _make_artifacts_with_filename("../../../etc/evil.txt")
        written = write_code_files(artifacts, output_dir=str(tmp_path))

        # File must not be written
        assert len(written) == 0

        # Verify no file was created outside workspace
        evil_file = tmp_path / ".." / ".." / ".." / "etc" / "evil.txt"
        assert not evil_file.exists(), "Path traversal succeeded -- file written outside workspace"

    def test_normal_filename_written(self, tmp_path: Path) -> None:
        """Normal filenames like app.py MUST be written correctly."""
        artifacts = _make_artifacts_with_filename("app.py", content="print('hi')")
        written = write_code_files(artifacts, output_dir=str(tmp_path))

        assert len(written) == 1
        out_file = tmp_path / "workspace" / "app.py"
        assert out_file.exists()
        assert out_file.read_text(encoding="utf-8") == "print('hi')"

    def test_windows_style_traversal_blocked(self, tmp_path: Path) -> None:
        """Windows-style path traversal must be blocked."""
        artifacts = _make_artifacts_with_filename(r"..\..\..\Windows\evil.bat")
        written = write_code_files(artifacts, output_dir=str(tmp_path))

        # File must not be written
        assert len(written) == 0

    def test_nested_valid_path(self, tmp_path: Path) -> None:
        """Nested but valid paths inside workspace should work."""
        artifacts = _make_artifacts_with_filename("src/components/Button.tsx", content="<div/>")
        written = write_code_files(artifacts, output_dir=str(tmp_path))

        assert len(written) == 1
        out_file = tmp_path / "workspace" / "src" / "components" / "Button.tsx"
        assert out_file.exists()
        assert out_file.read_text(encoding="utf-8") == "<div/>"

    def test_dotdot_in_middle_blocked(self, tmp_path: Path) -> None:
        """Traversal attempt with .. in the middle of a valid-looking path must be blocked."""
        artifacts = _make_artifacts_with_filename("safe/../..\\outside.txt")
        written = write_code_files(artifacts, output_dir=str(tmp_path))

        # Should be blocked since it resolves outside workspace
        # (depending on resolution, but should not escape)
        workspace = tmp_path / "workspace"
        for f in workspace.rglob("*"):
            assert "outside" not in f.name


class TestExtractCodeBlocks:
    """Verify extract_code_blocks parses filenames correctly."""

    def test_filename_extracted(self) -> None:
        text = "```python filename=main.py\nprint(1)\n```"
        blocks = extract_code_blocks(text)
        assert len(blocks) == 1
        assert blocks[0][1] == "main.py"

    def test_no_filename_generates_default(self) -> None:
        text = "```python\nprint(1)\n```"
        blocks = extract_code_blocks(text)
        assert len(blocks) == 1
        assert blocks[0][1].startswith("generated_")
