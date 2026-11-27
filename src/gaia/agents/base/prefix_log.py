# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Say in the log whether a new chat reuses the prompt the model already read.

A local model reads the system prompt and tool list once and then reuses that
read for every request that starts the same way. When a chat's first request
starts differently from what the warm-up (or the chat before it) sent, the
model reads all of it again: minutes on a slow GPU, with nothing on screen to
explain it. Each first request is compared here with the last one sent to the
same model, and a difference is logged with where it is.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: A heading GAIA writes into its own prompt: ``**NAME:**`` or ``==== NAME ====``.
_HEADING = re.compile(r"^(?:\*\*[^*\n]{1,60}\*\*|==== [^=\n]{1,60} ====)", re.MULTILINE)


def _section_at(prompt: str, at: int) -> str:
    """The last GAIA heading at or before character *at*, or "its opening"."""
    line_end = prompt.find("\n", at)
    headings = _HEADING.findall(prompt[: len(prompt) if line_end < 0 else line_end])
    return headings[-1] if headings else "its opening"


@dataclass(frozen=True)
class Prefix:
    """The part of a request every chat on one model is meant to share."""

    system_prompt: str
    tool_names: Tuple[str, ...]
    tools_digest: str

    @property
    def fingerprint(self) -> str:
        digest = hashlib.sha256(self.system_prompt.encode("utf-8"))
        digest.update(self.tools_digest.encode("ascii"))
        return digest.hexdigest()[:12]


def prefix_of(system_prompt: Optional[str], tools: Optional[List[dict]]) -> Prefix:
    """The :class:`Prefix` of a request with *system_prompt* and *tools*."""
    tools = tools or []
    names = tuple(
        str((tool.get("function") or {}).get("name") or tool.get("name") or "?")
        for tool in tools
    )
    rendered = json.dumps(tools, sort_keys=True, default=str)
    return Prefix(
        system_prompt=system_prompt or "",
        tool_names=names,
        tools_digest=hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
    )


def describe_difference(before: Prefix, now: Prefix) -> str:
    """Where *now* stops matching *before*, in words for a log line."""
    parts = []
    if before.system_prompt != now.system_prompt:
        old, new = before.system_prompt, now.system_prompt
        at = next(
            (i for i, (a, b) in enumerate(zip(old, new)) if a != b),
            min(len(old), len(new)),
        )
        # Headings only: the text itself can name the user's files and folders.
        parts.append(
            f"the system prompt differs from character {at} of {len(old)}, "
            f"under {_section_at(new, at)} ({len(new) - len(old):+d} characters)"
        )
    if before.tools_digest != now.tools_digest:
        added = [n for n in now.tool_names if n not in before.tool_names]
        removed = [n for n in before.tool_names if n not in now.tool_names]
        if added or removed:
            parts.append(f"the tool list changed (added {added}, removed {removed})")
        elif before.tool_names != now.tool_names:
            parts.append("the tool list is in a different order")
        else:
            parts.append("a tool's description or parameters changed")
    return "; ".join(parts)


_lock = threading.Lock()
#: (model id, server slot) -> (what sent it, its prefix): the last one sent there.
_last_sent: Dict[Tuple[str, int], Tuple[str, Prefix]] = {}


def note_sent(model: Optional[str], sender: str, prefix: Prefix, slot: int = 0) -> bool:
    """Record that *sender* sends *prefix* to *model*; log whether it is reused.

    The server keeps one read per *slot*, so a prefix is compared with the last
    one sent to the same slot. Returns whether it matches, or True when nothing
    was sent there before.
    """
    key = (model or "", slot)
    with _lock:
        earlier = _last_sent.get(key)
        _last_sent[key] = (sender, prefix)
    if earlier is None:
        logger.info(
            "PROMPT_PREFIX %s sends prefix %s to %s (%d characters, %d tools); "
            "nothing was sent to it before",
            sender,
            prefix.fingerprint,
            model,
            len(prefix.system_prompt),
            len(prefix.tool_names),
        )
        return True
    earlier_sender, earlier_prefix = earlier
    if earlier_prefix == prefix:
        logger.info(
            "PROMPT_PREFIX %s sends prefix %s to %s, the same as %s sent: the "
            "model can reuse what it read",
            sender,
            prefix.fingerprint,
            model,
            earlier_sender,
        )
        return True
    logger.warning(
        "PROMPT_PREFIX %s sends prefix %s to %s, but %s sent %s: the model "
        "cannot reuse what it read and reads the whole prompt again. Cause: %s.",
        sender,
        prefix.fingerprint,
        model,
        earlier_sender,
        earlier_prefix.fingerprint,
        describe_difference(earlier_prefix, prefix),
    )
    return False


def note_read(model: Optional[str], sender: str, usage: Any) -> None:
    """Log how much of *sender*'s first request the model read and reused."""
    if not isinstance(usage, dict) or usage.get("prompt_tokens") is None:
        logger.info(
            "PROMPT_PREFIX %s: %s reported no token counts for its first request",
            sender,
            model,
        )
        return
    logger.info(
        "PROMPT_PREFIX %s: first request to %s had %s prompt tokens, %s of them "
        "reused from the model's cache",
        sender,
        model,
        usage.get("prompt_tokens"),
        usage.get("cached_tokens", "an unreported number"),
    )


def forget() -> None:
    """Drop what was recorded."""
    with _lock:
        _last_sent.clear()
