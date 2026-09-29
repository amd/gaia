# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Shared host and coding-client operations, without an agent execution engine."""

from __future__ import annotations

import hashlib
import os
import secrets
import sys
from pathlib import Path

from gaia.engineering.handoff import connection_config, detect_apps, open_app
from gaia.engineering.identity import get_build_identity
from gaia.engineering.repository import Repository, repository_lock
from gaia.engineering.store import (
    JobStore,
    atomic_json,
    clean_context,
    private_directory,
    read_json,
    validate_backend,
)


class EngineeringService:
    """Host methods grant consent; the MCP adapter exposes only scoped operations."""

    def __init__(self, root: Path | None = None, *, developer_mode: bool | None = None):
        enabled = (
            developer_mode
            if developer_mode is not None
            else os.environ.get("GAIA_DEVELOPER_MODE") == "1"
        )
        if not enabled:
            raise PermissionError(
                "Engineering is only available in developer mode; explicitly enable --developer-mode"
            )
        self.store = JobStore(root)
        self.root = self.store.root
        # A custom profile must not share worktrees with the normal installation.
        cache = (
            self.root.parent / "cache" / "engineering"
            if self.root == (Path.home() / ".gaia" / "engineering").resolve()
            else self.root / "cache"
        )
        self.repository = Repository(cache)

    def setup(self) -> dict:
        return {
            "repository": self.repository.ensure_clone(),
            "apps": detect_apps(),
            "message": "Select claude or codex, then configure its local MCP server. Login has not been checked; do not infer that either app is signed out. No conversation has been shared.",
        }

    def connection(self, backend: str) -> dict:
        validate_backend(backend)
        folder = private_directory(self.root / "clients")
        config_path = folder / f"{backend}.json"
        auth_path = folder / f"{backend}.auth.json"
        with repository_lock(folder / f"{backend}.lock"):
            if not config_path.exists() and not auth_path.exists():
                if getattr(sys, "frozen", False):
                    raise RuntimeError(
                        "Configure MCP first from a Python installation with amd-gaia[mcp]: gaia engineering --developer-mode connect "
                        + backend
                        + ". A frozen gaia-agent cannot run Python -m modules."
                    )
                token = secrets.token_urlsafe(32)
                config = connection_config(backend, sys.executable, self.root, token)
                atomic_json(config_path, config)
                atomic_json(
                    auth_path,
                    {"token_sha256": hashlib.sha256(token.encode()).hexdigest()},
                )
            elif not config_path.exists() or not auth_path.exists():
                raise RuntimeError(
                    "Incomplete MCP pairing; remove the two client configuration files and reconnect"
                )
            else:
                read_json(config_path)
                read_json(auth_path)
        return {
            "backend": backend,
            "configuration_file": str(config_path),
            "message": "Add this private stdio configuration to the selected coding app's MCP settings. It contains a pairing credential; do not publish it. Connection alone grants no task access.",
        }

    def authenticate(self, backend: str, token: str) -> None:
        validate_backend(backend)
        if not token:
            raise PermissionError(
                "Set GAIA_ENGINEERING_TOKEN from the private connection configuration"
            )
        auth = read_json(self.root / "clients" / f"{backend}.auth.json")
        if not secrets.compare_digest(
            auth["token_sha256"], hashlib.sha256(token.encode()).hexdigest()
        ):
            raise PermissionError("Invalid engineering client credential")

    def share(self, backend: str, summary: str, context: str) -> dict:
        """Only call after the host obtained explicit approval for this exact snapshot."""
        connection = self.connection(backend)
        job = self.store.create(backend, summary, context, get_build_identity())
        return {
            **self.status(job["id"]),
            "connection": connection,
            "delivery": {
                "state": "snapshot_available",
                "task_created": False,
                "connection_verified": False,
                "detail": "Snapshot saved locally for approved MCP reads. Nothing was posted to a coding app. Configure the client and start its task before claiming an investigation is running.",
            },
            "handoff_prompt": f"Investigate GAIA engineering job {job['id']} using the gaia-engineering MCP server. Read get_context first. Diagnose configuration/model issues before proposing a code change. Private evidence must not appear in public output.",
        }

    def status(self, job_id: str) -> dict:
        job = self.store.read(job_id)
        return {
            key: job[key]
            for key in (
                "id",
                "revision",
                "backend",
                "summary",
                "updated_at",
                "grant",
                "identity",
                "diagnosis",
                "code_approved",
                "worktree",
                "previews",
                "results",
            )
        }

    def append(self, job_id: str, context: str) -> dict:
        self.store.append(job_id, context)
        return self.status(job_id)

    def revoke(self, job_id: str) -> dict:
        self.store.revoke(job_id)
        return self.status(job_id)

    def approve_code(self, job_id: str, *, expected_revision: int) -> dict:
        def mutate(job):
            self.store.require_access(job, job["backend"])
            if not job["diagnosis"]:
                raise ValueError(
                    "Ask the coding agent to report its diagnosis before approving an implementation worktree"
                )
            job["code_approved"] = True

        self.store.update(job_id, mutate, expected_revision=expected_revision)
        return self.status(job_id)

    def open(self, job_id: str) -> dict:
        job = self.store.read(job_id)
        self.store.require_access(job, job["backend"])
        # Before diagnosis, use an evidence-free scratch folder, not a code checkout.
        scratch = private_directory(self.root / "handoffs" / job_id)
        worktree = job["worktree"]
        directory = Path(worktree["path"]) if worktree else scratch
        return open_app(job["backend"], job_id, directory)

    def report_diagnosis(self, job_id: str, backend: str, report: str) -> dict:
        report = clean_context(report)

        def mutate(job):
            self.store.require_access(job, backend)
            job["diagnosis"] = {"report": report, "verification": "reported"}
            job["code_approved"] = False

        self.store.update(job_id, mutate)
        return {
            "recorded": True,
            "message": "Diagnosis recorded as reported. Ask the developer in GAIA to approve code scope before preparing a worktree.",
        }

    def prepare_worktree(
        self, job_id: str, backend: str, operation_id: str, expected_revision: int
    ) -> dict:
        if not operation_id or len(operation_id) > 100:
            raise ValueError("Supply an operation_id of 1–100 characters")

        def mutate(job):
            self.store.require_access(job, backend)
            if not job["code_approved"] or not job["diagnosis"]:
                raise PermissionError(
                    "Diagnosis and explicit code scope approval in GAIA are required"
                )
            if job["worktree"] is None:
                job["worktree"] = self.repository.create_worktree(job_id)
            job["operations"][operation_id] = "prepare_worktree"

        job = self.store.read(job_id)
        self.store.require_access(job, backend)
        if job["operations"].get(operation_id) == "prepare_worktree":
            return job["worktree"]
        return self.store.update(job_id, mutate, expected_revision=expected_revision)[
            "worktree"
        ]

    def report_result(self, job_id: str, backend: str, report: str) -> dict:
        report = clean_context(report)

        def mutate(job):
            self.store.require_access(job, backend)
            if len(job["results"]) >= 100:
                raise ValueError("Result limit reached; start another investigation")
            job["results"].append({"report": report, "verification": "reported"})

        self.store.update(job_id, mutate)
        return {"recorded": True, "verification": "reported"}

    def register_preview(
        self, job_id: str, backend: str, target: str, commit: str, instructions: str
    ) -> dict:
        """Record native-app preview instructions; never claim ownership of its process."""
        if target not in {"agent", "tui", "webui"}:
            raise ValueError("Preview target must be agent, tui or webui")
        import re

        if not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", commit):
            raise ValueError("Record the full tested source commit")
        instructions = clean_context(instructions)

        def mutate(job):
            self.store.require_access(job, backend)
            if not job["worktree"]:
                raise ValueError(
                    "Prepare this job's worktree before reporting a preview"
                )
            if len(job["previews"]) >= 100:
                raise ValueError("Preview limit reached")
            job["previews"].append(
                {
                    "revision": len(job["previews"]) + 1,
                    "target": target,
                    "commit": commit,
                    "instructions": instructions,
                    "verification": "reported",
                    "process_owner": "coding_app",
                }
            )

        job = self.store.update(job_id, mutate)
        return job["previews"][-1]
