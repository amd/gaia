"""Resolve relative tool paths against the active chat workspace."""

from pathlib import Path
from typing import Any, Optional


def _resolve_target(
    file_path: str,
    project_dir: Optional[str] = None,
    default_dir: Optional[str] = None,
) -> Path:
    """Resolve a path using an explicit project or session workspace base."""
    path = Path(file_path).expanduser()
    if not path.is_absolute():
        base = project_dir or default_dir
        if base:
            path = Path(base).resolve() / path
    return path.resolve()


def _workspace_default(host: Any) -> Optional[str]:
    """Return the host's explicit session workspace, if it has one.

    Allowed paths describe where a tool may go; they do not define a default
    directory.  Keeping this resolver shared prevents file, shell, search, and
    prompt code from silently choosing different relative-path bases.
    """
    config = getattr(host, "config", None)
    workspace = getattr(config, "working_directory", None) or getattr(
        host, "working_directory", None
    )
    if not workspace:
        return None
    return str(Path(workspace).expanduser().resolve())


def _project_target_for(host: Any, args: dict) -> Path:
    return _resolve_target(
        args["file_path"],
        args.get("project_dir"),
        _workspace_default(host),
    )


def _workspace_target(host: Any, path: str) -> Path:
    """Resolve a tool path relative to the active chat workspace."""
    return _resolve_target(path, None, _workspace_default(host))
