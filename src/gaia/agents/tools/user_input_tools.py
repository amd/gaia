# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""UserInputToolsMixin — lets an agent ask the user a question mid-turn.

The answer comes through the console: one with ``request_user_input_blocking``
(the Agent UI's and TUI's) shows the question and waits; a plain terminal
console reads a line from stdin; a console that says it cannot answer
(``answers_questions = False``) sends the question into the reply instead.
"""

from typing import Any, Dict, List, Optional

from gaia.agents.base.call_risk import NEEDS_PERSON_ARG, READ, call_risk

#: Longest request_user_input waits for an answer, in seconds.
USER_INPUT_MAX_WAIT_S = 600

#: request_user_input's answer where nobody can reply mid-run (a Discord DM, a
#: one-shot run): no choice was made, so the question goes in the reply.
NO_QUESTION_CHANNEL = (
    "__NO_RESPONSE__ The user cannot answer a question mid-run here, so nothing "
    "was chosen; do not act on a default. End your turn by asking the question "
    "in your reply, with what they need to decide (such as the list); their "
    "next message is the answer."
)


def _no_answer_result(timeout_seconds: int, default: Optional[str]) -> str:
    """What request_user_input returns when nobody answered: never an answer."""
    text = (
        f"__NO_RESPONSE__ The user did not answer within {timeout_seconds} "
        "seconds, so you have no answer from them. "
    )
    if default:
        text += (
            f'Your default was "{default}". You may go on with it only for steps '
            "that change nothing, and your reply must say you assumed it because "
            "they did not answer. "
        )
    return text + (
        "Do not create, change, move or delete anything on that assumption: any "
        "such step now needs the user's approval, even with Full access on. If "
        "the work needs their choice, stop and end your reply with the question."
    )


class UserInputToolsMixin:
    """
    Mixin providing a tool that asks the user and waits for the answer.

    Tools provided:
    - request_user_input: Ask a question, optionally with choices

    Requires an ``Agent`` host. While a question from this turn is unanswered,
    a confirmation-gated call that is not a read is marked as needing a
    person, so pre-approval never turns the model's guess into a change.
    """

    def register_user_input_tools(self) -> None:
        """Register request_user_input into _TOOL_REGISTRY."""
        from gaia.agents.base.tools import tool

        _agent = self

        # The tool watchdog must outlast the wait itself, or a slow answer is
        # dropped and the call abandoned.
        @tool(timeout=USER_INPUT_MAX_WAIT_S + 30)
        def request_user_input(
            message: str,
            choices: List[str] = None,
            default_if_no_response: str = None,
            timeout_seconds: int = 300,
            continue_if_no_response: bool = True,
        ) -> str:
            """Ask the user a question and wait for their response.

            Args:
                message: The question to present to the user.
                choices: Optional list of choices (renders as buttons in the UI).
                default_if_no_response: The choice you would pick if nobody
                    answers. It is never returned as the user's answer: on a
                    timeout you get "__NO_RESPONSE__" with this default named.
                timeout_seconds: How long to wait (10 to 600, default 300).
                continue_if_no_response: If True, continue after timeout.
                    If False, the loop pauses until user re-engages.

            Returns:
                The user's response or chosen option, or text starting with
                "__NO_RESPONSE__" when they did not answer. Silence is not an
                answer: then change nothing on an assumption, and say in your
                reply that you are waiting on them or what you assumed.
            """
            timeout_seconds = min(int(timeout_seconds), USER_INPUT_MAX_WAIT_S)
            console = _agent.console
            if not getattr(console, "answers_questions", True):
                # The default would read as the user's choice ("cancel").
                _agent._unanswered_question = message
                return NO_QUESTION_CHANNEL
            if hasattr(console, "request_user_input_blocking"):
                reply = console.request_user_input_blocking(
                    message=message,
                    choices=choices,
                    default_if_no_response=None,
                    timeout_seconds=timeout_seconds,
                    continue_if_no_response=continue_if_no_response,
                )
            else:
                # Non-SSE consoles (interactive sessions)
                try:
                    reply = input(f"\n[Agent asking]: {message}\n> ").strip()
                except (EOFError, OSError):
                    reply = ""
            if reply and reply != "__NO_RESPONSE__":
                _agent._unanswered_question = None
                return reply
            _agent._unanswered_question = message
            return _no_answer_result(timeout_seconds, default_if_no_response)

    def confirmation_args_for_call(
        self, tool_name: str, tool_args: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Also mark a gated call after an unanswered question for a person.

        Pre-approval (Full access) must not turn the model's guess at an
        answer into a change nobody agreed to. Only a call known to be a read
        is exempt; the marker matters only on a call that already asks.
        """
        parent = getattr(super(), "confirmation_args_for_call", None)
        shown = parent(tool_name, tool_args) if callable(parent) else tool_args
        if getattr(self, "_unanswered_question", None) is None or not isinstance(
            shown, dict
        ):
            return shown
        if call_risk(tool_name, tool_args) == READ:
            return shown
        return {**shown, NEEDS_PERSON_ARG: True}
