"""Extract code blocks from LLM responses and write them to disk."""

import re
from pathlib import Path
from typing import Dict, List, Tuple
import logging

logger = logging.getLogger(__name__)


def extract_code_blocks(text: str) -> List[Tuple[str, str, str]]:
    """
    Extract fenced code blocks from LLM response text.

    Looks for patterns like:
        ```python filename=app.py
        code here
        ```
    Or:
        ```python
        # app.py
        code here
        ```
    Or bare:
        ```python
        code here
        ```

    Returns list of (language, filename, content) tuples.
    """
    blocks = []
    # Pattern: ```lang filename=X or ```lang (file: X) or ```lang
    pattern = r"```(\w+)?(?:\s+(?:filename=|file:\s*)([^\n]+))?\n(.*?)```"

    for match in re.finditer(pattern, text, re.DOTALL):
        lang = match.group(1) or "txt"
        filename = match.group(2)
        content = match.group(3).strip()

        if not filename:
            # Try to infer filename from first comment line
            first_line = content.split("\n")[0].strip()
            if first_line.startswith("# ") and "." in first_line[2:]:
                candidate = first_line[2:].strip()
                if "/" not in candidate and len(candidate) < 50:
                    filename = candidate

        if not filename:
            # Generate filename from language
            ext_map = {
                "python": ".py",
                "py": ".py",
                "javascript": ".js",
                "js": ".js",
                "typescript": ".ts",
                "ts": ".ts",
                "html": ".html",
                "css": ".css",
                "json": ".json",
                "yaml": ".yaml",
                "yml": ".yaml",
                "bash": ".sh",
                "sh": ".sh",
                "sql": ".sql",
                "txt": ".txt",
            }
            ext = ext_map.get(lang.lower(), f".{lang}")
            filename = f"generated_{len(blocks) + 1}{ext}"

        blocks.append((lang, filename.strip(), content))

    return blocks


def write_code_files(
    artifacts: Dict[str, str],
    output_dir: str,
    create_dir: bool = True,
) -> List[str]:
    """
    Extract code blocks from pipeline artifacts and write them to disk.

    Args:
        artifacts: Dict of artifact_name -> artifact_text from pipeline
        output_dir: Directory to write code files to
        create_dir: Whether to create the directory if it doesn't exist

    Returns:
        List of file paths that were written
    """
    out_path = Path(output_dir)
    workspace = out_path / "workspace"
    if create_dir:
        workspace.mkdir(parents=True, exist_ok=True)

    written_files = []

    for artifact_name, artifact_text in artifacts.items():
        if not isinstance(artifact_text, str):
            continue

        blocks = extract_code_blocks(artifact_text)

        if blocks:
            for lang, filename, content in blocks:
                file_path = workspace / filename
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(content, encoding="utf-8")
                written_files.append(str(file_path))
                logger.info(f"Wrote {file_path} ({len(content)} bytes)")
        else:
            # No code blocks found -- save the raw artifact text as a file
            # Only for work-product artifacts (plan_* or code_*)
            if artifact_name.startswith("plan_") or artifact_name.startswith(
                "code_"
            ):
                safe_name = artifact_name.replace("/", "_").replace("\\", "_")
                file_path = workspace / f"{safe_name}.txt"
                file_path.write_text(artifact_text, encoding="utf-8")
                written_files.append(str(file_path))
                logger.info(f"Wrote raw artifact to {file_path}")

    return written_files
