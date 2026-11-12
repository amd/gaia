# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The installer journeys' stand-in Lemonade, as a fixture that reads its log.

``installer/headless/fake_model_server.py`` answers like Lemonade over real
HTTP and writes down every request. This starts it for a test and turns that
log into things to assert on: which model GAIA asked it to pull, load or chat
with. Nothing about GAIA's client is mocked.

    with FakeLemonade(downloaded=["Gemma-4-E4B-it-GGUF"]) as server:
        monkeypatch.setenv("LEMONADE_BASE_URL", server.base_url)
        ...run GAIA code...
        assert server.downloads == []

It plays a PC whose only downloaded models are the ones named. Like the real
server it knows the built-in catalog, and unlike it, it refuses a model that
is not downloaded instead of fetching it; ``downloads`` lists those requests,
because on a user's PC each one is gigabytes they did not ask for.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

SERVER = (
    Path(__file__).resolve().parents[2]
    / "installer"
    / "headless"
    / "fake_model_server.py"
)
#: Endpoints that make the real server fetch a model it does not have.
_USES_A_MODEL = ("/load", "/chat/completions", "/completions", "/embeddings")
START_TIMEOUT_S = 60


def _server_module():
    spec = importlib.util.spec_from_file_location("fake_model_server", SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: The text of every chat reply the server gives.
ANSWER: str = _server_module().ANSWER


@dataclass
class Request:
    """One request the server received."""

    method: str
    path: str
    status: int
    model: Optional[str]


class FakeLemonade:
    """A running stand-in server. Start it with ``with``."""

    def __init__(self, downloaded: tuple = (), env: Optional[dict] = None):
        self.downloaded = tuple(downloaded)
        self._env = dict(env or {})
        self._dir: Optional[tempfile.TemporaryDirectory] = None
        self._proc: Optional[subprocess.Popen] = None
        self._log: Optional[Path] = None
        self._stderr = None
        self.port = 0

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/api/v1"

    def __enter__(self) -> "FakeLemonade":
        self._dir = tempfile.TemporaryDirectory(prefix="fake-lemonade-")
        folder = Path(self._dir.name)
        port_file, self._log = folder / "port.txt", folder / "requests.jsonl"
        command = [sys.executable, str(SERVER), "--catalog"]
        command += ["--port-file", str(port_file), "--log", str(self._log)]
        for model in self.downloaded:
            command += ["--model", model]
        # A file, not a pipe: the server logs every request, and an unread
        # pipe fills and stalls it in a long test.
        stderr_file = folder / "stderr.txt"
        self._stderr = open(stderr_file, "wb")
        self._proc = subprocess.Popen(
            command,
            env={**os.environ, **self._env, "PYTHONIOENCODING": "utf-8"},
            stdout=subprocess.DEVNULL,
            stderr=self._stderr,
        )
        deadline = time.monotonic() + START_TIMEOUT_S
        while not (port_file.exists() and port_file.read_text().strip()):
            if self._proc.poll() is not None or time.monotonic() > deadline:
                self._stderr.flush()
                error = stderr_file.read_text(encoding="utf-8", errors="replace")
                self.__exit__(None, None, None)
                raise RuntimeError(f"{SERVER.name} did not start: {error[-800:]}")
            time.sleep(0.05)
        self.port = int(port_file.read_text().strip())
        return self

    def __exit__(self, *_exc) -> None:
        if self._proc is not None:
            self._proc.kill()
            self._proc.wait(timeout=30)
        if self._stderr is not None:
            self._stderr.close()
        if self._dir is not None:
            self._dir.cleanup()

    # -- what a test reads --------------------------------------------------

    @property
    def requests(self) -> list[Request]:
        """Every request so far, oldest first."""
        if self._log is None or not self._log.exists():
            return []
        found = []
        for line in self._log.read_text(encoding="utf-8").splitlines():
            entry = json.loads(line)
            path = urlsplit(entry["path"]).path
            found.append(
                Request(entry["method"], path, entry["status"], entry.get("model"))
            )
        return found

    def _named(self, *suffixes: str) -> list[str]:
        return [
            r.model
            for r in self.requests
            if r.method == "POST" and r.path.endswith(suffixes) and r.model
        ]

    @property
    def pulls(self) -> list[str]:
        """Models GAIA asked the server to download, in order."""
        return self._named("/pull")

    @property
    def loads(self) -> list[str]:
        """Models GAIA asked the server to load, in order."""
        return self._named("/load")

    @property
    def chats(self) -> list[str]:
        """The model of each chat request, in order."""
        return self._named("/chat/completions")

    @property
    def downloads(self) -> list[str]:
        """Models the real server would have fetched: every pull, and every
        load, chat or embedding asked of a model that was not downloaded."""
        return [
            r.model
            for r in self.requests
            if r.method == "POST"
            and r.model
            and (
                r.path.endswith("/pull")
                or (r.status == 404 and r.path.endswith(_USES_A_MODEL))
            )
        ]
