# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""GAIA's logs and the model server's, for the Agent UI's log viewer.

Every line is redacted before it is sent (:mod:`gaia.log_reader`). Lines can
still quote a conversation, so the routes need the UI header and refuse
mobile access.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..models import LogReadResponse, LogSourcesResponse
from ..security import require_ui_header, via_tunnel

router = APIRouter(tags=["logs"])

_OVER_TUNNEL = "Read GAIA's logs on the PC running GAIA, not over mobile access."


def _refuse_tunnel(request: Request) -> None:
    if via_tunnel(request):
        raise HTTPException(status_code=403, detail=_OVER_TUNNEL)


@router.get(
    "/api/logs",
    response_model=LogSourcesResponse,
    dependencies=[Depends(require_ui_header)],
)
def list_logs(request: Request) -> Dict[str, Any]:
    """Each log source and the files it has now."""
    from gaia.log_sources import describe_sources

    _refuse_tunnel(request)
    return {"sources": describe_sources()}


@router.get(
    "/api/logs/{source}",
    response_model=LogReadResponse,
    dependencies=[Depends(require_ui_header)],
)
async def read_log(
    source: str,
    request: Request,
    file: Optional[str] = Query(None, max_length=200),
    after: Optional[str] = Query(None, max_length=32),
    limit: int = Query(1000, ge=1, le=5000),
) -> Dict[str, Any]:
    """A log's last lines, or only those after *after* (a cursor it returned)."""
    from gaia.config import GaiaConfigError
    from gaia.llm import lemonade_choice as choice
    from gaia.llm.lemonade_logs import known_secrets, read_lemonade_log
    from gaia.log_reader import LogRead, read_log_file
    from gaia.log_sources import UnknownLogError, find_file

    _refuse_tunnel(request)
    try:
        if source == "model_server":
            result = await read_lemonade_log(after=after, limit=limit)
            return result.to_dict()
        found = find_file(source, file)
    except UnknownLogError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except (choice.LemonadeChoiceError, GaiaConfigError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    if found is None:
        return LogRead(reason="no_file").to_dict()
    secrets, note = await asyncio.to_thread(known_secrets)
    offset = int(after) if after and after.isdigit() else None
    result = await asyncio.to_thread(
        read_log_file, found.path, after=offset, limit=limit, secrets=secrets
    )
    if note and result.detail is None:
        result.detail = note
    return result.to_dict()
