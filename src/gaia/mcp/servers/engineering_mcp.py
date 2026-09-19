# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Local stdio context bridge; no consent, publication or arbitrary shell tools."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from gaia.engineering.service import EngineeringService


def create_engineering_mcp(
    root: Path | None, backend: str, token: str, *, developer_mode: bool = False
):
    from mcp.server import MCPServer

    service = EngineeringService(root, developer_mode=developer_mode)
    service.authenticate(backend, token)
    server = MCPServer(name="gaia-engineering")

    def authorize():
        service.authenticate(backend, token)

    @server.tool()
    def connection_status() -> dict:
        """Verify pairing with GAIA without reading any private job."""
        authorize()
        return {
            "connected": True,
            "backend": backend,
            "protocol": 1,
            "task_access": "explicit grants only",
        }

    @server.tool()
    def get_context(job_id: str, after_seq: int = 0) -> dict:
        """Read explicitly approved GAIA context. Updates are pulled, not pushed."""
        authorize()
        return service.store.context(job_id, backend, after_seq)

    @server.tool()
    def get_evidence(job_id: str, evidence_id: str) -> dict:
        """Read one approved evidence item; arbitrary filesystem paths are refused."""
        authorize()
        context = service.store.context(job_id, backend)
        for item in context["evidence"]:
            if item["id"] == evidence_id:
                return item
        raise ValueError("Evidence item not found in this approved job")

    @server.tool()
    def report_diagnosis(job_id: str, report: str) -> dict:
        """Record reproduction, model/configuration checks and recommended remedy."""
        authorize()
        return service.report_diagnosis(job_id, backend, report)

    @server.tool()
    def prepare_worktree(
        job_id: str, operation_id: str, expected_revision: int
    ) -> dict:
        """Prepare a cached GAIA worktree after diagnosis and host code-scope approval."""
        authorize()
        return service.prepare_worktree(
            job_id, backend, operation_id, expected_revision
        )

    @server.tool()
    def register_preview(
        job_id: str, target: str, commit: str, instructions: str
    ) -> dict:
        """Report an isolated native-app preview and how the developer can try it.

        Build and launch in a separate process with isolated state first. This
        records your claim; it neither launches nor validates a preview process.
        """
        authorize()
        return service.register_preview(job_id, backend, target, commit, instructions)

    @server.tool()
    def report_result(job_id: str, report: str) -> dict:
        """Record tests, developer feedback or PR references as unverified reports."""
        authorize()
        return service.report_result(job_id, backend, report)

    return server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--developer-mode",
        action="store_true",
        help="Explicitly enable developer-only MCP capabilities",
    )
    parser.add_argument("--root", type=Path)
    parser.add_argument("--backend", choices=["claude", "codex"], required=True)
    args = parser.parse_args(argv)
    from gaia.mcp.servers.tui_mcp import route_logging_to_stderr

    route_logging_to_stderr()
    server = create_engineering_mcp(
        args.root,
        args.backend,
        os.environ.get("GAIA_ENGINEERING_TOKEN", ""),
        developer_mode=args.developer_mode,
    )
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
