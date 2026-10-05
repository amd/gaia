# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Shared plumbing: building the code index under test, and measuring it."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from gaia.code_index.sdk import CodeIndexConfig, CodeIndexSDK
from gaia.llm.lemonade_launcher import describe_start_hint


def posix(path: str) -> str:
    return path.replace("\\", "/")


def make_sdk(repo: Path, cache_root: Path) -> CodeIndexSDK:
    """The code index as shipped (default config) with its cache under *cache_root*."""
    return CodeIndexSDK(CodeIndexConfig(repo_path=str(repo), cache_dir=str(cache_root)))


def cache_path(sdk: CodeIndexSDK) -> Path:
    return Path(sdk.get_status()["cache_path"])


def read_metadata(sdk: CodeIndexSDK) -> Dict[str, Any]:
    """The index's ``metadata.json`` (the cache layout the SDK documents)."""
    path = cache_path(sdk) / "metadata.json"
    if not path.is_file():
        raise RuntimeError(
            f"no code index metadata at {path}; index the repository first"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def corpus(sdk: CodeIndexSDK) -> Set[str]:
    """Repo-relative POSIX paths of every file the index holds."""
    return {posix(p) for p in read_metadata(sdk).get("file_hashes", {})}


def parse_only_chunks(repo: Path) -> int:
    """How many chunks indexing *repo* would embed, without embedding anything.

    Sizes a repository before committing an hour of embedding to it. Uses the
    index's own discovery and parser so the count is the one indexing produces.
    """
    from gaia.code_index.parsers import chunk_code_file

    # Never indexed, so the cache directory is never created.
    sdk = make_sdk(repo, Path(tempfile.gettempdir()) / "gaia-size-probe")
    # pylint: disable=protected-access
    files, _ = sdk._discover_files()
    total = 0
    for path in files:
        text = sdk._read_file_safe(path)
        if text is not None:
            total += len(chunk_code_file(path, text, sdk.config.max_file_size_mb))
    return total


def dir_bytes(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


class RssSampler:
    """Peak resident memory of this process while the block runs, sampled every 50 ms."""

    def __init__(self, interval_s: float = 0.05):
        import psutil

        self._proc = psutil.Process()
        self._interval = interval_s
        self._stop = threading.Event()
        self.start_bytes = 0
        self.peak_bytes = 0
        self._thread: Optional[threading.Thread] = None

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            self.peak_bytes = max(self.peak_bytes, self._proc.memory_info().rss)

    def __enter__(self) -> "RssSampler":
        self.start_bytes = self.peak_bytes = self._proc.memory_info().rss
        self._thread = threading.Thread(
            target=self._run, name="rss-sampler", daemon=True
        )
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        self.peak_bytes = max(self.peak_bytes, self._proc.memory_info().rss)


def timed(fn, *args, **kwargs):
    """``(result, seconds)``."""
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    return result, time.perf_counter() - start


def _cpu_name() -> str:
    if sys.platform == "win32":
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        ) as key:
            return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        for line in cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    return platform.processor() or platform.machine()


def _gaia_commit() -> Dict[str, Any]:
    root = Path(__file__).resolve().parents[4]
    if not (root / ".git").exists():
        return {"commit": None, "dirty": None}
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return {"commit": commit, "dirty": bool(dirty)}


def embedder_preflight() -> Dict[str, Any]:
    """Lemonade's version and URL; raises with a start hint when it is unreachable."""
    from gaia.llm.lemonade_client import DEFAULT_EMBEDDING_MODEL, LemonadeClient

    client = LemonadeClient()
    try:
        health = client.health_check(timeout=10)
    except Exception as exc:  # LemonadeClientError and transport errors alike
        raise RuntimeError(
            f"The retrieval benchmark embeds with Lemonade, which is not reachable at "
            f"{client.base_url}: {exc}. {describe_start_hint().instruction} If GAIA "
            "runs its own embedded Lemonade, load its URL first (`. ~/.gaia/lemonade/"
            "env.sh`, or env.ps1 on Windows)."
        ) from exc
    return {
        "lemonade_url": client.base_url,
        "lemonade_version": health.get("version"),
        "embedding_model": DEFAULT_EMBEDDING_MODEL,
    }


def environment() -> Dict[str, Any]:
    import psutil

    return {
        "machine": platform.node(),
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "cpu": _cpu_name(),
        "logical_cpus": os.cpu_count(),
        "ram_gb": round(psutil.virtual_memory().total / 2**30, 1),
        "python": platform.python_version(),
        **{f"gaia_{k}": v for k, v in _gaia_commit().items()},
    }


def top_keys(results: List[Any], k: int = 10) -> List[List[Any]]:
    """``[path, start_line]`` of the first *k* search results, for comparing runs."""
    return [[posix(r.chunk.file_path), r.chunk.start_line] for r in results[:k]]
