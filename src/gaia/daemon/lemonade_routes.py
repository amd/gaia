# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/daemon/v1/lemonade/*`` — the daemon's model server, on request.

The daemon owns the Lemonade process the way it owns a sidecar agent: it starts
it, tracks it, and reaps it on shutdown (see
:class:`gaia.llm.lemonade_supervisor.LemonadeSupervisor`). This router is how a
front-end asks for it. The Go TUI has no other way — it must not spawn a server
itself and must not shell out to the Python CLI — and routing every front-end
through the one supervisor is what makes "one machine-wide instance" true
rather than aspirational.

Blocking by design: the request does not return until the server answers health
or the attempt fails. It runs in a threadpool so the blocking probe loop never
stalls the event loop. A failure is a 503 whose ``detail`` is the supervisor's
own actionable message — never a 200 that quietly means "no LLM".
"""

from __future__ import annotations

from typing import Optional

# Module-level (like broker_routes.py): this module is imported lazily from
# create_app, and the endpoint annotations must resolve from module globals
# under PEP 563.
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from gaia.daemon.constants import API_PREFIX
from gaia.llm.lemonade_supervisor import LemonadeStartError, LemonadeSupervisor
from gaia.logger import get_logger

logger = get_logger(__name__)


class StartRequest(BaseModel):
    """Optional overrides; every field has a host default."""

    # The window the started server must come up with. Omitted means "this
    # machine's device profile" — a server started without one answers /health
    # and then fails long requests, which reads as an agent bug.
    ctx_size: Optional[int] = Field(default=None, gt=0)


def build_lemonade_router(token: str, supervisor: LemonadeSupervisor) -> APIRouter:
    """Token-guarded routes over the daemon's *supervisor*."""
    from gaia.daemon.app import build_require_token

    require_token = build_require_token(token)
    router = APIRouter(
        prefix=f"{API_PREFIX}/lemonade",
        tags=["lemonade"],
        dependencies=[Depends(require_token)],
    )

    @router.post("/start")
    async def start(body: Optional[StartRequest] = None) -> dict:
        from gaia.config import GaiaConfigError

        ctx_size = body.ctx_size if body else None
        if ctx_size is None:
            try:
                ctx_size = _profile_ctx_size()
            except GaiaConfigError as e:
                raise HTTPException(status_code=503, detail=str(e)) from e

        try:
            state = await run_in_threadpool(supervisor.ensure_running, ctx_size)
        except LemonadeStartError as e:
            logger.warning("lemonade: start refused: %s", e)
            raise HTTPException(status_code=503, detail=str(e)) from e

        if state.started:
            logger.info(
                "lemonade: started at %s in %.1fs (pid=%s)",
                state.base_url,
                state.waited_seconds,
                state.pid,
            )
        return _payload(state, ctx_size)

    @router.get("/status")
    def status() -> dict:
        """What the daemon knows about the server, without starting one.

        ``supervised`` distinguishes a server this daemon owns and will reap
        from one it merely found — the two behave differently at shutdown, and
        a status that blurred them would make that surprising.
        """
        return {
            "supervised": supervisor.is_running,
            "pid": supervisor.pid,
            "log_path": str(supervisor.log_path()),
        }

    return router


def _payload(state, ctx_size: int) -> dict:
    return {
        "status": "started" if state.started else "already_running",
        "base_url": state.base_url,
        "ctx_size": ctx_size,
        "supervised": state.owned,
        "pid": state.pid,
        "waited_seconds": round(state.waited_seconds, 2),
    }


def _profile_ctx_size() -> int:
    """This machine's pinned context window, from the persisted device profile.

    Resolves through the same ``GaiaConfig.default_device`` →
    ``profile_ctx_size`` path ``gaia.cli`` uses to size its own model loads, so
    a server the daemon starts serves the window every GAIA agent will ask for.
    A corrupt config raises ``GaiaConfigError`` rather than guessing: the NPU
    profile's ceiling is half the GPU one, and picking the wrong side fails the
    model load outright.
    """
    from gaia.config import GaiaConfig
    from gaia.llm.lemonade_client import profile_ctx_size

    return profile_ctx_size(GaiaConfig.load().default_device)
