# Email Sidecar in Agent UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Node-free, out-of-process email-agent sidecar that the Python Agent UI backend fetches (verified download), spawns (user=frozen binary / dev=uvicorn-from-source), health-checks, proxies to, and tree-kills — gated behind `GAIA_EMAIL_AGENT_MODE` so it is additive and non-breaking.

**Architecture:** A new `src/gaia/ui/email_sidecar/` Python package owns four units: `platform` (lock loading + platform-key + placeholder detection, ported from `platform.ts`), `fetch` (verified SHA-256 download, ported from `fetch.ts` — the security boundary), `manager` (`EmailSidecarManager`: mode select, ephemeral port, spawn, health-poll, tree-kill, ported from `lifecycle.ts`), and `proxy` (`EmailSidecarProxy`: forward triage/draft/send to the running sidecar, gate not-yet-existing routes). One small enabler lands in `hub/agents/email/python/packaging/server.py` (`app = build_app()` at module scope) so dev mode can hot-reload. **No Node.js, no npm import** is on the UI runtime path; the npm `fetch.ts`/`lifecycle.ts` are reference-only.

**Tech Stack:** Python 3.10+, `requests` (already a dep; used for HTTP/health), `subprocess` + `os.killpg`/`taskkill` (process tree management), `hashlib` (SHA-256), `socket` (ephemeral port), pytest + `responses`/monkeypatch for unit tests, FastAPI/uvicorn (email sidecar, already present in the email wheel).

## Global Constraints

- **ZERO Node.js / npm on the Agent UI runtime path.** No `npx`, no npm package import. Port `fetch.ts`/`lifecycle.ts` logic to Python.
- **NEVER bind or use port 4001.** Sidecar uses an ephemeral per-backend-instance port. Guard explicitly and raise if `4001` is ever requested.
- **Fail loudly — no silent fallbacks** (CLAUDE.md). No user→dev fallback, no sidecar→in-process fallback. Every failure raises an actionable error naming *what failed*, *what to do*, *where to look*.
- **No Claude/AI attribution** in any commit, comment, docstring, or doc.
- **Additive/gated:** Do NOT remove the in-process `/v1/email` mount (`src/gaia/ui/server.py:592-601`). Do NOT switch `agent_type=email` sessions to the proxy. New path lives behind `GAIA_EMAIL_AGENT_MODE` (`user` default / `dev`).
- SHA-256 verification is the security boundary — tampered bytes MUST raise `IntegrityError`; there is no "use it anyway" path.
- Lock file source of truth: `hub/agents/email/npm/binaries.lock.json` (currently has `PENDING-*` placeholder SHAs → fetch must refuse placeholders loudly).
- Cache dir: `~/.gaia/agents/email/`. Cache-hit (on-disk SHA matches lock) skips re-download.
- Routes that EXIST today and may be proxied: `POST /v1/email/triage`, `/draft`, `/send`; `GET /health`, `/version`. Routes that do NOT exist yet (gate, don't build): inbox pre-scan, search (#1781), archive/quarantine (#1779), calendar (#1780).
- **Dev-mode import-string correction (verified during impl):** the email package's `packaging/` dir has no `__init__.py` and collides by name with the PyPI `packaging` library, so `packaging.server:app` resolves to the WRONG `packaging` and fails. Adding `packaging/__init__.py` would shadow PyPI `packaging` for every dependency needing `packaging.version` — unacceptable. Dev mode therefore loads the file as the **top-level module `server`** via `uvicorn server:app --app-dir <email>/packaging`. Task 4's manager uses this form, not `packaging.server:app`.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `hub/agents/email/python/packaging/server.py` (modify) | Add `app = build_app()` at module scope for uvicorn import-string dev mode |
| `src/gaia/ui/email_sidecar/__init__.py` (create) | Package exports: `EmailSidecarManager`, `EmailSidecarProxy`, error types |
| `src/gaia/ui/email_sidecar/errors.py` (create) | `SidecarError` base + `PlatformError`, `IntegrityError`, `BinaryNotFoundError`, `HealthTimeoutError`, `SidecarSpawnError`, `RouteNotAvailableError` |
| `src/gaia/ui/email_sidecar/platform.py` (create) | `current_platform_key()`, `load_lock()`, `resolve_entry()`, `is_placeholder_sha()`, `default_lock_path()` (port of `platform.ts`) |
| `src/gaia/ui/email_sidecar/fetch.py` (create) | `verify_sha256()`, `file_sha256()`, `fetch_binary()` (port of `fetch.ts`; security boundary) |
| `src/gaia/ui/email_sidecar/manager.py` (create) | `EmailSidecarManager`: mode select, ephemeral port, spawn (user/dev), health-poll, tree-kill |
| `src/gaia/ui/email_sidecar/proxy.py` (create) | `EmailSidecarProxy`: forward triage/draft/send; gate pre-scan/search/archive/calendar |
| `tests/unit/test_email_sidecar_platform.py` (create) | platform-key, lock load/validate, placeholder detection |
| `tests/unit/test_email_sidecar_fetch.py` (create) | SHA verify (tamper → raise), cache-hit, placeholder-refuse, download-error |
| `tests/unit/test_email_sidecar_manager.py` (create) | mode select, spawn-arg shape, ephemeral port (≠4001), health-poll, tree-kill, dev-missing-env error |
| `tests/unit/test_email_sidecar_proxy.py` (create) | forward triage/draft/send envelopes unchanged; gated routes raise `RouteNotAvailableError` |
| `tests/unit/test_email_sidecar_devmode_app.py` (create) | `packaging.server:app` import-string resolves to a FastAPI app |
| `docs/guides/email.mdx` (modify) | Short "Dev mode (sidecar from source)" subsection |

---

## Task 1: Dev-mode enabler — module-level `app` in the freeze server

**Files:**
- Modify: `hub/agents/email/python/packaging/server.py`
- Test: `tests/unit/test_email_sidecar_devmode_app.py` (create)

**Interfaces:**
- Produces: module attribute `packaging.server.app` (a `fastapi.FastAPI`) so `uvicorn packaging.server:app --reload` resolves the import string. Consumed by Task 4's dev-mode spawn args.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_email_sidecar_devmode_app.py
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Dev mode needs uvicorn's import-string form (`packaging.server:app`), which
requires a module-level `app`. This guards that it exists and is a FastAPI app."""
import importlib.util
import sys
from pathlib import Path

import pytest

EMAIL_PKG = (
    Path(__file__).resolve().parents[2]
    / "hub" / "agents" / "python" / "email"
)


@pytest.mark.skipif(
    importlib.util.find_spec("gaia_agent_email") is None,
    reason="email agent wheel not installed (uv pip install -e hub/agents/email/python)",
)
def test_packaging_server_exposes_module_level_app():
    # Import-string resolution mirrors what `uvicorn packaging.server:app` does:
    # cwd = the email package root so `packaging` is importable.
    sys.path.insert(0, str(EMAIL_PKG))
    try:
        import packaging.server as server_mod

        assert hasattr(server_mod, "app"), "module-level `app` missing for uvicorn --reload"
        from fastapi import FastAPI

        assert isinstance(server_mod.app, FastAPI)
        # The dev app must serve the same probes the manager health-polls.
        routes = {r.path for r in server_mod.app.routes}
        assert "/health" in routes
        assert "/v1/email/triage" in routes
    finally:
        sys.path.remove(str(EMAIL_PKG))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_email_sidecar_devmode_app.py -v`
Expected: FAIL — `module-level app missing` (or skip if wheel not installed; if skipped, install with `uv pip install -e hub/agents/email/python` then re-run so it actually fails).

- [ ] **Step 3: Add the module-level app**

In `hub/agents/email/python/packaging/server.py`, after the `build_app()` definition (after line 79, before `def main`), add:

```python
# Module-level app for uvicorn's import-string form (`packaging.server:app`),
# which dev mode needs for `--reload`. build_app() also mounts the connector
# routes — fine for dev. main() builds its own app, so this is dev-only surface.
app = build_app()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_email_sidecar_devmode_app.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add hub/agents/email/python/packaging/server.py tests/unit/test_email_sidecar_devmode_app.py
git commit -m "feat(email): expose module-level app for uvicorn --reload dev mode"
```

---

## Task 2: Errors + platform/lock loading

**Files:**
- Create: `src/gaia/ui/email_sidecar/__init__.py`
- Create: `src/gaia/ui/email_sidecar/errors.py`
- Create: `src/gaia/ui/email_sidecar/platform.py`
- Test: `tests/unit/test_email_sidecar_platform.py`

**Interfaces:**
- Produces:
  - `errors.SidecarError(Exception)` and subclasses `PlatformError`, `IntegrityError`, `BinaryNotFoundError`, `HealthTimeoutError`, `SidecarSpawnError`, `RouteNotAvailableError`.
  - `platform.current_platform_key() -> str` (e.g. `"darwin-arm64"`).
  - `platform.LockEntry` dataclass `(filename: str, sha256: str, executable: str, size: int | None)`.
  - `platform.BinaryLock` dataclass `(schema_version, agent_version, base_url, binaries: dict[str, LockEntry])`.
  - `platform.default_lock_path() -> Path` → repo `hub/agents/email/npm/binaries.lock.json`.
  - `platform.load_lock(path: Path) -> BinaryLock`.
  - `platform.resolve_entry(lock, platform_key) -> LockEntry`.
  - `platform.is_placeholder_sha(sha: str) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_email_sidecar_platform.py
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
import json
from pathlib import Path

import pytest

from gaia.ui.email_sidecar.errors import PlatformError
from gaia.ui.email_sidecar import platform as plat


def _write_lock(tmp_path: Path, binaries: dict, base_url="https://example/r2") -> Path:
    p = tmp_path / "binaries.lock.json"
    p.write_text(json.dumps({
        "schemaVersion": "1.0", "agentVersion": "0.2.2",
        "baseUrl": base_url, "binaries": binaries,
    }))
    return p


def test_current_platform_key_shape():
    key = plat.current_platform_key()
    assert "-" in key  # e.g. darwin-arm64


def test_is_placeholder_sha():
    assert plat.is_placeholder_sha("PENDING-1648-replace-with-real-sha256")
    assert plat.is_placeholder_sha("0" * 64)
    assert not plat.is_placeholder_sha("a" * 64)


def test_load_and_resolve_entry(tmp_path):
    lock_path = _write_lock(tmp_path, {
        "darwin-arm64": {
            "filename": "email-agent-darwin-arm64",
            "executable": "email-agent",
            "sha256": "a" * 64, "size": 10,
        }
    })
    lock = plat.load_lock(lock_path)
    entry = plat.resolve_entry(lock, "darwin-arm64")
    assert entry.filename == "email-agent-darwin-arm64"
    assert entry.executable == "email-agent"
    assert entry.sha256 == "a" * 64


def test_resolve_entry_unknown_platform_raises(tmp_path):
    lock = plat.load_lock(_write_lock(tmp_path, {
        "linux-x64": {"filename": "f", "executable": "e", "sha256": "a" * 64}
    }))
    with pytest.raises(PlatformError, match="no email-agent binary for platform"):
        plat.resolve_entry(lock, "plan9-sparc")


def test_load_lock_missing_file_raises(tmp_path):
    with pytest.raises(PlatformError, match="cannot read binaries.lock.json"):
        plat.load_lock(tmp_path / "nope.json")


def test_default_lock_path_points_at_repo_lock():
    p = plat.default_lock_path()
    assert p.name == "binaries.lock.json"
    assert p.parts[-3:] == ("npm", "agent-email", "binaries.lock.json")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_email_sidecar_platform.py -v`
Expected: FAIL — `ModuleNotFoundError: gaia.ui.email_sidecar`

- [ ] **Step 3: Implement the package, errors, and platform**

```python
# src/gaia/ui/email_sidecar/__init__.py
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Node-free email-agent sidecar support for the Agent UI backend.

The Python UI backend fetches (verified download), spawns, health-checks,
proxies to, and tree-kills the email sidecar directly — no Node.js, no npm
package on the runtime path. The npm `fetch.ts`/`lifecycle.ts` are the
external Node-integrator channel and the reference for this Python port.
"""
from gaia.ui.email_sidecar.errors import (
    BinaryNotFoundError,
    HealthTimeoutError,
    IntegrityError,
    PlatformError,
    RouteNotAvailableError,
    SidecarError,
    SidecarSpawnError,
)

__all__ = [
    "SidecarError",
    "PlatformError",
    "IntegrityError",
    "BinaryNotFoundError",
    "HealthTimeoutError",
    "SidecarSpawnError",
    "RouteNotAvailableError",
]
```

```python
# src/gaia/ui/email_sidecar/errors.py
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Loud, actionable error types for the email sidecar (no silent fallbacks)."""


class SidecarError(Exception):
    """Base for all email-sidecar failures."""


class PlatformError(SidecarError):
    """Unsupported platform, unreadable/invalid lock, or placeholder entry."""


class IntegrityError(SidecarError):
    """A downloaded binary's SHA-256 did not match binaries.lock.json.

    The security boundary: a tampered/truncated download is rejected before it
    can ever be spawned. There is no 'use it anyway' path.
    """


class BinaryNotFoundError(SidecarError):
    """The frozen binary is not present where it was expected."""


class HealthTimeoutError(SidecarError):
    """The sidecar did not report healthy within the health-poll deadline."""


class SidecarSpawnError(SidecarError):
    """The sidecar process could not be launched (dev env missing, port in use)."""


class RouteNotAvailableError(SidecarError):
    """A UI capability whose REST route does not exist on the sidecar yet."""
```

```python
# src/gaia/ui/email_sidecar/platform.py
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Platform/arch resolution and binary-lock loading (port of platform.ts).

`binaries.lock.json` is the single source of truth for which artifact to fetch
for the current host and what its SHA-256 must be. Platform keys are
``{platform}-{arch}`` normalized to the npm package's key space.
"""
from __future__ import annotations

import json
import platform as _platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from gaia.ui.email_sidecar.errors import PlatformError

SUPPORTED_PLATFORMS = ("win32-x64", "darwin-arm64", "darwin-x64", "linux-x64")


@dataclass(frozen=True)
class LockEntry:
    filename: str
    sha256: str
    executable: str
    size: Optional[int] = None


@dataclass(frozen=True)
class BinaryLock:
    schema_version: str
    agent_version: str
    base_url: str
    binaries: dict


def current_platform_key(
    plat: Optional[str] = None, arch: Optional[str] = None
) -> str:
    """Resolve the host's platform key in the npm package's namespace.

    Maps Python's ``sys.platform``/``platform.machine()`` to the ``{os}-{arch}``
    keys used in binaries.lock.json (``win32``/``darwin``/``linux`` + ``x64``/``arm64``).
    """
    raw_os = plat if plat is not None else sys.platform
    if raw_os.startswith("win"):
        os_key = "win32"
    elif raw_os == "darwin":
        os_key = "darwin"
    elif raw_os.startswith("linux"):
        os_key = "linux"
    else:
        os_key = raw_os
    raw_arch = (arch if arch is not None else _platform.machine()).lower()
    if raw_arch in ("x86_64", "amd64", "x64"):
        arch_key = "x64"
    elif raw_arch in ("arm64", "aarch64"):
        arch_key = "arm64"
    else:
        arch_key = raw_arch
    return f"{os_key}-{arch_key}"


def default_lock_path() -> Path:
    """Locate the repo's binaries.lock.json (npm package ships the canonical one)."""
    # src/gaia/ui/email_sidecar/platform.py -> repo root is parents[4].
    repo_root = Path(__file__).resolve().parents[4]
    return repo_root / "hub" / "agents" / "npm" / "agent-email" / "binaries.lock.json"


def load_lock(lock_path: Optional[Path] = None) -> BinaryLock:
    path = Path(lock_path) if lock_path is not None else default_lock_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise PlatformError(
            f"cannot read binaries.lock.json at {path}: {e}. This manifest ships "
            "with the email agent package; reinstall/rebuild it if it is missing."
        ) from e
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise PlatformError(
            f"binaries.lock.json at {path} is not valid JSON: {e}"
        ) from e
    binaries = parsed.get("binaries")
    if not isinstance(binaries, dict):
        raise PlatformError(
            f'binaries.lock.json at {path} is missing a "binaries" map'
        )
    entries = {
        k: LockEntry(
            filename=v.get("filename", ""),
            sha256=v.get("sha256", ""),
            executable=v.get("executable", ""),
            size=v.get("size"),
        )
        for k, v in binaries.items()
    }
    return BinaryLock(
        schema_version=parsed.get("schemaVersion", ""),
        agent_version=parsed.get("agentVersion", ""),
        base_url=parsed.get("baseUrl", ""),
        binaries=entries,
    )


def resolve_entry(lock: BinaryLock, platform_key: str) -> LockEntry:
    entry = lock.binaries.get(platform_key)
    if entry is None:
        available = ", ".join(lock.binaries) or "(none)"
        raise PlatformError(
            f"no email-agent binary for platform '{platform_key}'. Available in "
            f"binaries.lock.json: {available}. Supported targets: "
            + ", ".join(SUPPORTED_PLATFORMS)
        )
    if not entry.sha256 or not entry.filename or not entry.executable:
        raise PlatformError(
            f"binaries.lock.json entry for '{platform_key}' is incomplete "
            "(needs filename, sha256, executable) — likely a placeholder with no "
            "published binary for this platform."
        )
    return entry


def is_placeholder_sha(sha256: str) -> bool:
    s = sha256 or ""
    return s.strip("0") == "" and s != "" or "PENDING" in s.upper()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_email_sidecar_platform.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gaia/ui/email_sidecar/__init__.py src/gaia/ui/email_sidecar/errors.py src/gaia/ui/email_sidecar/platform.py tests/unit/test_email_sidecar_platform.py
git commit -m "feat(ui): email sidecar platform/lock loading + error types"
```

---

## Task 3: Verified fetch (the security boundary)

**Files:**
- Create: `src/gaia/ui/email_sidecar/fetch.py`
- Test: `tests/unit/test_email_sidecar_fetch.py`

**Interfaces:**
- Consumes: `platform.load_lock`, `resolve_entry`, `current_platform_key`, `is_placeholder_sha`, `default_lock_path`; `errors.PlatformError`, `IntegrityError`.
- Produces:
  - `fetch.sha256_hex(data: bytes) -> str`
  - `fetch.file_sha256(path: Path) -> str | None`
  - `fetch.verify_sha256(data: bytes, expected: str, source_label: str) -> str` (raises `IntegrityError` on mismatch)
  - `fetch.default_cache_dir() -> Path` → `~/.gaia/agents/email`
  - `fetch.FetchResult` dataclass `(binary_path: Path, platform_key: str, sha256: str, url: str, cached: bool)`
  - `fetch.fetch_binary(*, out_dir=None, base_url=None, platform_key=None, lock_path=None, force=False, timeout=120.0, session=None) -> FetchResult`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_email_sidecar_fetch.py
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
import hashlib
import json
from pathlib import Path

import pytest

from gaia.ui.email_sidecar.errors import IntegrityError, PlatformError
from gaia.ui.email_sidecar import fetch as fetchmod


REAL_BYTES = b"fake-binary-payload"
REAL_SHA = hashlib.sha256(REAL_BYTES).hexdigest()


def _lock(tmp_path: Path, sha: str, base="https://r2.example") -> Path:
    p = tmp_path / "binaries.lock.json"
    p.write_text(json.dumps({
        "schemaVersion": "1.0", "agentVersion": "0.2.2", "baseUrl": base,
        "binaries": {"darwin-arm64": {
            "filename": "email-agent-darwin-arm64",
            "executable": "email-agent", "sha256": sha, "size": len(REAL_BYTES),
        }},
    }))
    return p


class _FakeResp:
    def __init__(self, content, status=200):
        self.content = content
        self.status_code = status
        self.reason = "OK" if status == 200 else "ERR"

    @property
    def ok(self):
        return self.status_code == 200


class _FakeSession:
    def __init__(self, content, status=200):
        self._content, self._status = content, status
        self.calls = []

    def get(self, url, timeout=None, headers=None, stream=False):
        self.calls.append(url)
        return _FakeResp(self._content, self._status)


def test_verify_sha256_tamper_raises():
    with pytest.raises(IntegrityError, match="SHA-256 mismatch"):
        fetchmod.verify_sha256(b"tampered", REAL_SHA, "test")


def test_fetch_downloads_and_verifies(tmp_path):
    out = tmp_path / "cache"
    sess = _FakeSession(REAL_BYTES)
    res = fetchmod.fetch_binary(
        out_dir=out, platform_key="darwin-arm64",
        lock_path=_lock(tmp_path, REAL_SHA), session=sess,
    )
    assert res.cached is False
    assert res.sha256 == REAL_SHA
    assert Path(res.binary_path).read_bytes() == REAL_BYTES
    assert sess.calls == ["https://r2.example/email-agent-darwin-arm64"]


def test_fetch_cache_hit_skips_download(tmp_path):
    out = tmp_path / "cache"
    out.mkdir()
    (out / "email-agent").write_bytes(REAL_BYTES)
    sess = _FakeSession(b"SHOULD-NOT-BE-USED")
    res = fetchmod.fetch_binary(
        out_dir=out, platform_key="darwin-arm64",
        lock_path=_lock(tmp_path, REAL_SHA), session=sess,
    )
    assert res.cached is True
    assert sess.calls == []  # no network on cache hit


def test_fetch_tampered_download_raises(tmp_path):
    out = tmp_path / "cache"
    sess = _FakeSession(b"corrupted-bytes")
    with pytest.raises(IntegrityError):
        fetchmod.fetch_binary(
            out_dir=out, platform_key="darwin-arm64",
            lock_path=_lock(tmp_path, REAL_SHA), session=sess,
        )
    # A failed verify must NOT leave a binary on disk.
    assert not (out / "email-agent").exists()


def test_fetch_placeholder_sha_refuses(tmp_path):
    sess = _FakeSession(REAL_BYTES)
    with pytest.raises(PlatformError, match="placeholder"):
        fetchmod.fetch_binary(
            out_dir=tmp_path / "cache", platform_key="darwin-arm64",
            lock_path=_lock(tmp_path, "PENDING-1648-replace"), session=sess,
        )


def test_fetch_http_error_raises(tmp_path):
    sess = _FakeSession(b"", status=404)
    with pytest.raises(RuntimeError, match="download failed"):
        fetchmod.fetch_binary(
            out_dir=tmp_path / "cache", platform_key="darwin-arm64",
            lock_path=_lock(tmp_path, REAL_SHA), session=sess,
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_email_sidecar_fetch.py -v`
Expected: FAIL — `ModuleNotFoundError: gaia.ui.email_sidecar.fetch`

- [ ] **Step 3: Implement fetch**

```python
# src/gaia/ui/email_sidecar/fetch.py
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Verified binary fetch (port of fetch.ts) — the security boundary.

Resolves the host platform -> looks up the artifact in binaries.lock.json ->
downloads it -> **verifies its SHA-256 against the lock and raises loudly on any
mismatch** -> writes it atomically into the cache -> chmod +x on POSIX. A
tampered/truncated download is rejected before it can ever be spawned. There is
NO 'use it anyway' path.
"""
from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from gaia.logger import get_logger
from gaia.ui.email_sidecar import platform as plat
from gaia.ui.email_sidecar.errors import IntegrityError, PlatformError

logger = get_logger(__name__)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> Optional[str]:
    try:
        return sha256_hex(Path(path).read_bytes())
    except OSError:
        return None


def verify_sha256(data: bytes, expected: str, source_label: str) -> str:
    actual = sha256_hex(data)
    if actual.lower() != expected.lower():
        raise IntegrityError(
            f"SHA-256 mismatch for {source_label}:\n"
            f"  expected {expected}\n  actual   {actual}\n"
            "Refusing to use a binary that does not match binaries.lock.json. The "
            "download may be corrupt, truncated, or tampered with. Re-run the fetch; "
            "if it persists, the lock may be stale relative to the published artifact."
        )
    return actual


def default_cache_dir() -> Path:
    return Path.home() / ".gaia" / "agents" / "email"


@dataclass(frozen=True)
class FetchResult:
    binary_path: Path
    platform_key: str
    sha256: str
    url: str
    cached: bool


def _join_url(base: str, name: str) -> str:
    return f"{base.rstrip('/')}/{name.lstrip('/')}"


def fetch_binary(
    *,
    out_dir: Optional[Path] = None,
    base_url: Optional[str] = None,
    platform_key: Optional[str] = None,
    lock_path: Optional[Path] = None,
    force: bool = False,
    timeout: float = 120.0,
    session=None,
) -> FetchResult:
    """Fetch + verify + cache the email-agent binary for the current platform.

    Raises:
        PlatformError: unsupported platform / incomplete or placeholder lock entry.
        IntegrityError: SHA-256 mismatch (tampered/truncated download).
        RuntimeError: download/network failure (HTTP status surfaced).
    """
    lock = plat.load_lock(lock_path)
    key = platform_key or plat.current_platform_key()
    entry = plat.resolve_entry(lock, key)
    resolved_base = base_url or os.environ.get("ASSETS_BASE_URL") or lock.base_url
    if not resolved_base:
        raise PlatformError(
            "no download base URL: binaries.lock.json has no baseUrl, ASSETS_BASE_URL "
            "is unset, and none was passed. Set ASSETS_BASE_URL or pass base_url."
        )
    if plat.is_placeholder_sha(entry.sha256):
        raise PlatformError(
            f"binaries.lock.json has a placeholder sha256 for '{key}' "
            f"({entry.sha256}); no binary is published for it in this build. Fetch is "
            "blocked so a bad binary can never be trusted. Publish the email agent "
            "(release_agent_email.yml) to populate the real SHA, or run dev mode."
        )

    cache = Path(out_dir) if out_dir is not None else default_cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    binary_path = cache / entry.executable
    url = _join_url(resolved_base, entry.filename)

    if not force:
        existing = file_sha256(binary_path)
        if existing and existing.lower() == entry.sha256.lower():
            logger.info("email sidecar: cache hit %s matches lock sha256", binary_path)
            return FetchResult(binary_path, key, existing, url, cached=True)

    if session is None:
        import requests

        session = requests.Session()
    logger.info("email sidecar: downloading %s binary from %s", key, url)
    resp = session.get(url, timeout=timeout, headers={"accept": "application/octet-stream"})
    if not getattr(resp, "ok", resp.status_code == 200):
        raise RuntimeError(
            f"download failed: HTTP {resp.status_code} {getattr(resp, 'reason', '')} "
            f"for {url}. Check ASSETS_BASE_URL and that the artifact is published "
            f"for {key}."
        )
    data = resp.content
    sha = verify_sha256(data, entry.sha256, f"{key} ({url})")

    # Write to a temp then rename so a crash mid-write never leaves a
    # half-written "verified" binary. Clean up the temp on any failure.
    tmp = binary_path.with_suffix(binary_path.suffix + f".download.{os.getpid()}")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, binary_path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    if os.name != "nt":
        binary_path.chmod(binary_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    logger.info("email sidecar: installed verified binary -> %s", binary_path)
    return FetchResult(binary_path, key, sha, url, cached=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_email_sidecar_fetch.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/gaia/ui/email_sidecar/fetch.py tests/unit/test_email_sidecar_fetch.py
git commit -m "feat(ui): email sidecar verified SHA-256 binary fetch (security boundary)"
```

---

## Task 4: EmailSidecarManager — mode, port, spawn, health, tree-kill

**Files:**
- Create: `src/gaia/ui/email_sidecar/manager.py`
- Test: `tests/unit/test_email_sidecar_manager.py`

**Interfaces:**
- Consumes: `fetch.fetch_binary`, `fetch.default_cache_dir`; `errors.*`.
- Produces:
  - `manager.find_free_port(host="127.0.0.1") -> int` (never returns 4001).
  - `manager.EmailSidecarManager(mode=None, host="127.0.0.1", lock_path=None, cache_dir=None, email_src_dir=None, health_timeout=30.0)`.
    - property `.mode -> str` (`"user"`/`"dev"`, from `GAIA_EMAIL_AGENT_MODE`, default `"user"`).
    - `.base_url -> str | None` (set after start).
    - `.build_spawn_command() -> tuple[list[str], dict]` → `(argv, popen_kwargs)`; user → `[binary, --host, h, --port, p]`; dev → `[sys.executable, -m, uvicorn, packaging.server:app, --reload, --host, h, --port, p]` with `cwd=email_src_dir`. Lazy-fetches the binary in user mode.
    - `.start() -> str` (spawn + health-poll; returns base_url; raises on failure, killing any spawned proc).
    - `.shutdown(timeout=5.0) -> None` (tree-kill).
    - `.is_running -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_email_sidecar_manager.py
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
import sys
from pathlib import Path

import pytest

from gaia.ui.email_sidecar.errors import SidecarSpawnError
from gaia.ui.email_sidecar import manager as mgr


def test_find_free_port_never_4001():
    for _ in range(20):
        assert mgr.find_free_port() != 4001


def test_mode_defaults_to_user(monkeypatch):
    monkeypatch.delenv("GAIA_EMAIL_AGENT_MODE", raising=False)
    assert mgr.EmailSidecarManager().mode == "user"


def test_mode_reads_env(monkeypatch):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    assert mgr.EmailSidecarManager().mode == "dev"


def test_invalid_mode_raises(monkeypatch):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "bananas")
    with pytest.raises(SidecarSpawnError, match="GAIA_EMAIL_AGENT_MODE"):
        mgr.EmailSidecarManager().mode


def test_user_mode_spawn_command_uses_fetched_binary(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "user")
    fake_binary = tmp_path / "email-agent"
    fake_binary.write_bytes(b"x")

    class _Res:
        binary_path = fake_binary

    monkeypatch.setattr(mgr.fetch, "fetch_binary", lambda **kw: _Res())
    m = mgr.EmailSidecarManager()
    argv, kwargs = m.build_spawn_command(port=9123)
    assert argv[0] == str(fake_binary)
    assert "--port" in argv and "9123" in argv
    assert "4001" not in argv


def test_dev_mode_spawn_command_is_uvicorn_import_string(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    src = tmp_path / "email"
    (src / "packaging").mkdir(parents=True)
    m = mgr.EmailSidecarManager(email_src_dir=src)
    argv, kwargs = m.build_spawn_command(port=9124)
    assert argv[0] == sys.executable
    assert "uvicorn" in argv
    assert "packaging.server:app" in argv
    assert "--reload" in argv
    assert kwargs["cwd"] == str(src)


def test_dev_mode_missing_src_dir_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    m = mgr.EmailSidecarManager(email_src_dir=tmp_path / "does-not-exist")
    with pytest.raises(SidecarSpawnError, match="uv pip install -e"):
        m.build_spawn_command(port=9125)


def test_spawn_port_4001_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    src = tmp_path / "email"
    (src / "packaging").mkdir(parents=True)
    m = mgr.EmailSidecarManager(email_src_dir=src)
    with pytest.raises(ValueError, match="4001"):
        m.build_spawn_command(port=4001)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_email_sidecar_manager.py -v`
Expected: FAIL — `ModuleNotFoundError: gaia.ui.email_sidecar.manager`

- [ ] **Step 3: Implement manager**

```python
# src/gaia/ui/email_sidecar/manager.py
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""EmailSidecarManager — spawn / health / tree-kill the email sidecar (port of
lifecycle.ts), Node-free.

Modes (GAIA_EMAIL_AGENT_MODE):
  user (default) — spawn the cached frozen binary (lazy-fetched on first use).
  dev            — spawn `uvicorn packaging.server:app --reload` from source.

Both serve the identical /v1/email/* contract; the mode only swaps which process
answers. The manager binds an ephemeral per-instance port (NEVER 4001) and
tree-kills the whole process group on shutdown (a PyInstaller one-file build
spawns a uvicorn child that a plain kill would orphan).
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from gaia.logger import get_logger
from gaia.ui.email_sidecar import fetch
from gaia.ui.email_sidecar.errors import (
    HealthTimeoutError,
    SidecarSpawnError,
)

logger = get_logger(__name__)

_HOST = "127.0.0.1"
_RESERVED_PORT = 4001
_VALID_MODES = ("user", "dev")


def find_free_port(host: str = _HOST) -> int:
    """Bind to port 0 to get an OS-assigned free port. Never returns 4001."""
    for _ in range(50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, 0))
            port = s.getsockname()[1]
        if port != _RESERVED_PORT:
            return port
    raise SidecarSpawnError("could not find a free ephemeral port for the email sidecar")


def _default_email_src_dir() -> Path:
    # src/gaia/ui/email_sidecar/manager.py -> repo root is parents[4].
    return Path(__file__).resolve().parents[4] / "hub" / "agents" / "python" / "email"


class EmailSidecarManager:
    def __init__(
        self,
        mode: Optional[str] = None,
        *,
        host: str = _HOST,
        lock_path: Optional[Path] = None,
        cache_dir: Optional[Path] = None,
        email_src_dir: Optional[Path] = None,
        health_timeout: float = 30.0,
    ):
        self._mode_override = mode
        self.host = host
        self.lock_path = lock_path
        self.cache_dir = cache_dir
        self.email_src_dir = Path(email_src_dir) if email_src_dir else _default_email_src_dir()
        self.health_timeout = health_timeout
        self._proc: Optional[subprocess.Popen] = None
        self.port: Optional[int] = None
        self.base_url: Optional[str] = None

    @property
    def mode(self) -> str:
        m = self._mode_override or os.environ.get("GAIA_EMAIL_AGENT_MODE") or "user"
        if m not in _VALID_MODES:
            raise SidecarSpawnError(
                f"GAIA_EMAIL_AGENT_MODE='{m}' is invalid; expected one of "
                f"{_VALID_MODES}. There is no fallback — set it explicitly."
            )
        return m

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def build_spawn_command(self, *, port: int):
        if port == _RESERVED_PORT:
            raise ValueError("port 4001 is reserved and must never be used")
        if self.mode == "user":
            try:
                result = fetch.fetch_binary(
                    out_dir=self.cache_dir, lock_path=self.lock_path
                )
            except Exception as e:  # re-raise with the user-mode remedy
                raise SidecarSpawnError(
                    f"email sidecar binary unavailable in user mode: {e} "
                    "Set GAIA_EMAIL_AGENT_MODE=dev to run from source, or publish "
                    "the email agent so the binary + real SHA exist."
                ) from e
            binary = str(result.binary_path)
            argv = [binary, "--host", self.host, "--port", str(port)]
            return argv, {}
        # dev mode
        if not (self.email_src_dir / "packaging").is_dir():
            raise SidecarSpawnError(
                f"dev mode needs the email source at {self.email_src_dir} but it is "
                "missing. Run from a source checkout, or install it: "
                "`uv pip install -e hub/agents/email/python`."
            )
        argv = [
            sys.executable, "-m", "uvicorn", "packaging.server:app", "--reload",
            "--host", self.host, "--port", str(port),
        ]
        return argv, {"cwd": str(self.email_src_dir)}

    def start(self) -> str:
        if self.is_running:
            return self.base_url
        self.port = find_free_port(self.host)
        argv, popen_kwargs = self.build_spawn_command(port=self.port)
        logger.info("email sidecar: spawning (%s mode) %s", self.mode, " ".join(argv))
        creationflags = 0
        start_new_session = False
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            start_new_session = True  # own process group for tree-kill
        try:
            self._proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=start_new_session,
                creationflags=creationflags,
                **popen_kwargs,
            )
        except OSError as e:
            raise SidecarSpawnError(
                f"failed to launch the email sidecar ({argv[0]}): {e}"
            ) from e
        self.base_url = f"http://{self.host}:{self.port}"
        try:
            self._wait_for_health()
        except Exception:
            self.shutdown()
            raise
        return self.base_url

    def _wait_for_health(self, interval: float = 0.25) -> None:
        import requests

        deadline = time.monotonic() + self.health_timeout
        url = f"{self.base_url}/health"
        last_err = ""
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                stderr = (self._proc.stderr.read() or b"").decode("utf-8", "replace")[-2000:]
                raise SidecarSpawnError(
                    f"email sidecar exited early (code {self._proc.returncode}) before "
                    f"becoming healthy. Last stderr:\n{stderr}"
                )
            try:
                r = requests.get(url, timeout=interval * 4)
                if r.status_code == 200 and r.json().get("status") == "ok":
                    logger.info("email sidecar healthy at %s", self.base_url)
                    return
                last_err = f"status={r.status_code} body={r.text[:200]}"
            except requests.exceptions.RequestException as e:
                last_err = f"{type(e).__name__}: {e}"
            time.sleep(interval)
        stderr = ""
        if self._proc and self._proc.stderr:
            try:
                self._proc.stderr.flush()
            except Exception:
                pass
        raise HealthTimeoutError(
            f"email sidecar at {self.base_url} did not become healthy within "
            f"{self.health_timeout}s. Last probe error: {last_err}. Check the process "
            f"launched and the port is free."
        )

    def shutdown(self, timeout: float = 5.0) -> None:
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is not None:
            self._proc = None
            return
        pid = proc.pid
        logger.info("email sidecar: tree-killing pid=%s", pid)
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                )
            else:
                os.killpg(os.getpgid(pid), 15)  # SIGTERM to the group
        except (ProcessLookupError, OSError):
            pass
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.warning("email sidecar did not exit in %ss; SIGKILL", timeout)
            try:
                if os.name != "nt":
                    os.killpg(os.getpgid(pid), 9)  # SIGKILL
                else:
                    proc.kill()
            except (ProcessLookupError, OSError):
                pass
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                pass
        self._proc = None
        logger.info("email sidecar: shut down")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_email_sidecar_manager.py -v`
Expected: PASS

- [ ] **Step 5: Add an integration test for real spawn/health/shutdown (dev mode)**

```python
# append to tests/unit/test_email_sidecar_manager.py
import importlib.util


@pytest.mark.skipif(
    importlib.util.find_spec("gaia_agent_email") is None
    or importlib.util.find_spec("uvicorn") is None,
    reason="email agent + uvicorn required for live dev-mode spawn",
)
def test_dev_mode_real_spawn_health_and_treekill(monkeypatch):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    m = mgr.EmailSidecarManager(health_timeout=45.0)
    base = m.start()
    try:
        import requests

        assert requests.get(f"{base}/health", timeout=5).json()["status"] == "ok"
        assert m.is_running
    finally:
        m.shutdown()
    assert not m.is_running
```

Run: `python -m pytest tests/unit/test_email_sidecar_manager.py -v`
Expected: PASS (the live test is skipped if the wheel/uvicorn are absent; install with `uv pip install -e hub/agents/email/python` to exercise it).

- [ ] **Step 6: Commit**

```bash
git add src/gaia/ui/email_sidecar/manager.py tests/unit/test_email_sidecar_manager.py
git commit -m "feat(ui): EmailSidecarManager — mode select, ephemeral port, spawn, health, tree-kill"
```

---

## Task 5: EmailSidecarProxy — forward live routes, gate future ones

**Files:**
- Create: `src/gaia/ui/email_sidecar/proxy.py`
- Modify: `src/gaia/ui/email_sidecar/__init__.py` (export `EmailSidecarManager`, `EmailSidecarProxy`)
- Test: `tests/unit/test_email_sidecar_proxy.py`

**Interfaces:**
- Consumes: `errors.RouteNotAvailableError`.
- Produces:
  - `proxy.EmailSidecarProxy(base_url: str, *, session=None, timeout=900.0)`.
    - `.triage(payload: dict) -> dict` → POST `/v1/email/triage`, returns the response JSON unchanged.
    - `.draft(payload: dict) -> dict` → POST `/v1/email/draft`.
    - `.send(payload: dict) -> dict` → POST `/v1/email/send`.
    - `.health() -> dict` → GET `/health`.
    - `.version() -> dict` → GET `/version`.
    - `.pre_scan_inbox(*a, **k)`, `.search_inbox(...)`, `.archive(...)`, `.quarantine(...)`, `.calendar(...)` → each raises `RouteNotAvailableError` naming the tracking issue.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_email_sidecar_proxy.py
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
import pytest

from gaia.ui.email_sidecar.errors import RouteNotAvailableError
from gaia.ui.email_sidecar.proxy import EmailSidecarProxy


class _Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status
        self.text = str(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Session:
    def __init__(self, payload):
        self._payload = payload
        self.posts = []
        self.gets = []

    def post(self, url, json=None, timeout=None):
        self.posts.append((url, json))
        return _Resp(self._payload)

    def get(self, url, timeout=None):
        self.gets.append(url)
        return _Resp(self._payload)


def test_triage_forwards_and_returns_envelope_unchanged():
    envelope = {"request_kind": "single", "result": {"category": "primary"}}
    sess = _Session(envelope)
    proxy = EmailSidecarProxy("http://127.0.0.1:9100", session=sess)
    out = proxy.triage({"payload": {"kind": "single"}})
    assert out == envelope
    assert sess.posts[0][0] == "http://127.0.0.1:9100/v1/email/triage"


def test_draft_and_send_target_correct_routes():
    sess = _Session({"ok": True})
    proxy = EmailSidecarProxy("http://127.0.0.1:9100", session=sess)
    proxy.draft({"to": [], "subject": "s", "body": "b"})
    proxy.send({"to": [], "subject": "s", "body": "b", "confirmation_token": "t"})
    assert [u for u, _ in sess.posts] == [
        "http://127.0.0.1:9100/v1/email/draft",
        "http://127.0.0.1:9100/v1/email/send",
    ]


def test_health_and_version_get_routes():
    sess = _Session({"status": "ok"})
    proxy = EmailSidecarProxy("http://127.0.0.1:9100", session=sess)
    assert proxy.health()["status"] == "ok"
    proxy.version()
    assert sess.gets == [
        "http://127.0.0.1:9100/health",
        "http://127.0.0.1:9100/version",
    ]


@pytest.mark.parametrize(
    "method,issue",
    [
        ("pre_scan_inbox", "pre-scan"),
        ("search_inbox", "1781"),
        ("archive", "1779"),
        ("quarantine", "1779"),
        ("calendar", "1780"),
    ],
)
def test_future_routes_gated(method, issue):
    proxy = EmailSidecarProxy("http://127.0.0.1:9100", session=_Session({}))
    with pytest.raises(RouteNotAvailableError, match=issue):
        getattr(proxy, method)()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_email_sidecar_proxy.py -v`
Expected: FAIL — `ModuleNotFoundError: gaia.ui.email_sidecar.proxy`

- [ ] **Step 3: Implement proxy**

```python
# src/gaia/ui/email_sidecar/proxy.py
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""EmailSidecarProxy — forward UI email calls to the running sidecar over HTTP.

Forwards the routes that EXIST today (triage / draft / send / health / version)
and returns the sidecar's envelopes unchanged so the existing SSE card pipeline
keeps working. Routes that do not exist yet (inbox pre-scan, search #1781,
archive/quarantine #1779, calendar #1780) are GATED: they raise loudly with the
tracking issue rather than silently no-op — no fallback, no fake success.
"""
from __future__ import annotations

from typing import Optional

from gaia.logger import get_logger
from gaia.ui.email_sidecar.errors import RouteNotAvailableError

logger = get_logger(__name__)


class EmailSidecarProxy:
    def __init__(self, base_url: str, *, session=None, timeout: float = 900.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        if session is None:
            import requests

            session = requests.Session()
        self._session = session

    def _post(self, path: str, payload: dict) -> dict:
        resp = self._session.post(
            f"{self.base_url}{path}", json=payload, timeout=self.timeout
        )
        resp.raise_for_status()
        return resp.json()

    def _get(self, path: str) -> dict:
        resp = self._session.get(f"{self.base_url}{path}", timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    # -- Routes that exist today --------------------------------------------
    def triage(self, payload: dict) -> dict:
        return self._post("/v1/email/triage", payload)

    def draft(self, payload: dict) -> dict:
        return self._post("/v1/email/draft", payload)

    def send(self, payload: dict) -> dict:
        return self._post("/v1/email/send", payload)

    def health(self) -> dict:
        return self._get("/health")

    def version(self) -> dict:
        return self._get("/version")

    # -- Routes not yet built (gated, not silently broken) ------------------
    def _pending(self, capability: str, issue: str):
        raise RouteNotAvailableError(
            f"email {capability} has no REST route on the sidecar yet "
            f"(pending {issue}). The sidecar can only serve inbox features once "
            f"that route lands. This is gated deliberately — no fallback."
        )

    def pre_scan_inbox(self, *args, **kwargs):
        self._pending("inbox pre-scan", "the inbox pre-scan REST route")

    def search_inbox(self, *args, **kwargs):
        self._pending("inbox search", "#1781")

    def archive(self, *args, **kwargs):
        self._pending("archive", "#1779")

    def quarantine(self, *args, **kwargs):
        self._pending("quarantine", "#1779")

    def calendar(self, *args, **kwargs):
        self._pending("calendar", "#1780")
```

Update `src/gaia/ui/email_sidecar/__init__.py` to also export the manager and proxy:

```python
from gaia.ui.email_sidecar.manager import EmailSidecarManager
from gaia.ui.email_sidecar.proxy import EmailSidecarProxy
```
and append `"EmailSidecarManager"`, `"EmailSidecarProxy"` to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_email_sidecar_proxy.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gaia/ui/email_sidecar/proxy.py src/gaia/ui/email_sidecar/__init__.py tests/unit/test_email_sidecar_proxy.py
git commit -m "feat(ui): EmailSidecarProxy — forward live routes, gate pre-scan/search/archive/calendar"
```

---

## Task 6: Dev-mode doc + full-suite verification

**Files:**
- Modify: `docs/guides/email.mdx` (add a short "Dev mode: run the agent from source" subsection)
- (No new test; this task gates on the full affected-path test run + lint.)

- [ ] **Step 1: Add the dev-mode doc subsection**

Append to `docs/guides/email.mdx` a subsection documenting:
- `GAIA_EMAIL_AGENT_MODE=user` (default) runs the published frozen binary; `=dev` runs `uvicorn packaging.server:app --reload` from `hub/agents/email/python`.
- Dev mode requires `uv pip install -e hub/agents/email/python`.
- The sidecar binds an ephemeral local port (never 4001); the UI backend proxies to it.
- This path is additive and gated; the in-process email path is unchanged until the supersede decision (#1767) lands.

```mdx
## Dev mode: run the email agent from source

The Agent UI talks to the email agent as an out-of-process **sidecar**. Two modes,
selected by `GAIA_EMAIL_AGENT_MODE`:

- `user` (default) — runs the published frozen binary, fetched on first email use
  and verified against `binaries.lock.json`.
- `dev` — runs the agent from your local source with hot reload, so prompt/tool
  edits show up live without a freeze → publish cycle:

  ```bash
  uv pip install -e hub/agents/email/python   # once
  GAIA_EMAIL_AGENT_MODE=dev gaia chat --ui
  ```

The sidecar binds an ephemeral local port (never 4001); the Python backend spawns,
health-checks, proxies to, and tree-kills it. Dev mode fails loudly if the source
package is missing — there is no silent fallback to the binary.
```

- [ ] **Step 2: Run the full affected-path test suite**

Run:
```bash
python -m pytest tests/unit/test_email_sidecar_platform.py \
  tests/unit/test_email_sidecar_fetch.py \
  tests/unit/test_email_sidecar_manager.py \
  tests/unit/test_email_sidecar_proxy.py \
  tests/unit/test_email_sidecar_devmode_app.py -v
```
Expected: all PASS (live spawn test may skip without the wheel installed).

- [ ] **Step 3: Lint**

Run: `python util/lint.py --all`
Expected: no errors (auto-fix with `--fix` if formatting differs, then re-run).

- [ ] **Step 4: Commit**

```bash
git add docs/guides/email.mdx
git commit -m "docs(email): document dev/user sidecar modes (GAIA_EMAIL_AGENT_MODE)"
```

---

## Deferred / explicitly NOT in this plan (gated on #1767 sign-off)

- **Wiring the proxy into live sessions.** `_chat_helpers.py` (`agent_type=email`,
  lines ~1300/1776) still constructs the in-process `EmailTriageAgent`. Swapping it
  to the proxy is deferred until @itomek signs off on superseding epic #1767.
- **Removing the in-process `/v1/email` mount** (`server.py:592-601`). Kept until
  the supersede decision; removal coordinates with the #1768 owner.
- **Inbox pre-scan / search (#1781) / archive-quarantine (#1779) / calendar (#1780)
  REST routes.** Owned by other tasks; this plan only *consumes* them and gates the
  proxy methods until they exist.
- **Populating real SHAs in `binaries.lock.json`.** Done by the email-agent publish
  pipeline (`release_agent_email.yml`); until then user-mode fetch refuses the
  placeholder loudly and dev mode is the working path.

## Hardening addendum (post-implementation adversarial review)

After the six tasks landed, an adversarial self-review + a `code-reviewer` pass
found and fixed runtime issues the happy-path tests missed:

- **Pipe-buffer deadlock → log file.** Sidecar stdout/stderr go to
  `~/.gaia/agents/email/logs/sidecar-<port>.log` (pruned to the newest
  `_MAX_SIDECAR_LOGS`), not an undrained `PIPE` that would hang the child once the
  ~64KB OS buffer filled (uvicorn logs a line per request). Failures report the
  log tail.
- **Orphan reaping.** `atexit` reaper tree-kills the sidecar if the backend exits
  without `shutdown()`; unregistered on clean shutdown.
- **Actionable errors preserved.** `EmailSidecarProxy` raises `SidecarHTTPError`
  carrying the sidecar's own `detail` (e.g. `502 local LLM triage failed`) instead
  of a generic `HTTPError`.
- **Contract handshake.** `/version` is read on start; `apiVersion`/`agentVersion`
  captured; a MAJOR mismatch (when a host pins `expected_api_version`) or a missing
  `apiVersion` under a pin fails loudly.
- **Port-race retry.** `start()` retries only the fast early-exit failure on a
  fresh port; a genuine health timeout fails once.
- **Identity check.** Health requires `service == "gaia-agent-email"`, rejecting a
  foreign process that grabbed the ephemeral port.
- **Concurrency.** `start()`/`shutdown()` are serialized by a reentrant lock so a
  concurrent lazy "first email use" spawns exactly one sidecar.
- **Integration seam.** `EmailSidecarManager.proxy()` returns a port-bound proxy;
  the manager is a context manager (`with EmailSidecarManager() as m:`) that
  guarantees shutdown. A live test round-trips a real LLM triage through the
  manager+proxy against the frozen contract.
- **Single app build** in the freeze entrypoint; **async-safety** documented (run
  the sync manager/proxy off the UI event loop via a worker thread).

## Self-Review notes

- Spec coverage: dev-mode enabler (Task 1), `EmailSidecarManager` (Task 4), Python
  verified fetch (Task 3), proxy agent (Task 5) — all four BUILD-NOW items covered.
  Platform/lock loading (Task 2) is the shared dependency. Doc (Task 6).
- Fail-loud: every error path raises a typed `SidecarError` with a remedy; no
  `except: pass`, no user→dev or sidecar→in-process fallback.
- Port 4001: guarded in `find_free_port` and `build_spawn_command`.
- Node-free: only `requests`/`subprocess`/`hashlib`/`socket` — no npm, no `npx`.
- Security boundary: `verify_sha256` tamper test + tampered-download-leaves-no-file
  test in Task 3.
</content>
</invoke>
