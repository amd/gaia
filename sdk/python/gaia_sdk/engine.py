# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The engine child process and the JSON Lines wire to it."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

#: The wire version this SDK speaks (see tests/fixtures/stdio/gaia_host_wire.json).
PROTOCOL = 1

ENGINE_ENV = "GAIA_ENGINE"
_BINARY = "gaiacpp.exe" if sys.platform == "win32" else "gaiacpp"


class EngineError(RuntimeError):
    """The engine refused a message, stopped, or broke the protocol.

    ``code`` is the engine's own error code when it sent one (``bad_init``,
    ``bad_turn``, ``bad_message``, ``engine_error``), else ``None``.
    """

    def __init__(self, message: str, code: Optional[str] = None):
        super().__init__(message)
        self.code = code


class AgentBusy(RuntimeError):
    """``run`` was called while another turn was running on the same agent."""


class EngineNotFound(EngineError):
    """No ``gaiacpp`` engine binary could be found to start."""


def find_engine(explicit: Optional[str] = None) -> List[str]:
    """The command that starts the engine.

    Looked for in this order: *explicit*, the ``GAIA_ENGINE`` environment
    variable, the binary packaged inside this wheel, then ``gaiacpp`` on PATH.
    """
    chosen = explicit or os.environ.get(ENGINE_ENV, "").strip()
    if chosen:
        if not Path(chosen).is_file():
            source = "engine=" if explicit else f"{ENGINE_ENV}="
            raise EngineNotFound(
                f"{source}{chosen!r} is not a file. Point it at the gaiacpp binary."
            )
        return [chosen]
    packaged = Path(__file__).parent / "bin" / _BINARY
    if packaged.is_file():
        return [str(packaged)]
    on_path = shutil.which("gaiacpp")
    if on_path:
        return [on_path]
    raise EngineNotFound(
        "The gaiacpp engine was not found. Install a gaia-sdk wheel built for "
        f"this platform, put gaiacpp on PATH, or set {ENGINE_ENV} to its path. "
        "Builds are attached to each GAIA release: "
        "https://github.com/amd/gaia/releases"
    )


class Engine:
    """One running engine. Not thread-safe: one turn at a time."""

    def __init__(
        self,
        command: Sequence[str],
        env: Optional[Dict[str, str]] = None,
    ):
        self._process = subprocess.Popen(  # pylint: disable=consider-using-with
            [*command, "--engine"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            # stderr is inherited: the engine's diagnostics belong in the host's log.
            env=env,
            encoding="utf-8",
            bufsize=1,
        )

    def send(self, message: Dict[str, Any]) -> None:
        if self._process.poll() is not None:
            raise EngineError(
                f"The engine has stopped (exit code {self._process.returncode})."
            )
        try:
            # NaN and Infinity are not JSON; the engine would refuse the line.
            self._process.stdin.write(json.dumps(message, allow_nan=False) + "\n")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise EngineError(f"The engine stopped reading: {e}") from e

    def receive(self) -> Dict[str, Any]:
        """The engine's next event. Raises when it sent an ``error`` or stopped."""
        line = self._process.stdout.readline()
        if not line:
            code = self._process.wait()
            raise EngineError(
                f"The engine closed the connection (exit code {code}) before "
                "finishing the exchange."
            )
        try:
            event = json.loads(line)
        except ValueError as e:
            raise EngineError(
                f"The engine wrote a line that is not JSON: {line[:200]!r}"
            ) from e
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise EngineError(f"The engine wrote an event with no type: {line[:200]!r}")
        if event["type"] == "error":
            raise EngineError(
                str(event.get("message") or "The engine reported an error."),
                code=event.get("code"),
            )
        return event

    def close(self) -> None:
        """Ask the engine to stop, and make sure it has."""
        if self._process.poll() is None:
            try:
                self.send({"type": "shutdown"})
                self._process.stdin.close()
                self._process.wait(timeout=5)
            except (EngineError, OSError, subprocess.TimeoutExpired):
                self._process.kill()
                self._process.wait()
        for stream in (self._process.stdin, self._process.stdout):
            if stream is not None and not stream.closed:
                stream.close()

    @property
    def running(self) -> bool:
        return self._process.poll() is None
