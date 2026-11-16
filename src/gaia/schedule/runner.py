# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Execute a single scheduled job and deliver its output.

Each fire runs in a fresh agent session (clean state, not a continuation) and
routes the result through the schedule's configured sink.
"""

from __future__ import annotations

from gaia.logger import get_logger
from gaia.schedule import sinks
from gaia.schedule.store import Schedule

log = get_logger(__name__)


def resolve_input(schedule: Schedule) -> str:
    """Return the prompt text to send to the agent for this schedule.

    The skills runtime (``gaia.skills``) ships, but the scheduler is not wired
    to it — a scheduled skill has no agent to load into. Fail loudly rather
    than guess at a prompt.
    """
    if schedule.prompt:
        return schedule.prompt
    raise NotImplementedError(
        f"schedule {schedule.name!r} uses --skill {schedule.skill!r}, but the "
        "scheduler cannot run skills yet. Re-create it with --prompt: "
        f"gaia schedule add --name {schedule.name} --cron '<expr>' --prompt '<text>'"
    )


class ScheduleModelError(RuntimeError):
    """The run's model can't answer without a download; worded for the user."""


def _chosen_model(prompt: str) -> tuple[str, str | None]:
    """The model the GAIA apps reopen on; a ``hybrid`` pick is routed for *prompt*.

    Also returns the ``hybrid_*`` setting that chose the model, or None.
    """
    from gaia.config import GaiaConfig
    from gaia.llm.hybrid import HybridRouter, HybridSettings, is_hybrid_model
    from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME, resolve_chat_model

    config = GaiaConfig.load()
    picked = config.last_model or config.resolve_model(None, DEFAULT_MODEL_NAME)
    if not is_hybrid_model(picked):
        return resolve_chat_model(), None
    # A fresh router per run: each run is its own session, so nothing is sticky.
    settings = HybridSettings.load(config)
    route = HybridRouter(settings).decide(prompt)
    if route.notice:
        log.warning("%s", route.notice)
    log.info("schedule: hybrid runs this prompt on %s (%s)", route.model, route.reason)
    key = "hybrid_strong_model"
    if route.model == settings.cheap_model:
        key = "hybrid_cheap_model"
    return route.model, key


def resolve_model(prompt: str) -> str:
    """The model a scheduled run of *prompt* answers with.

    The one the GAIA apps reopen on; when that is ``hybrid``, the model its
    router picks for *prompt*, as for a chat turn. A scheduled run never
    downloads a model: that is gigabytes nobody agreed to.
    """
    from gaia.config import GaiaConfig
    from gaia.llm.lemonade_client import LemonadeClient, cloud_model_provider

    model, hybrid_key = _chosen_model(prompt)
    if cloud_model_provider(model):
        return model
    client = LemonadeClient(verbose=False)
    # A gateway model the catalog leaves out (no or expired token) has nothing to download.
    if client._is_cloud_model(model):  # pylint: disable=protected-access
        return model
    info = client.get_model_info(model)
    if info["downloaded"]:
        return model
    if hybrid_key:
        remedy = (
            f"Hybrid picked it as its {hybrid_key}; run `gaia config set "
            f"{hybrid_key} <id>` with a model you have"
        )
    elif GaiaConfig.load().last_model == model:
        remedy = "Pick a model you have in the GAIA app"
    else:
        remedy = "Run `gaia config set default_model <id>` with a model you have"
    raise ScheduleModelError(
        f"{model} is not downloaded ({info['size_gb']:.1f} GB), and scheduled "
        f"runs do not download models. {remedy}, or download it from the GAIA "
        "app's Settings > Model."
    )


def fire(schedule: Schedule) -> str:
    """Run one scheduled job: fresh agent session -> sink. Returns the output."""
    # Imported lazily so the scheduler/store can be used without spinning up the
    # full agent stack (and so tests can register schedules without an LLM).
    from gaia.chat.sdk import AgentConfig, AgentSDK

    prompt = resolve_input(schedule)
    sinks.validate(schedule.sink, schedule.sink_args)
    log.info("schedule %r firing (sink=%s)", schedule.name, schedule.sink)

    sdk = AgentSDK(AgentConfig(model=resolve_model(prompt)))
    response = sdk.send(prompt, no_history=True)
    output = response.text

    sinks.dispatch(schedule.sink, schedule.sink_args, output)
    log.info("schedule %r delivered to sink %s", schedule.name, schedule.sink)
    return output
