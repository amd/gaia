# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Private, atomic job records shared by GAIA and its local MCP clients."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import tempfile
import time
from pathlib import Path
from typing import Callable

from gaia.engineering.repository import repository_lock

BACKENDS = frozenset({"claude", "codex"})
MAX_CONTEXT_BYTES = 128 * 1024
MAX_RECORD_BYTES = 2 * 1024 * 1024
_ID = re.compile(r"[a-f0-9]{32}\Z")


def default_root() -> Path:
    """An explicit test/profile root never aliases the normal engineering store."""
    return (
        Path(
            os.environ.get(
                "GAIA_ENGINEERING_HOME", Path.home() / ".gaia" / "engineering"
            )
        )
        .expanduser()
        .resolve()
    )


def private_directory(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError("Engineering directories must not be symlinks")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    if os.name == "nt":
        _lock_down_windows_acl(path)
    return path


def _lock_down_windows_acl(path: Path) -> None:
    """Restrict *path*'s DACL to the current user only — ``chmod 0700`` above
    is a no-op on NTFS (#2250: the same gap already fixed for the daemon's
    launch secret in ``gaia.daemon.sidecars.manager``). A shared snapshot or
    grant left world-readable on a shared Windows box is exactly what this
    store exists to prevent, so a failure here must not be swallowed.
    """
    try:
        import ntsecuritycon
        import win32api
        import win32security
    except ImportError as e:
        raise RuntimeError(
            f"cannot lock down {path} to the current user: pywin32 is not "
            "installed. There is no fallback — an engineering directory "
            "without an owner-only ACL is readable by other local accounts "
            "on NTFS. Run `pip install pywin32` (it ships as a core "
            "amd-gaia dependency on Windows). See issue #2250."
        ) from e

    try:
        token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(), win32security.TOKEN_QUERY
        )
        user_sid, _attributes = win32security.GetTokenInformation(
            token, win32security.TokenUser
        )

        dacl = win32security.ACL()
        dacl.AddAccessAllowedAce(
            win32security.ACL_REVISION, ntsecuritycon.FILE_ALL_ACCESS, user_sid
        )
        win32security.SetNamedSecurityInfo(
            str(path),
            win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION
            | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
            None,
            None,
            dacl,
            None,
        )

        sd = win32security.GetNamedSecurityInfo(
            str(path),
            win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION,
        )
        result_dacl = sd.GetSecurityDescriptorDacl()
        if result_dacl is None:
            raise RuntimeError(
                f"{path} has no DACL after lockdown (a null DACL grants "
                "everyone access on Windows) — refusing to use an "
                "unprotected engineering directory."
            )
        for i in range(result_dacl.GetAceCount()):
            _ace_type_flags, _mask, ace_sid = result_dacl.GetAce(i)
            if ace_sid != user_sid:
                raise RuntimeError(
                    f"{path} DACL still grants access to a SID other than "
                    "the current user after lockdown — refusing to use an "
                    "engineering directory other local users could read."
                )
    except RuntimeError:
        raise
    except Exception as e:
        # pywin32 raises pywintypes.error (not OSError) on API failures.
        raise RuntimeError(
            f"could not lock down {path} to the current user via a Windows " f"ACL: {e}"
        ) from e


def atomic_json(path: Path, value: dict) -> None:
    """Replace an owner-only record; never expose a partly written grant."""
    payload = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    if len(payload) > MAX_RECORD_BYTES:
        raise ValueError("Engineering record is full; create a new handoff")
    private_directory(path.parent)
    fd, name = tempfile.mkstemp(prefix=".record-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path: Path) -> dict:
    if path.is_symlink() or path.stat().st_size > MAX_RECORD_BYTES:
        raise ValueError("Unsafe or oversized engineering record")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Engineering record must be an object")
    return value


def validate_backend(backend: str) -> str:
    if backend not in BACKENDS:
        raise ValueError("Select claude or codex")
    return backend


def clean_context(text: str) -> str:
    """Remove terminal controls and common credentials before storing evidence."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Context must be nonempty text")
    if len(text.encode("utf-8")) > MAX_CONTEXT_BYTES:
        raise ValueError("Select at most 128 KiB of context per snapshot")
    text = re.sub(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))", "", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)
    text = re.sub(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        "[REDACTED PRIVATE KEY]",
        text,
        flags=re.S,
    )
    text = re.sub(
        r"(?i)([A-Za-z0-9_.-]*"
        r"(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|secret|authorization)"
        # A suffix needs a separator, so "secretary:" stays prose.
        r"(?:[_-][A-Za-z0-9_.-]*)?[\"']?\s*[:=]\s*)([^\n]+)",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(
        r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b",
        "[REDACTED]",
        text,
    )
    return text


class JobStore:
    """Operations require host consent; MCP is only given the read/report subset."""

    def __init__(self, root: Path | None = None):
        raw_root = Path(root) if root is not None else default_root()
        if raw_root.is_symlink():
            raise ValueError("Engineering root must not be a symlink")
        self.root = private_directory(raw_root.expanduser().resolve())
        self.jobs = private_directory(self.root / "jobs")

    def path(self, job_id: str) -> Path:
        if not isinstance(job_id, str) or not _ID.fullmatch(job_id):
            raise ValueError("Invalid engineering job ID")
        folder = self.jobs / job_id
        if folder.is_symlink():
            raise ValueError("Engineering job cannot be a symlink")
        return folder / "job.json"

    def read(self, job_id: str) -> dict:
        job = read_json(self.path(job_id))
        if job.get("schema") != 1 or job.get("id") != job_id:
            raise ValueError("Unsupported or corrupt engineering job")
        return job

    def update(
        self,
        job_id: str,
        mutate: Callable[[dict], None],
        *,
        expected_revision: int | None = None,
    ) -> dict:
        path = self.path(job_id)
        with repository_lock(path.parent / "job.lock"):
            job = self.read(job_id)
            if expected_revision is not None and job["revision"] != expected_revision:
                raise ValueError("Job changed; read its current revision and retry")
            mutate(job)
            job["revision"] += 1
            job["updated_at"] = time.time()
            atomic_json(path, job)
            return job

    def create(self, backend: str, summary: str, context: str, identity: dict) -> dict:
        validate_backend(backend)
        summary = clean_context(summary)
        if len(summary) > 2000:
            raise ValueError("Keep the task summary under 2000 characters")
        text = clean_context(context)
        now = time.time()
        job_id = secrets.token_hex(16)
        job = {
            "schema": 1,
            "id": job_id,
            "revision": 1,
            "backend": backend,
            "summary": summary,
            "created_at": now,
            "updated_at": now,
            "grant": {
                "mode": "snapshot",
                "expires_at": now + 24 * 3600,
                "revoked": False,
            },
            "identity": identity,
            "evidence": [self.evidence(1, text)],
            "diagnosis": None,
            "code_approved": False,
            "worktree": None,
            "previews": [],
            "results": [],
            "operations": {},
        }
        atomic_json(self.path(job_id), job)
        return job

    @staticmethod
    def evidence(seq: int, text: str) -> dict:
        return {
            "id": str(seq),
            "seq": seq,
            "text": text,
            "captured_at": time.time(),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }

    @staticmethod
    def require_access(job: dict, backend: str) -> None:
        grant = job["grant"]
        if (
            job["backend"] != validate_backend(backend)
            or grant["revoked"]
            or grant["expires_at"] <= time.time()
        ):
            raise PermissionError("No active sharing grant for this client and job")

    def context(self, job_id: str, backend: str, after_seq: int = 0) -> dict:
        if (
            isinstance(after_seq, bool)
            or not isinstance(after_seq, int)
            or after_seq < 0
        ):
            raise ValueError("after_seq must be a nonnegative integer")
        job = self.read(job_id)
        self.require_access(job, backend)
        return {
            "id": job_id,
            "revision": job["revision"],
            "summary": job["summary"],
            "identity": job["identity"],
            "grant": job["grant"],
            "evidence": [e for e in job["evidence"] if e["seq"] > after_seq],
            "next_seq": len(job["evidence"]),
            "code_approved": job["code_approved"],
            "worktree": job["worktree"],
            "diagnosis": job["diagnosis"],
        }

    def append(self, job_id: str, context: str) -> dict:
        text = clean_context(context)

        def mutate(job):
            self.require_access(job, job["backend"])
            job["evidence"].append(self.evidence(len(job["evidence"]) + 1, text))

        return self.update(job_id, mutate)

    def revoke(self, job_id: str) -> dict:
        def mutate(job):
            job["grant"]["revoked"] = True

        return self.update(job_id, mutate)
