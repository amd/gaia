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

    ``--skill`` resolution depends on the agentskills.io skill format (#888),
    which has not landed yet; fail loudly rather than guess.
    """
    if schedule.prompt:
        return schedule.prompt
    raise NotImplementedError(
        f"schedule {schedule.name!r} uses --skill {schedule.skill!r}, but skill-format "
        f"resolution is not available yet (blocked on #888). Use --prompt for now."
    )


def fire(schedule: Schedule) -> str:
    """Run one scheduled job: fresh agent session -> sink. Returns the output."""
    # Imported lazily so the scheduler/store can be used without spinning up the
    # full agent stack (and so tests can register schedules without an LLM).
    from gaia.chat.sdk import AgentConfig, AgentSDK

    prompt = resolve_input(schedule)
    log.info("schedule %r firing (sink=%s)", schedule.name, schedule.sink)

    sdk = AgentSDK(AgentConfig())
    response = sdk.send(prompt, no_history=True)
    output = response.text

    sinks.dispatch(schedule.sink, schedule.sink_args, output)
    log.info("schedule %r delivered to sink %s", schedule.name, schedule.sink)
    return output
