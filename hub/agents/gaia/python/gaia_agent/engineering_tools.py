# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Developer-only handoff tools; consent stays in the host, never in model arguments."""

from __future__ import annotations

import threading
from typing import Any

from gaia.logger import get_logger

logger = get_logger(__name__)
ENGINEERING_SKILL = "gaia-harness-engineering"
ENGINEERING_TOOL_NAMES = (
    "share_engineering_context",
    "engineering_status",
    "append_engineering_context",
    "revoke_engineering_context",
    "approve_engineering_code",
    "open_engineering_app",
)


class EngineeringToolsMixin:
    """Thin model-facing wrapper over the same service used by CLI and MCP."""

    def _engineering_service(self):
        if not getattr(self.config, "developer_mode", False):
            raise PermissionError("Harness engineering requires --developer-mode.")
        if getattr(self, "_engineering", None) is None:
            from gaia.engineering.service import EngineeringService

            self._engineering = EngineeringService(
                developer_mode=self.config.developer_mode
            )
        return self._engineering

    def _start_engineering_setup(self) -> None:
        """Cache initialization must not delay everyday conversations."""
        self._engineering_setup = {
            "status": "starting",
            "message": "Preparing developer source cache and coding app connection instructions.",
        }

        def prepare():
            try:
                self._engineering_setup = self._engineering_service().setup()
            except Exception as exc:  # preserve failure for the user/status tool
                logger.exception("Developer-mode initialization failed")
                self._engineering_setup = {"status": "error", "error": str(exc)}

        threading.Thread(
            target=prepare, daemon=True, name="gaia-engineering-setup"
        ).start()

    def _engineering_consent(self, name: str, args: dict) -> bool:
        """Require a fresh human decision, even in an otherwise unattended session.

        These tool names intentionally have no grant_scope rule, so the existing
        terminal/SSE permission UI offers no persistent 'always' choice.
        """
        console = getattr(self, "console", None)
        if console is None:
            return False
        if console.auto_approve_confirmations_enabled():
            return False
        if console.call_is_granted(name, args):
            return False
        approved = bool(console.confirm_tool_execution(name, args))
        return approved and not console.auto_approve_confirmations_enabled()

    def register_engineering_tools(self) -> dict:
        from gaia.agents.base.tools import tool

        if not self.config.developer_mode:
            raise PermissionError("Harness engineering requires --developer-mode.")
        agent = self
        registry = {}

        def refused() -> dict[str, Any]:
            return {
                "status": "denied",
                "error": "This action requires a fresh explicit user approval. Turn off bypass permissions and approve this specific request.",
            }

        @tool(atomic=True, registry=registry)
        def share_engineering_context(backend: str, summary: str, context: str) -> dict:
            """Ask permission to share exactly this problem summary and context with claude or codex.

            The coding app may send it to its configured cloud provider. Do not
            include credentials. No conversation history is collected automatically.
            Returns the job ID and connection instructions, not a running coding task.
            """
            service = agent._engineering_service()
            if backend not in ("claude", "codex"):
                return {"status": "error", "error": "backend must be claude or codex"}
            args = {
                "backend": backend,
                "summary": summary,
                "context": context,
                "disclosure": "Share this snapshot with the selected coding app and its configured model provider. Its own filesystem permissions remain independent of this bridge.",
            }
            if not agent._engineering_consent("share_engineering_context", args):
                return refused()
            return service.share(backend=backend, summary=summary, context=context)

        @tool(atomic=True, registry=registry)
        def engineering_status(job_id: str = "") -> dict:
            """Inspect source-cache setup or a handoff job, connection instructions and reported progress.

            Omit job_id for startup state. A connected app does not imply a task started.
            """
            if not agent.config.developer_mode:
                raise PermissionError("Harness engineering requires --developer-mode.")
            if not job_id:
                return dict(
                    getattr(agent, "_engineering_setup", {"status": "not_started"})
                )
            return agent._engineering_service().status(job_id)

        @tool(atomic=True, registry=registry)
        def append_engineering_context(job_id: str, context: str) -> dict:
            """Ask explicit permission to share one additional feedback snapshot with this job's coding app."""
            service = agent._engineering_service()
            status = service.status(job_id)
            args = {
                "job_id": job_id,
                "recipient": status.get("backend", "selected coding app"),
                "context": context,
                "disclosure": "Additional snapshot shared with the coding app and its configured model provider.",
            }
            if not agent._engineering_consent("append_engineering_context", args):
                return refused()
            return service.append(job_id, context)

        @tool(atomic=True, registry=registry)
        def revoke_engineering_context(job_id: str) -> dict:
            """Stop future MCP reads for this job; already delivered context cannot be retracted."""
            return agent._engineering_service().revoke(job_id)

        @tool(atomic=True, registry=registry)
        def approve_engineering_code(job_id: str) -> dict:
            """Ask the developer to approve worktree code changes after diagnosis. Does not approve public disclosure."""
            service = agent._engineering_service()
            args = {
                "job_id": job_id,
                "job": service.status(job_id),
                "scope": "Allow coding work in an isolated GAIA worktree after diagnosis; public content still needs review.",
            }
            if not agent._engineering_consent("approve_engineering_code", args):
                return refused()
            return service.approve_code(
                job_id, expected_revision=args["job"]["revision"]
            )

        @tool(atomic=True, registry=registry)
        def open_engineering_app(job_id: str) -> dict:
            """Open the selected native coding app or return supported handoff instructions. Does not submit a prompt automatically."""
            return agent._engineering_service().open(job_id)

        return registry
