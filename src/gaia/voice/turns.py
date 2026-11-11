# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Turn-taking: when the user has finished a thought, and when they mean to interrupt.

People pause mid-sentence, so a fixed silence window either cuts them off or
makes every answer feel slow. :class:`TurnTaker` ends a turn at once when what
was said sounds finished, and waits a little longer when it trails off ("and",
"so", "um"), joining what comes next into the same turn. While GAIA is
speaking it tells an interruption from a listener's "mm-hm" or a cough: only
real words stop her.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

#: Extra wait after speech that sounds unfinished, before giving up on more.
UNFINISHED_WAIT_MS = 1400
#: Extra wait after one or two words, which are often the start of a sentence.
SHORT_WAIT_MS = 600
#: How long a sound may duck GAIA's voice before words must follow.
DUCK_MS = 900

_WORD = re.compile(r"[A-Za-z0-9']+")

#: Last words that promise more to come.
_TRAILING = frozenset(
    """a an the and or but so because if when while that which who whose to of in
    on at for with from by about into than then also plus like is are was were
    be been my your our their his her its this these those can could would
    should will do does did not um uh er hmm well""".split()
)

#: What a listener says to show they're following; it never takes the floor.
_BACKCHANNELS = frozenset(
    """yeah yep yes ok okay right sure mhm mm hmm uh-huh uh huh mm-hmm i see got it
    cool nice great wow oh ah""".split()
)

#: Short words that do take the floor.
_STOP_WORDS = frozenset("stop wait no hold cancel pause quiet enough".split())


def words(text: str) -> List[str]:
    return [w.lower() for w in _WORD.findall(text or "")]


def sounds_unfinished(text: str) -> bool:
    """Whether *text* reads like the speaker stopped mid-thought."""
    stripped = (text or "").strip()
    if not stripped:
        return False
    if stripped.endswith(("...", "…", ",", "-", "—")):
        return True
    tokens = words(stripped)
    if not tokens:
        return False
    return tokens[-1] in _TRAILING and not stripped.endswith("?")


def is_backchannel(text: str) -> bool:
    """Whether *text* is only a listener's acknowledgment."""
    tokens = words(text)
    if not tokens or len(tokens) > 3:
        return False
    if any(t in _STOP_WORDS for t in tokens):
        return False
    return all(t in _BACKCHANNELS for t in tokens)


def wait_after(text: str) -> int:
    """Milliseconds to keep listening after a final transcript of *text*."""
    if sounds_unfinished(text):
        return UNFINISHED_WAIT_MS
    stripped = (text or "").strip()
    if len(words(stripped)) <= 2 and not stripped.endswith(("?", "!", ".")):
        return SHORT_WAIT_MS
    return 0


@dataclass
class Decision:
    """What the session should do next. Any field may be set."""

    turn: Optional[str] = None  # the user's turn is complete: this text
    hold_ms: int = 0  # keep listening this long, then call timeout()
    duck_ms: int = 0  # if no words follow in this long, call duck_timeout()
    barge_in: bool = False  # stop GAIA's speech: the user took the floor
    duck: Optional[bool] = None  # True lowers GAIA's voice, False restores it
    holding: Optional[str] = None  # text so far, while waiting for more


@dataclass
class TurnTaker:
    """Feed listener events in; act on the :class:`Decision` each returns.

    The caller owns the clock: when a decision has ``hold_ms``, call
    :meth:`timeout` that long after, unless :meth:`speech_started` came first.
    ``duck_ms`` works the same way through :meth:`duck_timeout`.
    """

    _segments: List[str] = field(default_factory=list)
    _ducked: bool = False
    _barged: bool = False

    # ---- while the user has the floor ------------------------------------

    def speech_started(self, speaking: bool) -> Decision:
        """The user made a sound. *speaking* is whether GAIA is talking."""
        if speaking and not self._barged:
            self._ducked = True
            return Decision(duck=True, duck_ms=DUCK_MS)
        return Decision()

    def partial(self, text: str, speaking: bool) -> Decision:
        """Words so far. Real words while GAIA talks are an interruption."""
        if speaking and not self._barged and self._takes_floor(text):
            return self._barge()
        return Decision()

    def final(self, text: str, speaking: bool) -> Decision:
        """One finished stretch of speech."""
        text = (text or "").strip()
        if not text:
            return Decision()
        if speaking and not self._barged:
            if not self._takes_floor(text):
                # "mm-hm" over GAIA's voice: she keeps talking, nothing is sent.
                return self._unduck()
            decision = self._barge()
        else:
            decision = Decision()
        self._segments.append(text)
        joined = " ".join(self._segments)
        wait = wait_after(joined)
        if wait:
            decision.hold_ms = wait
            decision.holding = joined
            return decision
        decision.turn = self._take()
        return decision

    def timeout(self) -> Decision:
        """The hold ran out with nothing more said."""
        if not self._segments:
            return Decision()
        return Decision(turn=self._take())

    def duck_timeout(self) -> Decision:
        """A sound ducked GAIA's voice but no words followed."""
        return self._unduck()

    def commit(self) -> Decision:
        """Push-to-talk released: whatever was said is the turn."""
        return self.timeout()

    def speaking_done(self) -> None:
        """GAIA finished or was stopped; the next sound is the user's turn."""
        self._barged = False
        self._ducked = False

    # ---- internals -------------------------------------------------------

    @staticmethod
    def _takes_floor(text: str) -> bool:
        return bool(words(text)) and not is_backchannel(text)

    def _barge(self) -> Decision:
        self._barged = True
        self._ducked = False
        return Decision(barge_in=True, duck=False)

    def _unduck(self) -> Decision:
        if not self._ducked:
            return Decision()
        self._ducked = False
        return Decision(duck=False)

    def _take(self) -> str:
        text = " ".join(self._segments)
        self._segments = []
        return text
