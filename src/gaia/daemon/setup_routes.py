# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/daemon/v1/setup`` — the model plan, choices and downloads for the TUI.

Each route is the Agent UI's own ``/api/setup/*`` handler, registered again
behind the daemon token, so the two apps read one plan and make one set of
choices: they can't disagree about what setup downloads.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends


def build_setup_router(token: str) -> APIRouter:
    """Read the plan, change it, and run or follow its downloads."""
    from gaia.daemon.app import build_require_token
    from gaia.ui.routers import setup as ui

    router = APIRouter(
        prefix="/daemon/v1/setup",
        tags=["setup"],
        dependencies=[Depends(build_require_token(token))],
    )
    router.add_api_route("/plan", ui.setup_plan, methods=["GET"])
    router.add_api_route("/chat-model", ui.setup_chat_model, methods=["POST"])
    router.add_api_route("/vision-model", ui.setup_vision_model, methods=["POST"])
    router.add_api_route("/models/skip", ui.setup_skip_model, methods=["POST"])
    router.add_api_route(
        "/models/download", ui.setup_download_model, methods=["POST"], status_code=202
    )
    router.add_api_route("/models/remove", ui.setup_remove_model, methods=["POST"])
    router.add_api_route("/run", ui.setup_run, methods=["POST"])
    router.add_api_route("/status", ui.setup_status, methods=["GET"])
    router.add_api_route("/cancel", ui.setup_cancel, methods=["POST"])
    return router
