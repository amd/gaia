# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Ask the user a question *during* a run and get the answer back (#2469).

Before this, an agent that needed a decision mid-run had exactly two moves:
guess, or stop and print a shell command for the user to go run somewhere else.
Both are bad; the second is the worst moment in the email agent's experience
("run ``gaia connectors connect google --scopes <scopes>``" to someone sitting
in a terminal chat).

This module is the agent-side half of the fix. It wraps the ONE blocking
primitive that already exists — ``SSEOutputHandler.request_user_input_blocking``
— so the semantics (emit a request, block the agent thread, resolve
out-of-band, bounded by a timeout) are inherited rather than forked. What it
adds on top is shape: a question can carry 2-4 mutually-exclusive **options**,
each with a short label AND a description of what choosing it means, plus an
always-available free-text escape. A bare yes/no cannot express "Gmail or
Outlook?" or "use the default scopes, or pick them yourself?".

The wire side is ``sse_translation`` mapping the emitted ``user_input_request``
onto the canonical ``needs_input`` event, and ``query_routes`` exposing
``POST /v1/email/query/{run_id}/respond`` to resolve it.

Fail-loudly, never silently: a surface that cannot ask raises
:class:`InputUnsupportedError`; an unanswered question raises
:class:`InputUnansweredError` rather than the agent inventing an answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence

from gaia.logger import get_logger

log = get_logger(__name__)

#: Sentinel ``request_user_input_blocking`` returns when nothing answered it.
NO_RESPONSE = "__NO_RESPONSE__"

#: Default wait for an answer. Comfortably longer than a human takes to read
#: four options, and shorter than the TUI's 300s read-idle watchdog — which the
#: ``/query`` stream keeps alive with heartbeats while a question is pending.
DEFAULT_TIMEOUT_SECONDS = 240


class InputUnsupportedError(RuntimeError):
    """The current surface cannot ask the user anything mid-run."""


class InputUnansweredError(RuntimeError):
    """The question was asked but nothing answered it (timeout or cancel)."""


@dataclass(frozen=True)
class Option:
    """One mutually-exclusive answer.

    ``value`` is the machine token the agent branches on; ``label`` is the short
    thing the user picks; ``description`` says what choosing it actually does,
    so the choice is informed rather than a guess at the label's meaning.
    """

    value: str
    label: str
    description: str = ""

    def as_dict(self) -> Dict[str, str]:
        return {
            "value": self.value,
            "label": self.label,
            "description": self.description,
        }


def _console_of(agent: Any) -> Any:
    console = getattr(agent, "console", None)
    if console is None:
        raise InputUnsupportedError(
            "This agent has no output console, so it cannot ask you anything "
            "while it runs. Run it through the GAIA TUI or the Agent UI."
        )
    return console


def ask(
    agent: Any,
    question: str,
    options: Sequence[Option] = (),
    *,
    allow_free_text: bool = True,
    sensitive: bool = False,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Ask *question* and block until the user answers.

    Returns the chosen option's ``value``, or — when ``allow_free_text`` — the
    text the user typed. Matching is forgiving in one direction only: an answer
    is accepted as an option if it equals that option's ``value`` or ``label``
    case-insensitively, so a client that echoes the label instead of the value
    still resolves to the same branch.

    Raises:
        InputUnsupportedError: the surface has no interactive input channel.
        InputUnansweredError: nothing answered within ``timeout_seconds``, or
            the run was cancelled while waiting.
        ValueError: the answer matched no option and free text is not allowed.
    """
    console = _console_of(agent)
    asker = getattr(console, "request_user_input_blocking", None)
    if not callable(asker):
        raise InputUnsupportedError(
            "This surface cannot ask questions during a run "
            f"({type(console).__name__} has no request_user_input_blocking). "
            "Run the agent through the GAIA TUI or the Agent UI, which do."
        )
    # The handler exists on every /query run, so its presence says nothing about
    # whether anyone is watching. The CALLER declares that (can_answer_questions
    # on the request), and a caller that cannot answer is refused here — parking
    # the run until the question times out is indistinguishable from a hang.
    if not getattr(agent, "can_answer_questions", False):
        raise InputUnsupportedError(
            "I'd need to ask you a question to do that, and this way of running "
            "me can't take an answer mid-task. Use the interactive GAIA TUI "
            "(`gaia` with no --query) or the Agent UI, which can."
        )

    opts = list(options)
    raw = asker(
        message=question,
        choices=[o.value for o in opts],
        options=[o.as_dict() for o in opts],
        allow_free_text=bool(allow_free_text),
        sensitive=bool(sensitive),
        timeout_seconds=int(timeout_seconds),
        # An unanswered question must fail the step loudly, not quietly turn
        # into "the agent assumed you meant the first option".
        default_if_no_response=None,
        continue_if_no_response=True,
    )

    answer = (raw or "").strip()
    if not answer or answer == NO_RESPONSE:
        raise InputUnansweredError(
            f"No answer to: {question!r} (waited {timeout_seconds}s). "
            "Nothing was changed. Ask me again when you're ready to answer."
        )

    resolved = match_option(answer, opts)
    if resolved is not None:
        return resolved
    if allow_free_text:
        return answer
    labels = ", ".join(f"{o.label} ({o.value})" for o in opts) or "<none>"
    # A sensitive answer is never echoed back — not even into an error the model
    # will paraphrase into the transcript.
    shown = "That answer" if sensitive else repr(answer)
    raise ValueError(f"{shown} is not one of the offered answers. Choose: {labels}.")


def match_option(answer: str, options: Sequence[Option]) -> Optional[str]:
    """Return the ``value`` *answer* selects, or ``None`` if it matches none."""
    needle = answer.strip().casefold()
    for opt in options:
        if needle in (opt.value.strip().casefold(), opt.label.strip().casefold()):
            return opt.value
    return None


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "InputUnansweredError",
    "InputUnsupportedError",
    "NO_RESPONSE",
    "Option",
    "ask",
    "match_option",
]
