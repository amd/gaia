# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Why a turn ended: the values of ``result["stop_reason"]``.

``status`` says whether a turn succeeded. This says why it stopped, so a caller
can tell a model outage from a step limit from one of GAIA's own checks without
reading the answer text.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

#: The agent acts on files and commands; its answer is checked against them.
HOST_WORKSPACE = "workspace"
#: The agent's reply goes to an end user and is returned as the model wrote it.
HOST_CONVERSATION = "conversation"
HOSTS = (HOST_WORKSPACE, HOST_CONVERSATION)

#: The model gave its answer.
STOP_ANSWERED = "answered"
#: The turn ended with no answer text and nothing else below applies.
STOP_NO_ANSWER = "no_answer"
#: The step limit (``max_steps``) ran out.
STOP_MAX_STEPS = "max_steps"
#: The agent's ``time_limit_s`` or ``TurnBudget.deadline_s`` passed.
STOP_DEADLINE = "deadline"
#: ``TurnBudget.max_model_calls`` was reached.
STOP_MODEL_CALLS = "model_calls"
#: ``TurnBudget.max_output_tokens`` was reached.
STOP_OUTPUT_TOKENS = "output_tokens"
#: ``TurnBudget.max_cost_usd`` was reached.
STOP_COST = "cost"
#: The host ended the turn: ``before_model_call`` or its client raised ``StopTurn``.
STOP_HOST = "host_stop"
#: A model call failed for good.
STOP_MODEL_CALL = "model_call"
#: Replies kept being cut off at the output-token cap.
STOP_OUTPUT_CAP = "output_cap"
#: The model's tool calls could not be parsed, after the retries.
STOP_TOOL_CALL_FORMAT = "tool_call_format"
#: The model kept announcing a step instead of answering, after the re-prompts.
STOP_REPROMPT_BUDGET = "reprompt_budget"
#: The same failing tool call was repeated until the loop guard stopped it.
STOP_REPEATED_CALLS = "repeated_calls"
#: A tool call failed and the turn did not recover from it.
STOP_TOOL_ERROR = "tool_error"
#: GAIA's own checks found the work unfinished; see ``completion_gaps``.
STOP_COMPLETION_GAPS = "completion_gaps"
#: The turn was cancelled, or the person at the prompt declined more steps.
STOP_CANCELLED = "cancelled"


def stop_reason(
    *,
    status: str,
    hint: Optional[str],
    cancelled: bool,
    time_limit_reached: bool,
    max_steps_reached: bool,
    model_failed: bool,
    failing_repeats: bool,
    blocking_gaps: List[Any],
    has_answer: bool,
) -> str:
    """The one reason a finished turn stopped, most specific cause first.

    *hint* is a reason the loop recorded at the exit itself, for the stops the
    flags cannot tell apart (a cut-off reply also counts as a failed call).
    """
    if cancelled:
        return STOP_CANCELLED
    if time_limit_reached:
        return STOP_DEADLINE
    if hint:
        return hint
    if model_failed:
        return STOP_MODEL_CALL
    if max_steps_reached:
        return STOP_MAX_STEPS
    if failing_repeats:
        return STOP_REPEATED_CALLS
    if status == "failed":
        return STOP_TOOL_ERROR
    if blocking_gaps:
        return STOP_COMPLETION_GAPS
    return STOP_ANSWERED if has_answer else STOP_NO_ANSWER


def tool_call_record(
    name: str, args: Any, result: Any, *, ran: bool, failed: bool
) -> Dict[str, Any]:
    """One entry of ``result["tool_calls"]``."""
    return {
        "name": name,
        "args": args if isinstance(args, dict) else {},
        "result": result,
        "ran": ran,
        "failed": failed,
    }
