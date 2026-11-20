# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""First-run setup endpoints: check readiness, run ``gaia init``, follow it.

See :mod:`gaia.ui.setup_runner`. The web UI and the TUI answer "is GAIA set up?"
with the same ``gaia init --check``, so they can never disagree.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from gaia.installer.model_choice import (
    ROLE_CHAT,
    ROLE_EMBEDDING,
    ROLE_IMAGE_CREATION,
    ROLE_SPEECH,
    ROLE_VISION,
    ROLE_VOICE,
)
from gaia.llm.lemonade_choice import MODE_EXTERNAL, MODE_GAIA
from gaia.llm.npu_linux import ACCESS, DEVICE, DRIVER, FIRMWARE, MEMLOCK, RUNTIME
from gaia.logger import get_logger

from .. import setup_plan as plan_api
from ..database import ChatDatabase
from ..dependencies import get_db
from ..models import WireModel
from ..security import refuse_on_device_listener, require_local_pc, require_ui_header
from ..setup_plan import (
    CantRunHere,
    ServerNotRunning,
    SetupPlanError,
    UnknownModel,
    build_plan,
    choose_chat_model,
)
from ..setup_runner import (
    MODEL_STATES,
    PHASES,
    REPORT_NAME_RE,
    SetupBusy,
    SetupError,
    agent_ui_chat_model,
    check,
    is_initialized,
    runner,
    step_labels,
    users_lemonade_url,
    write_report,
)

logger = get_logger(__name__)
router = APIRouter(tags=["setup"])

# Fixed words for each failure: what failed, what to do, where the details are.
# Exception text stays in the server log, never in a response.
# Neutral: the daemon serves these same handlers to the TUI.
_LOG = "Details are in the GAIA server log."
_SERVER_DOWN = (
    "The model server isn't running yet. Start setup first, then try again. " + _LOG
)
_PLAN_FAILED = (
    "GAIA couldn't work out the setup plan. Check that the model server is "
    "running, then try again. " + _LOG
)
_CHECK_FAILED = "GAIA couldn't check whether setup is done. Try again. " + _LOG
_BUSY = (
    "Setup or that model's download is already running. Wait for it or cancel "
    "it, then try again."
)
_START_FAILED = "Setup couldn't start. Try again. " + _LOG

_Number = Union[int, float]
_Role = Literal[
    ROLE_CHAT,
    ROLE_VISION,
    ROLE_EMBEDDING,
    ROLE_SPEECH,
    ROLE_VOICE,
    ROLE_IMAGE_CREATION,
]
_ModelState = Literal[MODEL_STATES]  # type: ignore[valid-type]
_Phase = Literal[PHASES]  # type: ignore[valid-type]


class SetupStepView(WireModel):
    """One step of setup as the user reads it; ``status`` only while a run is on."""

    key: _Phase
    label: str
    detail: str
    status: Optional[Literal["pending", "active", "done", "failed", "cancelled"]] = None


class SetupModelLoad(WireModel):
    """One model the load check tried."""

    id: str
    role: str
    size_gb: Optional[_Number]
    loaded: bool
    error: Optional[str]
    #: Whether the server already has it; null when not checked.
    downloaded: Optional[bool]
    #: Why it failed; only ``broken`` says the file itself is bad.
    kind: Optional[str]


class SetupCheckResponse(WireModel):
    """``gaia init --check --json`` plus the step lists the screen shows."""

    ready: bool
    stage: Optional[str]
    reasons: List[str]
    models: List[SetupModelLoad]
    agent_missing: bool
    warnings: List[str]
    no_agent_build: bool
    model_list_unread: bool
    initialized: bool
    steps: List[SetupStepView]
    cloud_steps: List[SetupStepView]


class SetupLemonade(WireModel):
    """The model server setup will use."""

    mode: Literal[MODE_GAIA, MODE_EXTERNAL]
    url: Optional[str]
    reachable: bool


class SetupChatModel(WireModel):
    """The chat model setup starts from."""

    id: str
    user_set: bool
    size_gb: Optional[_Number]
    downloaded: Optional[bool]


class SetupOption(WireModel):
    """One default chat model and whether this PC can run it."""

    id: str
    size_gb: Optional[_Number]
    fits: Optional[bool]
    reason: Optional[str]


class SetupDisk(WireModel):
    """The drive models download to."""

    path: Optional[str]
    free_gb: Optional[_Number]
    source: Optional[Literal["lemonade", "local"]]


class SetupCatalogEntry(WireModel):
    """One model setup can pull or use. Every ``*_gb`` is decimal GB."""

    id: str
    name: str
    roles: List[_Role]
    purpose: str
    if_skipped: Optional[str]
    recipe: Optional[str]
    size_gb: Optional[_Number]
    full_size_gb: Optional[_Number]
    partial: bool
    partial_gb: _Number
    downloaded: Optional[bool]
    in_lemonade: bool
    recommended: bool
    planned: bool
    gaia_model: bool
    selected: bool
    skipped: bool
    chat_choice: bool
    support: Optional[Literal["verified", "untested"]]
    reads_images: bool
    feature_tier: Literal["compact", "full"]
    source: Optional[str]
    fits: Optional[bool]
    need_gb: Optional[_Number]
    capacity_gb: Optional[_Number]
    fit_reason: Optional[str]
    fit_code: Optional[Literal["memory", "npu", "backend"]]


class SetupPlanModel(SetupCatalogEntry):
    """A catalog entry setup will download, with its role in the plan."""

    role: Literal["chat", "embedding"]


class SetupComponent(WireModel):
    """Something setup fetches besides the models."""

    id: Literal["server", "backend", "agent"]
    version: Optional[str]
    size_gb: Optional[_Number]
    source: str
    #: The server only: true when the GAIA installer carries its archive.
    bundled: Optional[bool] = None
    installed: Optional[bool]


class SetupBackend(WireModel):
    """A backend Lemonade reports for this PC."""

    recipe: str
    backend: str
    state: Optional[str]
    version: Optional[str]
    default: bool


class SetupNpuProblem(WireModel):
    """One thing between this PC and running a model on its NPU."""

    code: Literal[DRIVER, DEVICE, FIRMWARE, ACCESS, MEMLOCK, RUNTIME]
    title: str
    fix: str
    params: Dict[str, str]


class SetupNpuStatus(WireModel):
    """Whether this PC has an NPU and what stops a model running on it."""

    present: bool
    ready: bool
    problems: List[SetupNpuProblem]


class SetupDevice(WireModel):
    """A processor Lemonade found."""

    kind: str
    name: str
    family: Optional[str]
    available: bool
    integrated: Optional[bool]
    cores: Optional[_Number]
    threads: Optional[_Number]


class SetupGaiaBuild(WireModel):
    """Which GAIA is running."""

    version: str
    kind: Literal["release", "release_candidate", "local", "source"]
    rc: Optional[str]
    commit: Optional[str]


class SetupSystem(WireModel):
    """What setup found across GAIA, the model server and this PC."""

    gaia_build: SetupGaiaBuild
    gaia_version: str
    gaia_home: str
    agent_version: Optional[str]
    lemonade_version: Optional[str]
    os: Optional[str]
    processor: Optional[str]
    physical_memory: Optional[str]
    devices: List[SetupDevice]


class SetupPlanResponse(WireModel):
    """What setup will use and download, before it starts."""

    profile: str
    lemonade: SetupLemonade
    chat_model: SetupChatModel
    recommended_model: Optional[str]
    decided_after_server: bool
    memory_gb: Optional[int]
    memory_source: Optional[str]
    capacity_gb: Optional[_Number]
    disk: SetupDisk
    download_gb: Optional[_Number]
    disk_warning: Optional[str]
    skipped_models: List[str]
    options: List[SetupOption]
    models: List[SetupPlanModel]
    catalog: List[SetupCatalogEntry]
    components: List[SetupComponent]
    backends: List[SetupBackend]
    npu: SetupNpuStatus
    system: SetupSystem
    vision_model: Optional[str]


class SetupRemoveModelResponse(SetupPlanResponse):
    """The new plan after a model is removed, and the space it freed."""

    freed_gb: _Number


class SetupNeedModel(WireModel):
    """The model a job is missing."""

    id: str
    name: str
    size_gb: Optional[_Number]
    source: Optional[str]


class SetupModelNeedResponse(WireModel):
    """Whether a job needs a model that isn't on this PC; ``off`` says why none is offered."""

    job: _Role
    needed: bool
    model: Optional[SetupNeedModel]
    disk_free_gb: Optional[_Number]
    off: Optional[Literal["npu"]] = None


class SetupProblemView(WireModel):
    """A failed setup with a known fix: the user's model server."""

    kind: Literal["server_unreachable", "server_too_old"]
    message: str
    start: Optional[str]
    use_gaia: bool
    env_url: Optional[str]


class SetupModelProgress(WireModel):
    """One model's download. Sizes in bytes; speed in bytes per second."""

    id: str
    roles: List[_Role]
    state: _ModelState
    bytes_done: _Number
    bytes_total: _Number
    speed_bps: _Number
    eta_s: Optional[int]
    error: Optional[str]
    error_kind: Optional[str]
    can_retry: bool
    retrying_until: Optional[_Number]
    downloaded_before_gb: Optional[_Number]
    log: List[str]


class SetupOverall(WireModel):
    """Bytes-weighted progress across the models being set up."""

    bytes_done: _Number
    bytes_total: _Number
    speed_bps: _Number
    eta_s: Optional[int]
    percent: Optional[int]
    failed: int


class SetupTestRow(WireModel):
    """One row of the Test step; the fields come from ``gaia init``'s events."""

    job: Optional[str]
    subject: Optional[str]
    state: Optional[str]
    error: Optional[str]
    kind: Optional[str]
    seconds: Optional[_Number]


class SetupRunStatus(WireModel):
    """The running (or last) setup. ``idle`` carries only ``state`` and, after a
    single model download, ``models``, ``overall`` and ``logs``."""

    state: Literal["idle", "running", "verifying", "ready", "failed", "cancelled"]
    steps: Optional[List[SetupStepView]] = None
    text: Optional[str] = None
    percent: Optional[int] = None
    size: Optional[str] = None
    error: Optional[str] = None
    problem: Optional[SetupProblemView] = None
    command: Optional[str] = None
    log_tail: Optional[List[str]] = None
    skip_chat_model: Optional[bool] = None
    gaia_lemonade: Optional[bool] = None
    server_only: Optional[bool] = None
    upgrade_embedded: Optional[bool] = None
    model_failed: Optional[bool] = None
    components: Optional[Dict[str, str]] = None
    component_kinds: Optional[Dict[str, str]] = None
    tests: Optional[List[SetupTestRow]] = None
    chat_model: Optional[str] = None
    models: Optional[List[SetupModelProgress]] = None
    overall: Optional[SetupOverall] = None
    logs: Optional[Dict[str, List[str]]] = None


class SetupReportResponse(WireModel):
    """The troubleshooting bundle just written."""

    name: str
    path: str
    size_bytes: int


class SetupCancelResponse(WireModel):
    """Whether anything was stopped."""

    cancelled: bool


def _plan_error(e: SetupPlanError, unknown_model: str) -> HTTPException:
    """The response for a failed plan call; *unknown_model* is the 422 wording."""
    logger.exception("Setup plan request failed")
    if isinstance(e, ServerNotRunning):
        return HTTPException(status_code=503, detail=_SERVER_DOWN)
    if isinstance(e, UnknownModel):
        return HTTPException(status_code=422, detail=unknown_model)
    if isinstance(e, CantRunHere):
        # Our own sentence: the model and Lemonade's reason, nothing internal.
        return HTTPException(status_code=409, detail=str(e))
    return HTTPException(status_code=503, detail=_PLAN_FAILED)


def _run_error(e: SetupError) -> HTTPException:
    logger.exception("Setup could not start")
    if isinstance(e, SetupBusy):
        return HTTPException(status_code=409, detail=_BUSY)
    return HTTPException(status_code=500, detail=_START_FAILED)


# Starts a subprocess that can load models, so a cross-site GET must not reach it.
@router.get(
    "/api/setup/check",
    dependencies=[Depends(require_ui_header)],
    response_model=SetupCheckResponse,
    response_model_exclude_unset=True,
)
def setup_check(skip_chat_model: bool = False, load: bool = False) -> Dict[str, Any]:
    """``gaia init --check --json``: ready, or the stage and reasons it is not."""
    try:
        status = check(skip_chat_model=skip_chat_model, load=load)
        users_server = users_lemonade_url()
    except SetupError as e:
        logger.exception("Setup check failed")
        raise HTTPException(status_code=503, detail=_CHECK_FAILED) from e
    # cloud_steps lets the UI show a cloud choice's smaller download before it starts.
    return {
        **status,
        # Seen ready before: a server that isn't answering now is not a first run.
        "initialized": is_initialized(),
        "steps": step_labels(skip_chat_model, users_server),
        "cloud_steps": step_labels(True, users_server),
    }


# Probes the Lemonade GAIA uses, so a cross-site GET must not reach it.
@router.get(
    "/api/setup/plan",
    dependencies=[Depends(require_ui_header)],
    response_model=SetupPlanResponse,
    response_model_exclude_unset=True,
)
def setup_plan(skip_chat_model: Optional[bool] = None) -> Dict[str, Any]:
    """The server, chat model and downloads setup will use; loads nothing.

    *skip_chat_model* unset plans no local chat model while chat runs on a cloud
    model; the setup screen passes false to see what a local setup downloads.
    """
    try:
        return build_plan(skip_chat_model=skip_chat_model)
    except SetupPlanError as e:
        raise _plan_error(e, _PLAN_FAILED) from e


@router.post(
    "/api/setup/npu/recheck",
    dependencies=[Depends(require_ui_header)],
    response_model=SetupPlanResponse,
    response_model_exclude_unset=True,
)
def setup_npu_recheck() -> Dict[str, Any]:
    """Read the NPU's setup afresh, after the user fixed something; returns the plan."""
    from gaia.llm import npu_linux

    npu_linux.forget()
    return setup_plan()


# Starts a root step on this PC, so only someone at this PC may ask for it.
@router.post(
    "/api/setup/npu/memlock-fix",
    dependencies=[Depends(require_ui_header), Depends(require_local_pc)],
    response_model=SetupPlanResponse,
    response_model_exclude_unset=True,
)
def setup_npu_memlock_fix() -> Dict[str, Any]:
    """Raise the locked-memory limit FastFlowLM needs, behind the desktop's own
    administrator prompt; returns the plan. It applies after a restart."""
    from gaia.llm import npu_linux

    try:
        npu_linux.apply_memlock_fix()
    except npu_linux.MemlockFixError as e:
        logger.warning("NPU memlock fix not applied (%s): %s", e.code, e)
        status = {
            "cancelled": 409,
            "timeout": 409,
            "not_authorized": 409,
            "not_linux": 422,
            "no_pkexec": 422,
        }.get(e.code, 500)
        raise HTTPException(
            status_code=status, detail={"code": e.code, "message": str(e)}
        ) from e
    return setup_plan()


def _confirm(warning: Dict[str, Any]) -> HTTPException:
    """409 whose detail says what to confirm and which field confirms it."""
    return HTTPException(status_code=409, detail=warning)


class ChatModelRequest(BaseModel):
    """A chat model GAIA knows, or one already downloaded in Lemonade."""

    model_id: str
    acknowledge_no_fit: bool = False


@router.post(
    "/api/setup/chat-model",
    dependencies=[Depends(require_ui_header)],
    response_model=SetupPlanResponse,
    response_model_exclude_unset=True,
)
def setup_chat_model(
    body: ChatModelRequest, skip_chat_model: Optional[bool] = None
) -> Dict[str, Any]:
    """Pick the chat model, saved as ``default_model``; returns the new plan.

    One that may not fit this PC answers 409 ``no_fit`` until it is confirmed.
    """
    try:
        warning = plan_api.chat_model_warning(body.model_id)
        if warning and not body.acknowledge_no_fit:
            raise _confirm(warning)
        choose_chat_model(body.model_id, acknowledge_no_fit=True)
        return build_plan(skip_chat_model=skip_chat_model)
    except SetupPlanError as e:
        raise _plan_error(
            e,
            f"{body.model_id} isn't a chat model GAIA knows or one this Lemonade "
            "Server has downloaded. Download it first, then pick it.",
        ) from e


class VisionModelRequest(BaseModel):
    """A model that reads images, or null to go back to GAIA's choice."""

    model_id: Optional[str] = None


@router.post(
    "/api/setup/vision-model",
    dependencies=[Depends(require_ui_header)],
    response_model=SetupPlanResponse,
    response_model_exclude_unset=True,
)
def setup_vision_model(
    body: VisionModelRequest, skip_chat_model: Optional[bool] = None
) -> Dict[str, Any]:
    """Pick the model that reads images; returns the new plan."""
    try:
        plan_api.choose_vision_model(body.model_id)
        return build_plan(skip_chat_model=skip_chat_model)
    except SetupPlanError as e:
        raise _plan_error(e, f"{body.model_id} can't read images.") from e


@router.post(
    "/api/setup/report",
    dependencies=[Depends(require_ui_header), Depends(refuse_on_device_listener)],
    response_model=SetupReportResponse,
    response_model_exclude_unset=True,
)
def setup_report() -> Dict[str, Any]:
    """Bundle setup's record, every GAIA log and a system snapshot for a bug report."""
    record = runner.report()
    try:
        record["plan"] = build_plan(skip_chat_model=False)
    except SetupPlanError as e:
        # The report is for exactly this: say why the plan couldn't be read.
        record["plan_error"] = str(e)
    try:
        path = write_report(record)
    except SetupError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"name": path.name, "path": str(path), "size_bytes": path.stat().st_size}


@router.get(
    "/api/setup/report/{name}",
    dependencies=[Depends(require_ui_header), Depends(refuse_on_device_listener)],
)
def setup_report_file(name: str) -> FileResponse:
    """A troubleshooting report written by ``POST /api/setup/report``."""
    from gaia.home import gaia_home

    path = gaia_home() / name
    if not REPORT_NAME_RE.fullmatch(name) or not path.is_file():
        raise HTTPException(status_code=404, detail=f"No report named {name}.")
    return FileResponse(path, media_type="application/gzip", filename=name)


_MODEL_JOBS = ("chat", "vision", "embedding", "speech", "voice", "image_creation")


@router.get(
    "/api/setup/needs/{job}",
    dependencies=[Depends(require_ui_header)],
    response_model=SetupModelNeedResponse,
    response_model_exclude_unset=True,
)
def setup_model_need(job: str) -> Dict[str, Any]:
    """Whether a job needs a model that isn't on this PC, and which."""
    if job not in _MODEL_JOBS:
        raise HTTPException(
            status_code=422, detail=f"job must be one of {', '.join(_MODEL_JOBS)}."
        )
    try:
        return plan_api.model_need(job)
    except SetupPlanError as e:
        raise _plan_error(e, _PLAN_FAILED) from e


class SkipModelRequest(BaseModel):
    model_id: str
    skipped: bool = True


@router.post(
    "/api/setup/models/skip",
    dependencies=[Depends(require_ui_header)],
    response_model=SetupPlanResponse,
    response_model_exclude_unset=True,
)
def setup_skip_model(
    body: SkipModelRequest, skip_chat_model: Optional[bool] = None
) -> Dict[str, Any]:
    """Skip one of setup's models, or take it back; returns the new plan."""
    try:
        plan_api.set_model_skipped(
            body.model_id, body.skipped, skip_chat_model=skip_chat_model
        )
        return build_plan(skip_chat_model=skip_chat_model)
    except SetupPlanError as e:
        raise _plan_error(
            e,
            f"{body.model_id} isn't one of the models setup downloads, so there "
            "is nothing to skip.",
        ) from e


class DownloadModelRequest(BaseModel):
    model_id: str
    acknowledge_low_disk: bool = False
    keep_trying: bool = False


@router.post(
    "/api/setup/models/download",
    status_code=202,
    dependencies=[Depends(require_ui_header)],
    response_model=SetupRunStatus,
    response_model_exclude_unset=True,
)
def setup_download_model(body: DownloadModelRequest) -> Dict[str, Any]:
    """Download one model now: Retry, Resume, or one skipped at setup.

    Takes it off ``skipped_models``. Follow it with ``/api/setup/status``.
    """
    try:
        warning = plan_api.download_disk_warning(body.model_id)
        if warning and not body.acknowledge_low_disk:
            raise _confirm(warning)
        client, model_id = plan_api.prepare_download(
            body.model_id, acknowledge_low_disk=True
        )
        return runner.download_model(client, model_id, body.keep_trying)
    except SetupError as e:
        raise _run_error(e) from e
    except SetupPlanError as e:
        raise _plan_error(
            e,
            f"{body.model_id} isn't a model GAIA knows or one in this Lemonade "
            "Server. Add it in Settings, then try again.",
        ) from e


class RemoveModelRequest(BaseModel):
    model_id: str


@router.post(
    "/api/setup/models/remove",
    dependencies=[Depends(require_ui_header)],
    response_model=SetupRemoveModelResponse,
    response_model_exclude_unset=True,
)
def setup_remove_model(
    body: RemoveModelRequest, skip_chat_model: Optional[bool] = None
) -> Dict[str, Any]:
    """Delete one model from Lemonade at the user's request; it joins
    ``skipped_models``. Returns the new plan and ``freed_gb``."""
    if runner.busy_with(body.model_id):
        raise HTTPException(
            status_code=409,
            detail=(
                "Setup or that model's download is running, so the model can't be "
                "removed now. Wait for it or cancel it, then try again."
            ),
        )
    try:
        freed = plan_api.remove_model(body.model_id)
        return {**build_plan(skip_chat_model=skip_chat_model), "freed_gb": freed}
    except SetupPlanError as e:
        raise _plan_error(
            e, f"{body.model_id} isn't downloaded, so there is nothing to remove."
        ) from e


class SetupRunRequest(BaseModel):
    """``skip_chat_model`` when the chat runs on a cloud provider;
    ``gaia_lemonade`` to set up GAIA's own server instead of the user's;
    ``acknowledge_low_disk`` to download past a too-small model drive;
    ``keep_trying`` to wait up to two minutes out a dropped connection;
    ``extra_models`` to pull models beside the plan's (the user ticked them);
    ``server_only`` to start the model server and stop, so the models can be
    chosen against it before anything downloads;
    ``upgrade_embedded`` to replace an older embedded server artifact without
    downloading the existing model cache again."""

    skip_chat_model: bool = False
    gaia_lemonade: bool = False
    acknowledge_low_disk: bool = False
    keep_trying: bool = False
    extra_models: List[str] = []
    server_only: bool = False
    upgrade_embedded: bool = False


@router.post(
    "/api/setup/run",
    response_model=SetupRunStatus,
    response_model_exclude_unset=True,
)
def setup_run(
    body: SetupRunRequest, db: ChatDatabase = Depends(get_db)
) -> Dict[str, Any]:
    """Start ``gaia init --profile gaia --yes``; follow it with ``/api/setup/status``.

    When the selected models need more disk than is free, answers 409
    ``low_disk`` until ``acknowledge_low_disk`` confirms it.
    """
    return start_run(body, lambda run: agent_ui_chat_model(db, run))


def start_run(body: SetupRunRequest, chat_model_for) -> Dict[str, Any]:
    """Start setup for an app whose chat opens on ``chat_model_for()``."""
    if body.server_only and body.upgrade_embedded:
        raise HTTPException(
            status_code=422,
            detail="Choose either server_only or upgrade_embedded, not both.",
        )
    if body.upgrade_embedded and body.extra_models:
        raise HTTPException(
            status_code=422,
            detail="upgrade_embedded replaces the server without downloading, "
            "so it takes no extra_models.",
        )
    if body.server_only and body.extra_models:
        raise HTTPException(
            status_code=422,
            detail="server_only starts the model server without downloading, "
            "so it takes no extra_models.",
        )
    try:
        extras = plan_api.check_extra_models(body.extra_models)
    except UnknownModel as e:
        # Our own sentence naming the id the caller sent; nothing internal.
        raise HTTPException(status_code=422, detail=str(e)) from e
    except SetupPlanError as e:
        raise _plan_error(e, _PLAN_FAILED) from e
    try:
        # Switching to GAIA's own server: the user's server is moot.
        if (
            not body.gaia_lemonade
            and not body.server_only
            and not body.upgrade_embedded
        ):
            warning = plan_api.run_disk_warning(
                skip_chat_model=body.skip_chat_model,
                extra_models=extras,
                check_disk=not body.acknowledge_low_disk,
                check_chat_model=True,
            )
            if warning:
                raise _confirm(warning)
        return runner.start(
            body.skip_chat_model,
            body.gaia_lemonade,
            allow_low_disk=body.acknowledge_low_disk,
            keep_trying=body.keep_trying,
            extra_models=extras,
            server_only=body.server_only,
            upgrade_embedded=body.upgrade_embedded,
            chat_model_for=chat_model_for,
        )
    except SetupPlanError as e:
        raise _plan_error(e, _PLAN_FAILED) from e
    except SetupError as e:
        raise _run_error(e) from e


@router.get(
    "/api/setup/status",
    response_model=SetupRunStatus,
    response_model_exclude_unset=True,
)
def setup_status() -> Dict[str, Any]:
    """The running (or last) setup's steps and progress; ``state: idle`` if none."""
    return runner.status() or {"state": "idle"}


class CancelRequest(BaseModel):
    model_id: Optional[str] = None


@router.post(
    "/api/setup/cancel",
    response_model=SetupCancelResponse,
    response_model_exclude_unset=True,
)
def setup_cancel(body: Optional[CancelRequest] = None) -> Dict[str, Any]:
    """Stop the running setup, or one model's download. Finished models and
    partial files are kept, so a later run or download resumes."""
    return {"cancelled": runner.cancel(body.model_id if body else None)}
