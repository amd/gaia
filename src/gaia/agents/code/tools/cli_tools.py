# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
CLI Tools Mixin for Code Agent.

Provides universal CLI command execution with background process management,
error detection, and graceful shutdown capabilities.
"""

import logging
import os
import platform
import re
import signal
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Thread
from typing import Any, Dict, List, Optional

import psutil

logger = logging.getLogger(__name__)

# CLI configuration constants
DEFAULT_PORT_RANGE_START = 3000
DEFAULT_PORT_RANGE_END = 9999
DEFAULT_COMMAND_TIMEOUT_SECS = 120
MAX_OUTPUT_CHARS = 10_000
MAX_ERROR_OUTPUT_CHARS = 2000


@dataclass
class ProcessInfo:
    """Information about a managed background process."""

    pid: int
    command: str
    working_dir: str
    log_file: str
    start_time: float
    port: Optional[int] = None
    errors: List[Dict[str, Any]] = None
    warnings: List[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []
        if self.warnings is None:
            self.warnings = []


# Error patterns to detect common startup issues
ERROR_PATTERNS = {
    "port_conflict": [
        r"Port (\d+) is (?:already )?in use",
        r"EADDRINUSE.*:(\d+)",
        r"address already in use.*:(\d+)",
        r"bind\(\) failed.*Address already in use",
        r"listen EADDRINUSE:.*:(\d+)",
    ],
    "missing_deps": [
        r"Cannot find module ['\"]([^'\"]+)['\"]",
        r"Module not found: Error: Can't resolve ['\"]([^'\"]+)['\"]",
        r"Error: Cannot find package ['\"]([^'\"]+)['\"]",
        r"ModuleNotFoundError: No module named ['\"]([^'\"]+)['\"]",
        r"ImportError: No module named ['\"]([^'\"]+)['\"]",
        r"command not found: (npm|node|yarn|pnpm|python|pip)",
    ],
    "compilation": [
        r"SyntaxError:",
        r"TypeError:",
        r"ReferenceError:",
        r"(\d+) error(?:s)?",
        r"Failed to compile",
        r"Build failed",
        r"ERROR in \./",
        r"TS\d+:",  # TypeScript errors
        r"Compilation failed",
    ],
    "permissions": [
        r"EACCES: permission denied",
        r"Permission denied",
        r"Access is denied",
        r"Operation not permitted",
        r"EPERM:",
    ],
    "resources": [
        r"JavaScript heap out of memory",
        r"ENOMEM",
        r"Cannot allocate memory",
        r"Out of memory",
    ],
    "network": [
        r"ECONNREFUSED",
        r"fetch failed",
        r"Network error",
        r"ETIMEDOUT",
        r"ENOTFOUND",
    ],
}


def find_process_on_port(port: int) -> Optional[int]:
    """
    Find the PID of the process using a specific port.

    Args:
        port: Port number to check

    Returns:
        PID of the process using the port, or None if port is free
    """
    try:
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                connections = proc.net_connections()
                for conn in connections:
                    if conn.laddr.port == port:
                        return proc.pid
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    except Exception as e:
        logger.warning(f"Error checking port {port}: {e}")
    return None


def kill_process_on_port(port: int) -> bool:
    """
    Kill any process using the specified port.

    Args:
        port: Port number to free

    Returns:
        True if a process was killed, False otherwise
    """
    pid = find_process_on_port(port)
    if pid:
        try:
            proc = psutil.Process(pid)
            proc.kill()
            logger.info(f"Killed process {proc.name()} (PID: {pid}) using port {port}")
            return True
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            logger.warning(f"Failed to kill process on port {port}: {e}")
    return False


def is_port_available(port: int, host: str = "localhost") -> bool:
    """
    Check if a port is available for use.

    Args:
        port: Port number to check
        host: Host address (default: localhost)

    Returns:
        True if port is available, False if in use
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, port))
            return True
    except OSError:
        return False


def find_available_port(
    start: int = DEFAULT_PORT_RANGE_START, end: int = DEFAULT_PORT_RANGE_END
) -> int:
    """
    Find the next available port in a range.

    Args:
        start: Starting port number
        end: Ending port number

    Returns:
        Next available port number

    Raises:
        RuntimeError: If no available ports found
    """
    for port in range(start, end + 1):
        if is_port_available(port):
            return port
    raise RuntimeError(f"No available ports found in range {start}-{end}")


class CLIToolsMixin:
    """
    Mixin providing unrestricted CLI command execution with process management.

    Designed for trusted developer tools - no security restrictions.
    Provides background process support with error detection and graceful shutdown.
    """

    def __init__(self, *args, **kwargs):
        """Initialize CLI tools with process tracking."""
        super().__init__(*args, **kwargs)

        self._ensure_cli_tools_initialized()

    def _ensure_cli_tools_initialized(self) -> None:
        """Ensure platform flags and process registries exist."""
        if getattr(self, "_cli_tools_initialized", False):
            return

        existing_platform = getattr(self, "platform", None)
        if isinstance(existing_platform, str):
            normalized_platform = existing_platform.lower()
        else:
            normalized_platform = platform.system().lower()

        self.platform = normalized_platform
        self.is_windows = normalized_platform == "windows"

        if not hasattr(self, "background_processes"):
            self.background_processes = {}  # type: Dict[int, ProcessInfo]
        if not hasattr(self, "port_registry"):
            self.port_registry = {}  # type: Dict[int, int]

        self._cli_tools_initialized = True

    def register_cli_tools(self) -> None:
        """Register CLI command execution tools."""
        self._ensure_cli_tools_initialized()
        from gaia.agents.base.tools import tool

        @tool(
            name="run_cli_command",
            description="Execute any CLI command (npm, python, docker, gh, etc.) with optional background execution for servers. "
            "Automatically detects startup errors and manages process lifecycle.",
            parameters={
                "command": {
                    "type": "str",
                    "description": "The command to execute (e.g., 'npm run dev', 'python app.py', 'docker ps')",
                    "required": True,
                },
                "working_dir": {
                    "type": "str",
                    "description": "Directory to execute command in (default: current directory)",
                    "required": False,
                },
                "background": {
                    "type": "bool",
                    "description": "Run as background process (for dev servers, long-running processes)",
                    "required": False,
                },
                "timeout": {
                    "type": "int",
                    "description": "Timeout in seconds for foreground commands (default: 120)",
                    "required": False,
                },
                "startup_timeout": {
                    "type": "int",
                    "description": "Timeout for startup error detection in background mode (default: 5)",
                    "required": False,
                },
                "expected_port": {
                    "type": "int",
                    "description": "Port the process should bind to (for verification)",
                    "required": False,
                },
                "auto_respond": {
                    "type": "str",
                    "description": "String to auto-respond to interactive prompts (default: 'y\\n')",
                    "required": False,
                },
            },
        )
        def run_cli_command(
            command: str,
            working_dir: str = ".",
            background: bool = False,
            timeout: Optional[int] = None,
            startup_timeout: int = 5,
            expected_port: Optional[int] = None,
            auto_respond: str = "y\n",
        ) -> Dict[str, Any]:
            """
            Execute any CLI command without restrictions.

            Args:
                command: Command to execute
                working_dir: Directory to run command in
                background: Run as background process
                timeout: Timeout for foreground commands (default: 120s)
                startup_timeout: Timeout for error detection (default: 5s)
                expected_port: Port process should bind to
                auto_respond: Response for interactive prompts

            Returns:
                Dict with execution status, output, errors, and suggestions
            """
            try:
                # Resolve working directory
                work_path = Path(working_dir).resolve()
                if not work_path.exists():
                    return {
                        "success": False,
                        "error": f"Working directory not found: {working_dir}",
                        "has_errors": True,
                    }

                if not work_path.is_dir():
                    return {
                        "success": False,
                        "error": f"Path is not a directory: {working_dir}",
                        "has_errors": True,
                    }

                # Set default timeout for foreground commands
                if timeout is None and not background:
                    timeout = DEFAULT_COMMAND_TIMEOUT_SECS

                # Detect port from command if not specified
                if expected_port is None:
                    expected_port = self._detect_port_from_command(command)

                # Check for port conflicts before starting
                if expected_port and not is_port_available(expected_port):
                    existing_pid = find_process_on_port(expected_port)
                    return {
                        "success": False,
                        "error": f"Port {expected_port} is already in use",
                        "port": expected_port,
                        "blocking_pid": existing_pid,
                        "has_errors": True,
                        "suggestions": [
                            f"Kill the process using port {expected_port}: PID {existing_pid}",
                            f"Use a different port: PORT={expected_port + 1} {command}",
                            f"Stop the process with: stop_process({existing_pid})",
                        ],
                    }

                if background:
                    return self._run_background_command(
                        command=command,
                        work_path=work_path,
                        startup_timeout=startup_timeout,
                        expected_port=expected_port,
                        auto_respond=auto_respond,
                    )
                else:
                    return self._run_foreground_command(
                        command=command,
                        work_path=work_path,
                        timeout=timeout,
                        auto_respond=auto_respond,
                    )

            except Exception as e:
                logger.error(f"Error executing CLI command: {e}")
                return {
                    "success": False,
                    "error": str(e),
                    "has_errors": True,
                }

        @tool(
            name="stop_process",
            description="Stop a background process gracefully (SIGINT/Ctrl+C), with optional force kill",
            parameters={
                "pid": {
                    "type": "int",
                    "description": "Process ID to stop",
                    "required": True,
                },
                "force": {
                    "type": "bool",
                    "description": "Force kill immediately without graceful shutdown",
                    "required": False,
                },
            },
        )
        def stop_process(pid: int, force: bool = False) -> Dict[str, Any]:
            """
            Stop a background process with graceful shutdown.

            Args:
                pid: Process ID to stop
                force: Skip graceful shutdown and force kill

            Returns:
                Dict with stop status and method used
            """
            return self._stop_process(pid, force)

        @tool(
            name="list_processes",
            description="List all managed background processes",
            parameters={},
        )
        def list_processes() -> Dict[str, Any]:
            """
            List all managed background processes.

            Returns:
                Dict with list of running processes and their info
            """
            return self._list_processes()

        @tool(
            name="get_process_logs",
            description="Get output logs from a background process",
            parameters={
                "pid": {
                    "type": "int",
                    "description": "Process ID to get logs for",
                    "required": True,
                },
                "lines": {
                    "type": "int",
                    "description": "Number of lines to return (default: 50)",
                    "required": False,
                },
            },
        )
        def get_process_logs(pid: int, lines: int = 50) -> Dict[str, Any]:
            """
            Get output logs from a background process.

            Args:
                pid: Process ID
                lines: Number of lines to return

            Returns:
                Dict with log output
            """
            return self._get_process_logs(pid, lines)

        @tool(
            name="cleanup_all_processes",
            description="Stop all managed background processes gracefully",
            parameters={},
        )
        def cleanup_all_processes() -> Dict[str, Any]:
            """
            Stop all managed background processes.

            Returns:
                Dict with cleanup status
            """
            return self._cleanup_all_processes()

    def _detect_port_from_command(self, command: str) -> Optional[int]:
        """
        Detect port number from common command patterns.

        Args:
            command: Command string

        Returns:
            Detected port number or None
        """
        # Common port patterns
        patterns = [
            r"PORT[=\s]+(\d+)",  # PORT=3000
            r"--port[=\s]+(\d+)",  # --port 3000
            r"-p\s+(\d+)",  # -p 3000
            r":(\d+)",  # localhost:3000
        ]

        for pattern in patterns:
            match = re.search(pattern, command, re.IGNORECASE)
            if match:
                return int(match.group(1))

        # Default ports for common commands
        if "npm run dev" in command or "next dev" in command:
            return 3000
        if "vite" in command.lower():
            return 5173
        if "flask run" in command:
            return 5000
        if "uvicorn" in command:
            return 8000

        return None

    def _run_foreground_command(
        self,
        command: str,
        work_path: Path,
        timeout: int,
        auto_respond: str,
    ) -> Dict[str, Any]:
        """
        Execute a command in foreground mode.

        Args:
            command: Command to execute
            work_path: Working directory
            timeout: Command timeout
            auto_respond: Auto-response for interactive prompts

        Returns:
            Dict with command output and status
        """
        try:
            logger.info(f"Executing foreground command: {command}")

            # Run command
            result = subprocess.run(
                command,
                cwd=str(work_path),
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                input=auto_respond if auto_respond else None,
                check=False,
            )

            # Truncate output if too long
            stdout = result.stdout or ""
            stderr = result.stderr or ""
            truncated = False
            max_output = MAX_OUTPUT_CHARS

            if len(stdout) > max_output:
                stdout = stdout[:max_output] + "\n...output truncated (stdout)..."
                truncated = True

            if len(stderr) > max_output:
                stderr = stderr[:max_output] + "\n...output truncated (stderr)..."
                truncated = True

            # Scan for errors
            errors = self._scan_output_for_errors(stdout + stderr)

            return {
                "success": result.returncode == 0,
                "command": command,
                "stdout": stdout,
                "stderr": stderr,
                "return_code": result.returncode,
                "has_errors": result.returncode != 0 or len(errors) > 0,
                "errors": errors,
                "suggestions": self._generate_error_suggestions(errors),
                "output_truncated": truncated,
                "working_dir": str(work_path),
            }

        except subprocess.TimeoutExpired as e:
            stdout_str = e.stdout.decode("utf-8") if e.stdout else ""
            stderr_str = e.stderr.decode("utf-8") if e.stderr else ""

            return {
                "success": False,
                "error": f"Command timed out after {timeout} seconds",
                "command": command,
                "stdout": stdout_str,
                "stderr": stderr_str,
                "has_errors": True,
                "timed_out": True,
                "timeout": timeout,
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "command": command,
                "has_errors": True,
            }

    def _run_background_command(
        self,
        command: str,
        work_path: Path,
        startup_timeout: int,
        expected_port: Optional[int],
        auto_respond: str,
    ) -> Dict[str, Any]:
        """
        Execute a command in background mode with error detection.

        Args:
            command: Command to execute
            work_path: Working directory
            startup_timeout: Time to monitor for errors
            expected_port: Expected port for the process
            auto_respond: Auto-response for interactive prompts

        Returns:
            Dict with process info or error details
        """
        self._ensure_cli_tools_initialized()
        try:
            logger.info(f"Starting background command: {command}")

            # Create log file
            log_file = tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".log",
                prefix="gaia_cli_",
                delete=False,
                encoding="utf-8",
            )

            # Start process
            if self.is_windows:
                # Windows: CREATE_NEW_PROCESS_GROUP for Ctrl+C support
                process = subprocess.Popen(
                    command,
                    cwd=str(work_path),
                    shell=True,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.PIPE if auto_respond else subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                )
            else:
                # Unix: start_new_session for signal isolation
                process = subprocess.Popen(
                    command,
                    cwd=str(work_path),
                    shell=True,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.PIPE if auto_respond else subprocess.DEVNULL,
                    start_new_session=True,
                )

            log_file.close()

            # Auto-respond to prompts in background thread
            if auto_respond:

                def send_input():
                    try:
                        time.sleep(0.5)  # Small delay for process to start
                        if process.stdin:
                            process.stdin.write(auto_respond.encode())
                            process.stdin.flush()
                            process.stdin.close()
                    except Exception as e:
                        logger.debug(f"Auto-respond thread error: {e}")

                Thread(target=send_input, daemon=True).start()

            # Monitor for startup errors
            start_time = time.time()
            errors_detected = []
            output_buffer = []

            logger.info(
                f"Monitoring process {process.pid} for {startup_timeout} seconds..."
            )

            while time.time() - start_time < startup_timeout:
                # Check if process died
                if process.poll() is not None:
                    errors_detected.append(
                        {
                            "type": "process_died",
                            "message": f"Process exited with code {process.returncode}",
                        }
                    )
                    break

                # Read recent output
                try:
                    with open(log_file.name, "r", encoding="utf-8") as f:
                        new_output = f.read()
                        if new_output and new_output != "".join(output_buffer):
                            output_buffer.append(new_output)
                            # Scan for errors
                            errors = self._scan_output_for_errors(new_output)
                            errors_detected.extend(errors)

                            # Stop if critical errors found
                            if any(
                                e["type"]
                                in ["port_conflict", "missing_deps", "permissions"]
                                for e in errors
                            ):
                                break

                except Exception as e:
                    logger.debug(f"Error reading log file: {e}")

                time.sleep(0.1)

            # Check if process is still alive
            is_alive = process.poll() is None

            # If errors detected or process died, stop it
            if errors_detected or not is_alive:
                if is_alive:
                    self._terminate_process(process.pid)

                # Read final output
                try:
                    with open(log_file.name, "r", encoding="utf-8") as f:
                        final_output = f.read()
                except Exception:
                    final_output = "".join(output_buffer)

                return {
                    "success": False,
                    "command": command,
                    "output": final_output[-MAX_ERROR_OUTPUT_CHARS:],
                    "errors": errors_detected,
                    "suggestions": self._generate_error_suggestions(errors_detected),
                    "has_errors": True,
                    "log_file": log_file.name,
                }

            # Check port is bound if expected
            if expected_port:
                port_ready = False
                for _ in range(10):  # Check for 1 second
                    if not is_port_available(expected_port):
                        port_ready = True
                        self.port_registry[expected_port] = process.pid
                        break
                    time.sleep(0.1)

                if not port_ready:
                    self._terminate_process(process.pid)
                    return {
                        "success": False,
                        "error": f"Process started but port {expected_port} not bound",
                        "has_errors": True,
                    }

            # Process started successfully!
            process_info = ProcessInfo(
                pid=process.pid,
                command=command,
                working_dir=str(work_path),
                log_file=log_file.name,
                start_time=time.time(),
                port=expected_port,
            )

            self.background_processes[process.pid] = process_info

            logger.info(f"Background process started successfully: PID {process.pid}")

            return {
                "success": True,
                "pid": process.pid,
                "command": command,
                "port": expected_port,
                "log_file": log_file.name,
                "background": True,
                "message": f"Process started with PID {process.pid}"
                + (f" on port {expected_port}" if expected_port else ""),
            }

        except Exception as e:
            logger.error(f"Error starting background command: {e}")
            return {
                "success": False,
                "error": str(e),
                "has_errors": True,
            }

    def _scan_output_for_errors(self, output: str) -> List[Dict[str, Any]]:
        """
        Scan output for known error patterns.

        Args:
            output: Command output to scan

        Returns:
            List of detected errors
        """
        detected_errors = []

        for error_type, patterns in ERROR_PATTERNS.items():
            for pattern in patterns:
                matches = re.finditer(pattern, output, re.IGNORECASE | re.MULTILINE)
                for match in matches:
                    error_info = {
                        "type": error_type,
                        "message": match.group(0),
                    }

                    # Extract specific details
                    if error_type == "port_conflict" and len(match.groups()) > 0:
                        error_info["port"] = int(match.group(1))
                    elif error_type == "missing_deps" and len(match.groups()) > 0:
                        error_info["module"] = match.group(1)

                    detected_errors.append(error_info)

        return detected_errors

    def _generate_error_suggestions(self, errors: List[Dict[str, Any]]) -> List[str]:
        """
        Generate helpful suggestions for detected errors.

        Args:
            errors: List of detected errors

        Returns:
            List of actionable suggestions
        """
        suggestions = []

        for error in errors:
            error_type = error.get("type")

            if error_type == "port_conflict":
                port = error.get("port", "N/A")
                suggestions.append(f"Port {port} is in use. Try a different port.")
                suggestions.append("Or kill the process: stop_process(<pid>)")

            elif error_type == "missing_deps":
                module = error.get("module", "unknown")
                suggestions.append(f"Install missing dependency: npm install {module}")
                suggestions.append("Or run: npm install to install all dependencies")

            elif error_type == "compilation":
                suggestions.append("Fix compilation errors in your code")
                suggestions.append("Check syntax and type errors")

            elif error_type == "permissions":
                suggestions.append("Check file permissions")
                suggestions.append("You may need elevated permissions")

            elif error_type == "resources":
                suggestions.append(
                    "Increase memory: NODE_OPTIONS=--max-old-space-size=4096"
                )

            elif error_type == "network":
                suggestions.append("Check network connectivity")
                suggestions.append("Verify URLs and API endpoints")

        return list(set(suggestions))  # Remove duplicates

    def _stop_process(self, pid: int, force: bool = False) -> Dict[str, Any]:
        """
        Stop a background process gracefully.

        Args:
            pid: Process ID
            force: Skip graceful shutdown

        Returns:
            Dict with stop status
        """
        self._ensure_cli_tools_initialized()
        if pid not in self.background_processes:
            return {
                "success": False,
                "error": f"Process {pid} not found in managed processes",
            }

        try:
            # Get process info
            proc_info = self.background_processes[pid]

            # Try to terminate
            method = "forced" if force else "graceful"
            self._terminate_process(pid, force=force)

            # Remove from tracking
            del self.background_processes[pid]

            # Remove port registry
            if proc_info.port and proc_info.port in self.port_registry:
                del self.port_registry[proc_info.port]

            logger.info(f"Stopped process {pid} ({method})")

            return {
                "success": True,
                "pid": pid,
                "method": method,
                "message": f"Process {pid} stopped successfully",
            }

        except Exception as e:
            logger.error(f"Error stopping process {pid}: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    def _terminate_process(self, pid: int, force: bool = False):
        """
        Terminate a process using platform-specific methods.

        Args:
            pid: Process ID to terminate
            force: Skip graceful termination
        """
        self._ensure_cli_tools_initialized()
        try:
            proc = psutil.Process(pid)

            if self.is_windows:
                if force:
                    # Force kill with taskkill
                    os.system(f"taskkill /F /T /PID {pid}")
                else:
                    # Try graceful termination first
                    try:
                        # pylint: disable=no-member
                        proc.send_signal(signal.CTRL_C_EVENT)
                        proc.wait(timeout=5)
                    except (psutil.TimeoutExpired, psutil.NoSuchProcess):
                        # Fallback to taskkill
                        os.system(f"taskkill /F /T /PID {pid}")
            else:
                # Unix/Linux/macOS
                if not force:
                    # Send SIGINT (Ctrl+C)
                    try:
                        os.kill(pid, signal.SIGINT)
                        proc.wait(timeout=5)
                        return  # Success!
                    except (psutil.TimeoutExpired, ProcessLookupError):
                        pass  # Continue to SIGTERM

                    # Send SIGTERM (Unix only - killpg not available on Windows)
                    if hasattr(os, "killpg"):
                        try:
                            os.killpg(os.getpgid(pid), signal.SIGTERM)
                            proc.wait(timeout=2)
                            return  # Success!
                        except (OSError, ProcessLookupError, psutil.TimeoutExpired):
                            pass  # Continue to SIGKILL

                # Force kill with SIGKILL (Unix) or terminate (Windows)
                if hasattr(os, "killpg") and hasattr(signal, "SIGKILL"):
                    try:
                        # pylint: disable=no-member
                        os.killpg(os.getpgid(pid), signal.SIGTERM)
                        proc.wait(timeout=2)
                        return  # Success!
                    except (OSError, ProcessLookupError, psutil.TimeoutExpired):
                        pass  # Continue to SIGKILL

                # Force kill with SIGKILL
                try:
                    # pylint: disable=no-member
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    # Fallback to individual kill
                    try:
                        proc.kill()
                    except psutil.NoSuchProcess:
                        pass  # Already dead

        except psutil.NoSuchProcess:
            logger.debug(f"Process {pid} already terminated")
        except Exception as e:
            logger.warning(f"Error terminating process {pid}: {e}")

    def _list_processes(self) -> Dict[str, Any]:
        """
        List all managed background processes.

        Returns:
            Dict with process list
        """
        self._ensure_cli_tools_initialized()
        processes = []

        for pid, info in self.background_processes.items():
            # Check if process is still running
            try:
                proc = psutil.Process(pid)
                is_running = proc.is_running()
            except psutil.NoSuchProcess:
                is_running = False

            processes.append(
                {
                    "pid": pid,
                    "command": info.command,
                    "working_dir": info.working_dir,
                    "port": info.port,
                    "running": is_running,
                    "runtime_seconds": time.time() - info.start_time,
                    "log_file": info.log_file,
                }
            )

        return {
            "success": True,
            "count": len(processes),
            "processes": processes,
        }

    def _get_process_logs(self, pid: int, lines: int = 50) -> Dict[str, Any]:
        """
        Get output logs from a background process.

        Args:
            pid: Process ID
            lines: Number of lines to return

        Returns:
            Dict with log output
        """
        self._ensure_cli_tools_initialized()
        if pid not in self.background_processes:
            return {
                "success": False,
                "error": f"Process {pid} not found",
            }

        proc_info = self.background_processes[pid]

        try:
            with open(proc_info.log_file, "r", encoding="utf-8") as f:
                all_lines = f.readlines()
                recent_lines = (
                    all_lines[-lines:] if len(all_lines) > lines else all_lines
                )

            return {
                "success": True,
                "pid": pid,
                "lines": len(recent_lines),
                "output": "".join(recent_lines),
                "log_file": proc_info.log_file,
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
            }

    def _cleanup_all_processes(self) -> Dict[str, Any]:
        """
        Stop all managed background processes.

        Returns:
            Dict with cleanup status
        """
        self._ensure_cli_tools_initialized()
        pids = list(self.background_processes.keys())
        stopped = []
        failed = []

        for pid in pids:
            result = self._stop_process(pid, force=False)
            if result.get("success"):
                stopped.append(pid)
            else:
                failed.append(pid)

        return {
            "success": len(failed) == 0,
            "stopped": stopped,
            "failed": failed,
            "message": f"Stopped {len(stopped)} processes, {len(failed)} failures",
        }

    def __del__(self):
        """Cleanup all processes on deletion."""
        if hasattr(self, "background_processes"):
            try:
                self._cleanup_all_processes()
            except Exception as e:
                logger.debug(f"Error cleaning up processes in __del__: {e}")
