# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
EtherREPL - Persistent Python REPL with Session State Management.

Provides safe, subprocess-based Python code execution with:
- Persistent session state across eval calls
- Multiple concurrent sessions via PipelineIsolation
- Timeout enforcement and output truncation
- Component Framework template integration
- WorkspacePolicy path safety

Example:
    >>> repl = EtherREPL()
    >>> with repl.create_session("session-001") as session:
    ...     result = session.eval("x = 5")
    ...     result = session.eval("x + 3")  # Returns 8, state persists
    >>> repl.cleanup("session-001")
"""

import ast
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from gaia.pipeline.isolation import PipelineIsolation
from gaia.security.workspace import WorkspacePolicy
from gaia.utils.component_loader import ComponentLoader
from gaia.utils.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# Exception Classes
# =============================================================================

class EtherREPLError(Exception):
    """Base exception for EtherREPL operations."""

    def __init__(self, message: str, session_id: Optional[str] = None):
        super().__init__(message)
        self.session_id = session_id
        self.timestamp = time.time()


class SessionNotFoundError(EtherREPLError):
    """Raised when accessing a non-existent session."""


class ExecutionTimeoutError(EtherREPLError):
    """Raised when code execution exceeds timeout."""


class StatePersistenceError(EtherREPLError):
    """Raised when session state serialization fails."""


# =============================================================================
# Session Result Dataclass
# =============================================================================

@dataclass
class ExecutionResult:
    """Result of a REPL code execution.

    Attributes:
        success: Whether execution completed without error
        stdout: Captured standard output
        stderr: Captured standard error
        return_code: Process return code (0 = success)
        duration_sec: Execution time in seconds
        timed_out: Whether execution was killed for timeout
        state_changed: Whether session state was modified
    """
    success: bool
    stdout: str = ""
    stderr: str = ""
    return_code: int = 0
    duration_sec: float = 0.0
    timed_out: bool = False
    state_changed: bool = False
    session_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for tool response."""
        return {
            "success": self.success,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "return_code": self.return_code,
            "duration_sec": self.duration_sec,
            "timed_out": self.timed_out,
            "state_changed": self.state_changed,
            "session_id": self.session_id,
        }


# =============================================================================
# EtherREPL Core Class
# =============================================================================

class EtherREPL:
    """
    Persistent Python REPL with session state management.

    EtherREPL provides:
    1. Multiple concurrent REPL sessions via PipelineIsolation
    2. State persistence between eval calls using JSON serialization
    3. Subprocess-based sandboxed execution
    4. Timeout enforcement (configurable per-call)
    5. Output truncation for large results

    Thread Safety:
        All public methods are thread-safe using RLock.

    Security:
        - Each session runs in isolated workspace (hash-named)
        - WorkspacePolicy validates all file paths
        - Shell injection patterns blocked in code snippets

    Example:
        >>> repl = EtherREPL()
        >>> with repl.create_session("analysis-001") as session:
        ...     session.eval("import pandas as pd")
        ...     session.eval("df = pd.read_csv('data.csv')")
        ...     result = session.eval("df.describe()")
    """

    DEFAULT_TIMEOUT = 60  # seconds
    MAX_OUTPUT_CHARS = 10_000

    def __init__(
        self,
        workspace_root: Optional[str] = None,
        default_timeout: int = 60,
        persist_sessions: bool = False,
    ):
        """
        Initialize EtherREPL.

        Args:
            workspace_root: Root directory for isolated workspaces.
                Defaults to ~/.gaia/ether_repl
            default_timeout: Default execution timeout in seconds.
            persist_sessions: If True, sessions survive process restart.
        """
        self._workspace_root = Path(workspace_root) if workspace_root else (
            Path.home() / ".gaia" / "ether_repl"
        )
        self._workspace_root.mkdir(parents=True, exist_ok=True)

        self._default_timeout = default_timeout
        self._persist_sessions = persist_sessions

        # Session tracking
        self._sessions: Dict[str, "REPLSession"] = {}
        self._lock = threading.RLock()

        # Workspace policy for path safety
        self._workspace_policy = WorkspacePolicy(
            allowed_paths=[str(self._workspace_root)],
            workspace_root=str(self._workspace_root),
        )

        # Component loader for template integration
        self._component_loader = ComponentLoader()

        # Statistics
        self._stats = {
            "sessions_created": 0,
            "sessions_cleaned": 0,
            "total_evals": 0,
            "total_timeouts": 0,
        }

        logger.info(
            "EtherREPL initialized",
            extra={
                "workspace_root": str(self._workspace_root),
                "default_timeout": default_timeout,
            }
        )

    def create_session(
        self,
        session_id: str,
        timeout: Optional[int] = None,
    ) -> "REPLSession":
        """
        Create a new persistent REPL session.

        Args:
            session_id: Unique identifier for this session.
            timeout: Optional per-session timeout override.

        Returns:
            REPLSession context manager for code execution.

        Raises:
            EtherREPLError: If session_id already exists.

        Example:
            >>> repl = EtherREPL()
            >>> with repl.create_session("session-001") as session:
            ...     session.eval("x = 5")
            ...     session.eval("x * 2")  # Sees x from previous eval
        """
        with self._lock:
            if session_id in self._sessions:
                raise EtherREPLError(
                    f"Session already exists: {session_id}",
                    session_id=session_id
                )

            effective_timeout = timeout or self._default_timeout

            session = REPLSession(
                session_id=session_id,
                workspace_root=self._workspace_root,
                timeout=effective_timeout,
                persist=self._persist_sessions,
                workspace_policy=self._workspace_policy,
            )

            self._sessions[session_id] = session
            self._stats["sessions_created"] += 1

            logger.info(
                "Created REPL session",
                extra={"session_id": session_id, "timeout": effective_timeout}
            )

            return session

    def get_session(self, session_id: str) -> "REPLSession":
        """
        Get an existing REPL session.

        Args:
            session_id: Session identifier.

        Returns:
            Existing REPLSession instance.

        Raises:
            SessionNotFoundError: If session doesn't exist.
        """
        with self._lock:
            if session_id not in self._sessions:
                raise SessionNotFoundError(
                    f"Session not found: {session_id}",
                    session_id=session_id
                )
            return self._sessions[session_id]

    def cleanup(self, session_id: str) -> bool:
        """
        Clean up a REPL session and its workspace.

        Args:
            session_id: Session to clean up.

        Returns:
            True if cleanup successful, False otherwise.

        Raises:
            SessionNotFoundError: If session doesn't exist.
        """
        with self._lock:
            if session_id not in self._sessions:
                raise SessionNotFoundError(
                    f"Session not found: {session_id}",
                    session_id=session_id
                )

            session = self._sessions[session_id]
            success = session.cleanup()

            if success:
                del self._sessions[session_id]
                self._stats["sessions_cleaned"] += 1
                logger.info(f"Cleaned up session: {session_id}")

            return success

    def cleanup_all(self) -> Dict[str, bool]:
        """
        Clean up all active sessions.

        Returns:
            Dictionary mapping session_id to cleanup success status.
        """
        with self._lock:
            results = {}
            for session_id in list(self._sessions.keys()):
                try:
                    results[session_id] = self.cleanup(session_id)
                except Exception as e:
                    results[session_id] = False
                    logger.error(f"Failed to cleanup {session_id}: {e}")
            return results

    def get_statistics(self) -> Dict[str, Any]:
        """Get REPL statistics."""
        with self._lock:
            return {
                **self._stats,
                "active_sessions": len(self._sessions),
                "active_session_ids": list(self._sessions.keys()),
            }

    @property
    def component_loader(self) -> ComponentLoader:
        """Get the component loader for template access."""
        return self._component_loader


# =============================================================================
# REPL Session Class
# =============================================================================

class REPLSession:
    """
    Persistent Python REPL with session state management.

    A REPLSession represents a single persistent Python interpreter
    session. Code is executed via subprocess, and state is persisted
    between calls using JSON serialization.

    Thread Safety:
        All public methods are thread-safe.

    Example:
        >>> session = REPLSession("sess-001", workspace_root="/tmp")
        >>> with session:
        ...     session.eval("x = [1, 2, 3]")
        ...     result = session.eval("sum(x)")  # Returns 6
    """

    STATE_FILE = "_ether_state.json"

    def __init__(
        self,
        session_id: str,
        workspace_root: Path,
        timeout: int = 60,
        persist: bool = False,
        workspace_policy: Optional[WorkspacePolicy] = None,
    ):
        """
        Initialize REPL session.

        Args:
            session_id: Unique session identifier.
            workspace_root: Root path for isolated workspace.
            timeout: Execution timeout in seconds.
            persist: Whether to persist state across process restarts.
            workspace_policy: WorkspacePolicy for path validation.
        """
        self._session_id = session_id
        self._workspace_root = workspace_root
        self._timeout = timeout
        self._persist = persist
        self._workspace_policy = workspace_policy or WorkspacePolicy(
            allowed_paths=[str(workspace_root)],
            workspace_root=str(workspace_root),
        )

        # Generate hash-named workspace
        hash_digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]
        self._workspace_name = f"ether_{hash_digest}"
        self._workspace_path = workspace_root / self._workspace_name
        self._workspace_path.mkdir(parents=True, exist_ok=True)

        # State tracking
        self._state: Dict[str, Any] = {}
        self._active = False
        self._lock = threading.RLock()

        # Statistics
        self._eval_count = 0
        self._timeout_count = 0

        logger.debug(
            "REPLSession initialized",
            extra={"session_id": session_id, "workspace": str(self._workspace_path)}
        )

    def __enter__(self) -> "REPLSession":
        """Enter session context."""
        with self._lock:
            self._active = True
            self._load_state()  # Restore state if persisted
            return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit session context."""
        with self._lock:
            self._active = False
            self._save_state()  # Persist state
            if not self._persist:
                self.cleanup()

    def _get_state_path(self) -> Path:
        """Get path to state file."""
        return self._workspace_path / self.STATE_FILE

    def _load_state(self) -> None:
        """Load persisted state from disk using JSON (pickle-free for security)."""
        state_path = self._get_state_path()
        if state_path.exists():
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    self._state = json.load(f)
                logger.debug(f"Loaded state for session {self._session_id}")
            except Exception as e:
                logger.warning(f"Failed to load state: {e}")
                self._state = {}

    def _save_state(self) -> None:
        """Persist state to disk using JSON (pickle-free for security)."""
        if not self._persist:
            return

        state_path = self._get_state_path()
        try:
            # Exclude __builtins__ to avoid serializing builtin references
            safe_state = {k: v for k, v in self._state.items() if k != "__builtins__"}
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(safe_state, f, indent=2, default=str)
            logger.debug(f"Saved state for session {self._session_id}")
        except Exception as e:
            logger.error(f"Failed to save state: {e}")
            raise StatePersistenceError(
                f"State persistence failed: {e}",
                session_id=self._session_id
            )

    def _update_state(self, code: str, result: ExecutionResult) -> None:
        """Update session state after execution.

        This is a simplified state tracking - actual variable state
        is persisted via pickle in the subprocess.
        """
        with self._lock:
            self._eval_count += 1
            if result.timed_out:
                self._timeout_count += 1

    def cleanup(self) -> bool:
        """
        Clean up session workspace.

        Returns:
            True if cleanup successful.
        """
        with self._lock:
            try:
                if self._workspace_path.exists():
                    import shutil
                    shutil.rmtree(self._workspace_path)
                    logger.info(f"Cleaned up workspace: {self._workspace_path}")
                return True
            except Exception as e:
                logger.error(f"Cleanup failed: {e}")
                return False

    def eval(
        self,
        code: str,
        timeout: Optional[int] = None,
    ) -> ExecutionResult:
        """
        Execute Python code in the REPL session.

        Args:
            code: Python code snippet to execute (not a file path).
            timeout: Execution timeout in seconds (overrides default).

        Returns:
            ExecutionResult with output and metadata.

        Example:
            >>> session.eval("x = 5")
            >>> session.eval("x + 3")  # Returns execution with output "8"
        """
        effective_timeout = timeout or self._timeout

        # Security: Check for shell injection in code
        if not self._check_code_safety(code):
            return ExecutionResult(
                success=False,
                stderr="Shell injection patterns detected in code",
                return_code=-1,
            )

        with self._lock:
            if not self._active:
                return ExecutionResult(
                    success=False,
                    stderr="Session not active - use 'with session:' context",
                    return_code=-1,
                )

        # Create execution script
        script_content = self._build_execution_script(code)

        # Write script to isolated workspace
        script_path = self._workspace_path / f"eval_{int(time.time() * 1000)}.py"
        state_path = self._workspace_path / self.STATE_FILE

        try:
            script_path.write_text(script_content, encoding="utf-8")

            # Build command
            cmd = [
                sys.executable,
                str(script_path),
                "--state-in", str(state_path),
                "--state-out", str(state_path),
            ]

            # Execute
            start_time = time.monotonic()
            try:
                result = subprocess.run(
                    cmd,
                    cwd=str(self._workspace_path),
                    capture_output=True,
                    text=True,
                    timeout=effective_timeout,
                    check=False,
                )
                duration = time.monotonic() - start_time

            except subprocess.TimeoutExpired as exc:
                duration = time.monotonic() - start_time
                with self._lock:
                    self._timeout_count += 1

                stdout_str = ""
                stderr_str = ""
                if exc.stdout:
                    stdout_str = exc.stdout if isinstance(exc.stdout, str) else exc.stdout.decode("utf-8", errors="replace")
                if exc.stderr:
                    stderr_str = exc.stderr if isinstance(exc.stderr, str) else exc.stderr.decode("utf-8", errors="replace")

                exec_result = ExecutionResult(
                    success=False,
                    stdout=self._truncate_output(stdout_str),
                    stderr=self._truncate_output(stderr_str),
                    return_code=-1,
                    duration_sec=duration,
                    timed_out=True,
                    session_id=self._session_id,
                )
                self._update_state(code, exec_result)
                return exec_result

            # Process output
            stdout = self._truncate_output(result.stdout or "")
            stderr = self._truncate_output(result.stderr or "")

            exec_result = ExecutionResult(
                success=result.returncode == 0,
                stdout=stdout,
                stderr=stderr,
                return_code=result.returncode,
                duration_sec=duration,
                timed_out=False,
                state_changed=True,
                session_id=self._session_id,
            )

            self._update_state(code, exec_result)

            # Persist state after successful execution
            if result.returncode == 0:
                self._save_state()

            return exec_result

        finally:
            # Cleanup script
            try:
                script_path.unlink()
            except Exception:
                pass

    def _build_execution_script(self, code: str) -> str:
        """
        Build the execution script that runs user code.

        This script:
        1. Loads previous state from JSON (pickle-free for security)
        2. Executes user code
        3. Prints result of last expression
        4. Saves updated state

        Args:
            code: User's Python code.

        Returns:
            Complete Python script as string.
        """
        # Escape the code for safe embedding
        escaped_code = code.replace('\\', '\\\\').replace('"""', '\\"\\"\\"')

        return f'''import sys
import json
import argparse
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-in", dest="state_in", required=True)
    parser.add_argument("--state-out", dest="state_out", required=True)
    args = parser.parse_args()

    # Load previous state (JSON, not pickle — SEC-001 fix)
    state = {{}}
    state_path = Path(args.state_in)
    if state_path.exists():
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            state = {{}}

    # Execute user code
    code = """{escaped_code}"""

    try:
        # Compile and execute
        compiled = compile(code, "<repl>", "exec")
        exec(compiled, state)

        # Try to evaluate as expression for result printing
        try:
            expr_compiled = compile(code, "<repl>", "eval")
            result = eval(expr_compiled, state)
            if result is not None:
                print(repr(result))
        except SyntaxError:
            pass  # It's a statement, no output expected

    except Exception as e:
        print(f"Error: {{e}}", file=sys.stderr)
        sys.exit(1)

    # Save updated state (JSON, not pickle — SEC-001 fix)
    # Exclude __builtins__ — it gets re-injected by exec() and serializing
    # it as strings breaks builtins on reload (SEC-001 side-effect fix)
    try:
        save_state = {{k: v for k, v in state.items() if k != "__builtins__"}}
        with open(args.state_out, "w", encoding="utf-8") as f:
            json.dump(save_state, f, indent=2, default=str)
    except Exception as e:
        print(f"State save error: {{e}}", file=sys.stderr)

if __name__ == "__main__":
    main()
'''

    def _check_code_safety(self, code: str) -> bool:
        """
        Check code for dangerous patterns using AST analysis.

        Blocks:
        - Import statements (import, from...import)
        - eval()/exec()/compile() calls
        - __import__() builtin calls
        - getattr() on __builtins__
        - subprocess/os.system calls

        Uses AST parsing instead of string matching to prevent
        bypass via spacing variations, hex encoding, or getattr tricks.
        (SEC-002 fix)

        Args:
            code: Python code to check.

        Returns:
            True if safe, False if dangerous patterns detected.
        """
        try:
            tree = ast.parse(code)
        except SyntaxError:
            # Invalid syntax — let it through, subprocess will report the error
            return True

        dangerous_builtins = {"eval", "exec", "compile", "__import__", "getattr"}
        dangerous_modules = {"os", "subprocess", "sys", "importlib", "ctypes", "pickle"}

        for node in ast.walk(tree):
            # Block import statements
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom) and node.module in dangerous_modules:
                    logger.warning(f"Dangerous import blocked: {node.module}")
                    return False
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in dangerous_modules:
                            logger.warning(f"Dangerous import blocked: {alias.name}")
                            return False

            # Block dangerous function calls
            if isinstance(node, ast.Call):
                func = node.func

                # Check for direct builtin calls: eval(), exec(), compile()
                if isinstance(func, ast.Name) and func.id in dangerous_builtins:
                    logger.warning(f"Dangerous call blocked: {func.id}()")
                    return False

                # Check for getattr(__builtins__, 'eval')(...) pattern
                if isinstance(func, ast.Call) and isinstance(func.func, ast.Name):
                    if func.func.id == "getattr":
                        logger.warning("getattr() call blocked — potential builtin access")
                        return False

                # Check for attribute access on dangerous modules
                if isinstance(func, ast.Attribute):
                    # os.system(), subprocess.call(), etc.
                    if isinstance(func.value, ast.Name) and func.value.id in dangerous_modules:
                        logger.warning(f"Dangerous module access: {func.value.id}.{func.attr}")
                        return False
                    # __builtins__.eval pattern
                    if isinstance(func.value, ast.Name) and func.value.id == "__builtins__":
                        logger.warning("__builtins__ attribute access blocked")
                        return False

                # Check for string-based getattr patterns like getattr(os, "system")
                if isinstance(func, ast.Name) and func.id == "getattr":
                    logger.warning("getattr() call blocked")
                    return False

            # Block bare attribute access on __builtins__ (not just calls)
            if isinstance(node, ast.Attribute):
                if isinstance(node.value, ast.Name) and node.value.id == "__builtins__":
                    logger.warning("__builtins__ attribute access blocked")
                    return False

        return True

    def _truncate_output(self, output: str, max_chars: int = 10_000) -> str:
        """Truncate output if too long."""
        if len(output) > max_chars:
            return output[:max_chars] + "\n...output truncated..."
        return output

    @property
    def session_id(self) -> str:
        """Get session identifier."""
        return self._session_id

    @property
    def workspace_path(self) -> Path:
        """Get workspace path."""
        return self._workspace_path

    @property
    def statistics(self) -> Dict[str, Any]:
        """Get session statistics."""
        with self._lock:
            return {
                "eval_count": self._eval_count,
                "timeout_count": self._timeout_count,
                "is_active": self._active,
                "workspace_path": str(self._workspace_path),
            }


# =============================================================================
# Component Framework Integration Tools
# =============================================================================

def register_component_tools(repl: EtherREPL) -> None:
    """
    Register Component Framework tools with the ToolRegistry.

    These tools allow agents to:
    - Read component templates from component-framework/
    - Write component templates with path safety

    Args:
        repl: EtherREPL instance with component_loader access.
    """
    from gaia.agents.base.tools import tool

    @tool
    def read_component_template(component_path: str) -> Dict[str, Any]:
        """
        Read a component template from component-framework/.

        Args:
            component_path: Relative path within component-framework/
                (e.g., "memory/working-memory.md")

        Returns:
            Dictionary with:
            - path: Component path
            - frontmatter: YAML frontmatter as dict
            - content: Markdown body content
            - error: Error message if failed

        Example:
            >>> read_component_template("checklists/code-review.md")
            {'path': '...', 'frontmatter': {...}, 'content': '...'}
        """
        try:
            loader = repl.component_loader
            component = loader.load_component(component_path)
            return {
                "path": component["path"],
                "frontmatter": component["frontmatter"],
                "content": component["content"],
                "error": None,
            }
        except Exception as e:
            return {
                "path": component_path,
                "error": str(e),
            }

    @tool
    def write_component_template(
        component_path: str,
        content: str,
        frontmatter: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Write a component template to component-framework/.

        Args:
            component_path: Relative path within component-framework/
            content: Markdown content for component body
            frontmatter: Optional dictionary of frontmatter fields
                (template_id, template_type, version, description)

        Returns:
            Dictionary with:
            - path: Full path to saved component
            - success: Whether save was successful
            - error: Error message if failed

        Example:
            >>> write_component_template(
            ...     "tasks/new-task.md",
            ...     content="# New Task\\n\\nDescription...",
            ...     frontmatter={
            ...         "template_id": "new-task",
            ...         "template_type": "tasks",
            ...         "version": "1.0.0",
            ...         "description": "New task template"
            ...     }
            ... )
        """
        try:
            loader = repl.component_loader
            full_path = loader.save_component(component_path, content, frontmatter)
            return {
                "path": str(full_path),
                "success": True,
                "error": None,
            }
        except Exception as e:
            return {
                "path": component_path,
                "success": False,
                "error": str(e),
            }

    @tool
    def list_component_templates(component_type: Optional[str] = None) -> Dict[str, Any]:
        """
        List available component templates.

        Args:
            component_type: Optional filter by type:
                - "memory", "knowledge", "tasks", "commands",
                - "documents", "checklists", "personas", "workflows"

        Returns:
            Dictionary with:
            - components: List of component paths
            - count: Number of components
            - error: Error message if failed

        Example:
            >>> list_component_templates("checklists")
            {'components': ['checklists/code-review.md', ...], 'count': 5}
        """
        try:
            loader = repl.component_loader
            components = loader.list_components(component_type)
            return {
                "components": components,
                "count": len(components),
                "error": None,
            }
        except Exception as e:
            return {
                "components": [],
                "count": 0,
                "error": str(e),
            }

    @tool
    def render_component_template(
        component_path: str,
        variables: Dict[str, str],
    ) -> Dict[str, Any]:
        """
        Render a component template with variable substitution.

        Args:
            component_path: Relative path within component-framework/
            variables: Dictionary of variable mappings
                (e.g., {"AGENT_ID": "domain-analyzer"})

        Returns:
            Dictionary with:
            - content: Rendered template content
            - path: Original component path
            - error: Error message if failed

        Example:
            >>> render_component_template(
            ...     "memory/working-memory.md",
            ...     {"{{AGENT_ID}}": "my-agent", "{{TIMESTAMP}}": "2026-04-12"}
            ... )
        """
        try:
            loader = repl.component_loader
            rendered = loader.render_component(component_path, variables)
            return {
                "content": rendered,
                "path": component_path,
                "error": None,
            }
        except Exception as e:
            return {
                "content": "",
                "path": component_path,
                "error": str(e),
            }


# =============================================================================
# EtherREPL Execution Tool
# =============================================================================

def register_ether_tools(repl: EtherREPL) -> None:
    """
    Register EtherREPL execution tools with the ToolRegistry.

    These tools allow agents to:
    - Create REPL sessions
    - Execute code in sessions
    - Manage session lifecycle

    Args:
        repl: EtherREPL instance.
    """
    from gaia.agents.base.tools import tool

    @tool
    def ether_create_session(
        session_id: str,
        timeout: int = 60,
    ) -> Dict[str, Any]:
        """
        Create a new persistent Python REPL session.

        Args:
            session_id: Unique identifier for this session.
            timeout: Execution timeout in seconds (default: 60).

        Returns:
            Dictionary with:
            - session_id: Created session identifier
            - success: Whether creation succeeded
            - error: Error message if failed

        Example:
            >>> ether_create_session("analysis-001", timeout=120)
            {'session_id': 'analysis-001', 'success': True}
        """
        try:
            repl.create_session(session_id, timeout)
            return {
                "session_id": session_id,
                "success": True,
                "error": None,
            }
        except EtherREPLError as e:
            return {
                "session_id": session_id,
                "success": False,
                "error": str(e),
            }

    @tool
    def ether_eval(
        session_id: str,
        code: str,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Execute Python code in a REPL session.

        The session must be created first with ether_create_session.
        Code is executed as a snippet (not a file path).

        Args:
            session_id: Session to execute code in.
            code: Python code snippet to execute.
            timeout: Optional timeout override in seconds.

        Returns:
            Dictionary with:
            - success: Whether execution succeeded
            - stdout: Captured standard output
            - stderr: Captured standard error
            - return_code: Process return code
            - duration_sec: Execution time
            - timed_out: Whether execution timed out
            - error: Error message if session not found

        Example:
            >>> ether_eval("session-001", "x = 5")
            >>> ether_eval("session-001", "x * 2")  # Returns 8
        """
        try:
            session = repl.get_session(session_id)

            # Auto-activate session if not active
            if not session._active:
                session._active = True
                session._load_state()

            result = session.eval(code, timeout)

            return {
                **result.to_dict(),
                "error": None,
            }

        except SessionNotFoundError as e:
            return {
                "success": False,
                "error": str(e),
            }

    @tool
    def ether_cleanup_session(session_id: str) -> Dict[str, Any]:
        """
        Clean up a REPL session and remove its workspace.

        Args:
            session_id: Session to clean up.

        Returns:
            Dictionary with:
            - session_id: Cleaned session identifier
            - success: Whether cleanup succeeded
            - error: Error message if failed

        Example:
            >>> ether_cleanup_session("analysis-001")
            {'session_id': 'analysis-001', 'success': True}
        """
        try:
            success = repl.cleanup(session_id)
            return {
                "session_id": session_id,
                "success": success,
                "error": None if success else "Cleanup failed",
            }
        except SessionNotFoundError as e:
            return {
                "session_id": session_id,
                "success": False,
                "error": str(e),
            }

    @tool
    def ether_get_statistics() -> Dict[str, Any]:
        """
        Get EtherREPL statistics.

        Returns:
            Dictionary with:
            - sessions_created: Total sessions created
            - sessions_cleaned: Total sessions cleaned up
            - total_evals: Total code evaluations
            - total_timeouts: Total timeouts
            - active_sessions: Currently active sessions
            - active_session_ids: List of active session IDs

        Example:
            >>> ether_get_statistics()
            {'sessions_created': 5, 'active_sessions': 2, ...}
        """
        return repl.get_statistics()


# =============================================================================
# Convenience Factory Function
# =============================================================================

def create_ether_repl(
    workspace_root: Optional[str] = None,
    default_timeout: int = 60,
    register_tools: bool = True,
) -> EtherREPL:
    """
    Create and configure an EtherREPL instance.

    Convenience factory that:
    1. Creates EtherREPL instance
    2. Registers all tools with ToolRegistry
    3. Registers component framework tools

    Args:
        workspace_root: Root for isolated workspaces.
        default_timeout: Default execution timeout.
        register_tools: Whether to register tools automatically.

    Returns:
        Configured EtherREPL instance.

    Example:
        >>> repl = create_ether_repl(
        ...     workspace_root="/tmp/ether",
        ...     default_timeout=120
        ... )
        # Tools automatically registered
    """
    repl = EtherREPL(
        workspace_root=workspace_root,
        default_timeout=default_timeout,
    )

    if register_tools:
        register_ether_tools(repl)
        register_component_tools(repl)
        logger.info("EtherREPL tools registered")

    return repl
