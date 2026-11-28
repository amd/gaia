# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The two programs that can be the GAIA agent: what each weighs, and getting gaiacpp.

``GET /api/agent-programs`` lists gaiapy and gaiacpp with whether each is on
this PC and its download size. ``POST /api/agent-programs/gaiacpp/download``
starts the verified download in the background; ``GET`` on the same path
reports its bytes so the UI can draw a progress bar. Choosing the agent itself
stays ``PUT /api/settings``.
"""

import importlib.util
import threading
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool

from gaia.daemon.sidecars import fetch, gaiacpp_fetch
from gaia.daemon.sidecars import platform as plat
from gaia.daemon.sidecars.errors import (
    KIND_NO_RELEASE,
    IntegrityError,
    PlatformError,
    SidecarError,
)
from gaia.logger import get_logger
from gaia.ui.security import FLAGSHIP_AGENT_ID, require_local_pc, require_ui_header

logger = get_logger(__name__)
router = APIRouter()

_SIZE_TTL_S = 600.0
_lock = threading.Lock()
# One measurement at a time: each is up to three network calls.
_measuring = threading.Lock()
# "at" is None when nothing is cached: 0.0 would read as fresh on a just-booted PC.
_sizes: Dict[str, Any] = {"at": None, "values": {}, "reasons": {}, "code": None}
# Shown under the gaiacpp option; the addresses and error text go to the log.
# Each has a code the Agent UI translates; a reason with no code is shown as it is.
_UNREACHABLE = (
    "GAIA couldn't get gaiacpp's details from the download server. Check your "
    "internet connection, or try again later."
)
_UNVERIFIABLE = (
    "this GAIA release's gaiacpp can't be verified, so GAIA won't install it. "
    "Use the Python agent, or update GAIA."
)
_job: Dict[str, Any] = {
    "state": "idle",
    "bytes_done": 0,
    "bytes_total": None,
    "error": None,
}


def _gaiapy_size() -> Optional[int]:
    """This PC's download size for the Python agent, from the hub manifest."""
    from gaia.hub import catalog, installer

    manifest = catalog.fetch_manifest(FLAGSHIP_AGENT_ID)
    return installer._artifact_size(manifest, None) or None


def _measure() -> None:
    values: Dict[str, Optional[int]] = {}
    reasons: Dict[str, Optional[str]] = {}
    code: Optional[str] = None
    lasting = True
    try:
        values[fetch.PYTHON_FLAGSHIP] = _gaiapy_size()
    except Exception as e:  # noqa: BLE001 - a size is information, never a gate
        logger.warning("agent programs: no size for gaiapy: %s", e)
        values[fetch.PYTHON_FLAGSHIP] = None
    try:
        values[fetch.NATIVE_FLAGSHIP] = gaiacpp_fetch.download_info().size
        reasons[fetch.NATIVE_FLAGSHIP] = None
    except PlatformError as e:
        logger.warning("agent programs: gaiacpp cannot be downloaded: %s", e)
        values[fetch.NATIVE_FLAGSHIP] = None
        reasons[fetch.NATIVE_FLAGSHIP] = str(e)
        code = KIND_NO_RELEASE if e.kind == KIND_NO_RELEASE else None
    except IntegrityError as e:
        logger.warning("agent programs: gaiacpp cannot be verified: %s", e)
        values[fetch.NATIVE_FLAGSHIP] = None
        reasons[fetch.NATIVE_FLAGSHIP] = _UNVERIFIABLE
        code = "unverifiable"
    except (SidecarError, RuntimeError) as e:
        # The network, not the release: asked again on the next request.
        logger.warning("agent programs: gaiacpp release not reachable: %s", e)
        values[fetch.NATIVE_FLAGSHIP] = None
        reasons[fetch.NATIVE_FLAGSHIP] = _UNREACHABLE
        code = "unreachable"
        lasting = False
    with _lock:
        _sizes.update(
            at=time.monotonic() if lasting else None,
            values=values,
            reasons=reasons,
            code=code,
        )


def _gaiapy_installed() -> bool:
    """True when the Python agent can answer: importable, or a binary setup installed."""
    if importlib.util.find_spec("gaiapy") is not None:
        return True
    if fetch.installer_placed_flagship() is not None:
        return True
    try:
        hub = fetch._hub_installed_binary(
            fetch.default_cache_dir(FLAGSHIP_AGENT_ID), plat.current_platform_key()
        )
    except SidecarError as e:
        logger.warning(
            "agent programs: the installed Python agent failed its check: %s", e
        )
        return False
    return hub is not None


#: ``chosen_agent_problem()`` codes: why the chosen gaiacpp can't be started.
GAIACPP_MISSING = "gaiacpp_missing"
GAIACPP_INVALID = "gaiacpp_invalid"


def chosen_agent_problem() -> Optional[Dict[str, str]]:
    """Why the agent the user chose can't start, as ``{"code", "detail"}``; else ``None``.

    Only gaiacpp has such a reason of its own: it is chosen but not on this
    PC, or no longer matches what was downloaded.
    """
    if fetch.chosen_flagship() != fetch.NATIVE_FLAGSHIP:
        return None
    try:
        binary = fetch.verified_native_flagship()
    except IntegrityError as e:
        return {"code": GAIACPP_INVALID, "detail": str(e)}
    if binary is None:
        looked = ", ".join(str(p) for p in fetch.native_flagship_candidates())
        return {
            "code": GAIACPP_MISSING,
            "detail": (
                "gaiacpp is chosen as the agent but it is not installed on this "
                f"PC (looked in: {looked})."
            ),
        }
    return None


def _fails_check() -> bool:
    """True when the gaiacpp on this PC no longer matches what was downloaded."""
    try:
        fetch.verified_native_flagship()
    except IntegrityError as e:
        logger.warning("agent programs: gaiacpp failed its check: %s", e)
        return True
    return False


def _installed_version(binary) -> Optional[str]:
    """The version of the gaiacpp on this PC, for the Agent card; ``None`` when it can't be read."""
    try:
        return gaiacpp_fetch.installed_version(binary)
    except (SidecarError, OSError) as e:
        logger.warning("agent programs: no version for %s: %s", binary, e)
        return None


def _job_view() -> Dict[str, Any]:
    with _lock:
        return dict(_job)


def _fresh() -> bool:
    with _lock:
        at = _sizes["at"]
        return at is not None and time.monotonic() - at <= _SIZE_TTL_S


def _listing() -> Dict[str, Any]:
    if not _fresh():
        with _measuring:
            if not _fresh():
                _measure()
    with _lock:
        values, reasons = dict(_sizes["values"]), dict(_sizes["reasons"])
        native_code = _sizes["code"]
    native_path = fetch.native_flagship()
    native_size = values.get(fetch.NATIVE_FLAGSHIP)
    native_reason = reasons.get(fetch.NATIVE_FLAGSHIP)
    update = native_path is not None and gaiacpp_fetch.update_available(native_path)
    # A copy that fails its check is replaced the same way a missing one is had.
    broken = native_path is not None and _fails_check()
    if native_path is not None and native_size is None:
        native_size = native_path.stat().st_size
    return {
        "current": fetch.saved_flagship(),
        "programs": [
            {
                "id": fetch.PYTHON_FLAGSHIP,
                "installed": _gaiapy_installed(),
                "size_bytes": values.get(fetch.PYTHON_FLAGSHIP),
                "can_download": False,
                "update_available": False,
                "unavailable_reason": None,
                "unavailable_code": None,
                "version": None,
                "release_version": None,
            },
            {
                "id": fetch.NATIVE_FLAGSHIP,
                "installed": native_path is not None,
                "size_bytes": native_size,
                "can_download": (native_path is None or update or broken)
                and native_reason is None,
                "update_available": update and native_reason is None,
                "unavailable_reason": (
                    native_reason if native_path is None or broken else None
                ),
                "unavailable_code": (
                    native_code if native_path is None or broken else None
                ),
                # Both set and different when the copy is from another release.
                "version": (
                    _installed_version(native_path) if native_path is not None else None
                ),
                "release_version": gaiacpp_fetch.release_version(),
            },
        ],
        "download": _job_view(),
    }


@router.get("/api/agent-programs")
async def list_agent_programs():
    """Both agent programs, with install state, size and any running download."""
    return await run_in_threadpool(_listing)


def _on_progress(done: int, total: Optional[int]) -> None:
    with _lock:
        _job.update(bytes_done=done, bytes_total=total)


def _run_download() -> None:
    try:
        if fetch.saved_flagship() == fetch.NATIVE_FLAGSHIP:
            # An update replaces the file the running agent was started from.
            from gaia.ui.sidecar import daemon_client

            daemon_client.stop_sidecar(FLAGSHIP_AGENT_ID)
        gaiacpp_fetch.download(_on_progress)
    except Exception as e:  # noqa: BLE001 - reported to the UI through the job
        logger.warning("agent programs: gaiacpp download failed: %s", e)
        with _lock:
            _job.update(state="failed", error=str(e))
        return
    with _lock:
        _job.update(state="done", error=None)
        _sizes["at"] = None


def _require_gaiacpp(program_id: str) -> None:
    if program_id != fetch.NATIVE_FLAGSHIP:
        raise HTTPException(
            status_code=404,
            detail=(
                f"'{program_id}' is not downloaded here. Only gaiacpp is; the "
                "Python agent is installed by GAIA's setup."
            ),
        )


def _refuse_while_agent_is_busy() -> None:
    """An update stops the running gaiacpp; refuse while it is answering or under test."""
    from gaia.ui.run_manager import run_manager
    from gaia.ui.setup_runner import runner as setup_runner

    if (setup_runner.status() or {}).get("state") in ("running", "verifying"):
        raise HTTPException(
            status_code=409,
            detail=(
                "GAIA is still setting up and testing the agent. Update gaiacpp "
                "once setup has finished."
            ),
        )
    if run_manager.active_sessions():
        raise HTTPException(
            status_code=409,
            detail=(
                "GAIA is answering in a chat, and updating gaiacpp now would cut "
                "that answer off. Let it finish or stop it, then update."
            ),
        )


def _start_download() -> Dict[str, Any]:
    installed = fetch.native_flagship()
    if (
        installed is not None
        and not gaiacpp_fetch.update_available(installed)
        and not _fails_check()
    ):
        raise HTTPException(
            status_code=409, detail="gaiacpp is already installed on this PC."
        )
    if installed is not None and fetch.saved_flagship() == fetch.NATIVE_FLAGSHIP:
        _refuse_while_agent_is_busy()
    with _lock:
        if _job["state"] == "downloading":
            return dict(_job)
        _job.update(state="downloading", bytes_done=0, bytes_total=None, error=None)
    threading.Thread(target=_run_download, name="gaiacpp-download", daemon=True).start()
    return _job_view()


# Downloads and installs a program on this PC, so only someone at this PC may ask.
@router.post(
    "/api/agent-programs/{program_id}/download",
    status_code=202,
    dependencies=[Depends(require_ui_header), Depends(require_local_pc)],
)
async def start_agent_program_download(program_id: str):
    """Start downloading gaiacpp in the background; poll the same path with GET."""
    _require_gaiacpp(program_id)
    return await run_in_threadpool(_start_download)


@router.get("/api/agent-programs/{program_id}/download")
async def agent_program_download_status(program_id: str):
    """Bytes downloaded so far, the total when known, and how it ended."""
    _require_gaiacpp(program_id)
    return _job_view()
