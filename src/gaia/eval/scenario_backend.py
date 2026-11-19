# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Agent UI backend's side of one eval scenario.

While the driver (``claude -p``) runs a scenario, nobody sits in front of the
Agent UI to answer its permission prompts, and each one would wait out the
product's 10-minute bound. :class:`ScenarioBackend` plays that user: it
watches the scenario's turns through the same turn control the Agent UI MCP
server exposes (``answer_prompt``) and answers every prompt at once under the
scenario's ``setup.approve_prompts`` (deny unless the scenario says the user
would allow the tool).

When the driver stops, for any reason, it also stops whatever the scenario
left running on the backend and waits for the chat slot to free up, so the
next scenario is not scored for this one's leftovers, and it recovers the
turns that finished when the driver produced none.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Set

import requests

logger = logging.getLogger(__name__)

#: Sessions the eval driver creates are titled ``Eval: <scenario_id>``.
EVAL_SESSION_PREFIX = "Eval: "

#: A prompt still unanswered after this long means the harness, not the agent,
#: decided the scenario.
PROMPT_STUCK_S = 30.0

#: How long the backend gets to finish a stopped turn before the run stops.
SETTLE_TIMEOUT_S = 180.0

POLL_INTERVAL_S = 0.5
REQUEST_TIMEOUT_S = 10.0
_HEADERS = {"X-Gaia-UI": "1"}

ALLOW = "allow_once"
DENY = "deny"


def approved_tools(scenario_data: dict) -> List[str]:
    """Tools whose prompts the scenario's user approves (``setup.approve_prompts``)."""
    return list((scenario_data.get("setup") or {}).get("approve_prompts") or [])


def validate_approve_prompts(value: Any) -> Optional[str]:
    """Why ``setup.approve_prompts`` is malformed, or None when it is fine."""
    if value is None:
        return None
    if not isinstance(value, list) or not all(
        isinstance(t, str) and t.strip() for t in value
    ):
        return (
            "setup.approve_prompts must be a list of tool names whose permission "
            "prompts the simulated user approves, e.g. ['write_file']; every "
            "other prompt is denied"
        )
    return None


def decide(tool: str, approve: Iterable[str]) -> tuple:
    """The simulated user's answer to a prompt for *tool*, and why."""
    if tool in set(approve):
        return ALLOW, f"the scenario's user approves {tool}"
    return DENY, "the scenario does not say the user approves this tool"


class ScenarioBackend:
    """Answers one scenario's prompts and cleans its backend state up after it."""

    def __init__(self, backend_url: str, scenario_data: dict) -> None:
        self.backend_url = backend_url.rstrip("/")
        self.scenario_id = scenario_data["id"]
        self.approve = approved_tools(scenario_data)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._prompts: List[Dict[str, Any]] = []
        self._by_confirm_id: Dict[str, Dict[str, Any]] = {}
        self._titles: Dict[str, Optional[str]] = {}
        self._seen_sessions: Set[str] = set()
        self._sessions_before: Set[str] = set()
        self._registry = None

    # ── HTTP ──

    def _get(self, path: str, **params) -> Any:
        resp = requests.get(
            f"{self.backend_url}/api{path}",
            params=params or None,
            headers=_HEADERS,
            timeout=REQUEST_TIMEOUT_S,
        )
        resp.raise_for_status()
        return resp.json()

    def _active(self) -> List[str]:
        return list(self._get("/chat/active").get("session_ids") or [])

    def _title(self, session_id: str) -> Optional[str]:
        if session_id not in self._titles:
            try:
                self._titles[session_id] = self._get(f"/sessions/{session_id}").get(
                    "title"
                )
            except requests.HTTPError:
                self._titles[session_id] = None
        return self._titles[session_id]

    def _is_eval_session(self, session_id: str) -> bool:
        title = self._title(session_id) or ""
        return title.startswith(EVAL_SESSION_PREFIX)

    def _scenario_sessions(self) -> List[Dict[str, Any]]:
        title = f"{EVAL_SESSION_PREFIX}{self.scenario_id}"
        sessions = self._get("/sessions", limit=200).get("sessions") or []
        return [s for s in sessions if s.get("title") == title]

    # ── lifecycle ──

    def start(self) -> None:
        """Note the sessions that already exist, then start answering prompts."""
        from gaia.mcp.servers.agent_ui_control import TurnRegistry

        self._sessions_before = {s["id"] for s in self._scenario_sessions()}
        self._registry = TurnRegistry(self.backend_url, _HEADERS)
        self._thread = threading.Thread(
            target=self._watch, name=f"eval-prompts-{self.scenario_id}", daemon=True
        )
        self._thread.start()

    def _stop_watching(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(REQUEST_TIMEOUT_S * 2)

    def _watch(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except requests.RequestException as e:
                logger.warning("Eval prompt watcher could not reach the backend: %s", e)
            self._stop.wait(POLL_INTERVAL_S)

    def _tick(self) -> None:
        for session_id in self._active():
            if not self._is_eval_session(session_id):
                continue
            self._seen_sessions.add(session_id)
            tracker, problem = self._registry.ensure(session_id)
            if tracker is None:
                if problem:
                    logger.warning("Cannot watch %s: %s", session_id, problem)
                continue
            with tracker.cond:
                pending = dict(tracker.pending_prompt or {})
            if pending.get("confirm_id"):
                self._answer(session_id, pending)

    def _answer(self, session_id: str, pending: Dict[str, Any]) -> None:
        confirm_id = pending["confirm_id"]
        with self._lock:
            record = self._by_confirm_id.get(confirm_id)
            if record is None:
                decision, reason = decide(pending.get("tool") or "", self.approve)
                record = {
                    "session_id": session_id,
                    "tool": pending.get("tool"),
                    "args_summary": pending.get("args_summary"),
                    "decision": decision,
                    "reason": reason,
                    "delivered": False,
                    "waited_s": None,
                    "_seen_at": pending.get("received_at") or time.monotonic(),
                }
                self._by_confirm_id[confirm_id] = record
                self._prompts.append(record)
            elif record["delivered"] or record.get("gone"):
                return
        reply = self._registry.answer_prompt(session_id, confirm_id, record["decision"])
        with self._lock:
            if reply.get("status") == "ok":
                record["delivered"] = True
                record["waited_s"] = round(time.monotonic() - record["_seen_at"], 2)
                record.pop("error", None)
            elif reply.get("refused") in ("stale", "already_answered", "expired"):
                # Someone else (or the prompt's own bound) ended it first.
                record["gone"] = reply["refused"]
            else:
                record["error"] = reply.get("detail") or str(reply)
        if record["delivered"]:
            logger.info(
                "Eval user answered %s for %s in %s",
                record["decision"],
                record["tool"],
                self.scenario_id,
            )

    # ── after the driver ──

    def finish(self, result: dict) -> None:
        """Stop answering, free the backend, and record what happened in *result*.

        Never raises for a backend that stays busy: that is written to
        ``result["backend_not_idle"]`` for the run loop to stop on, so this
        scenario's own result is still kept.
        """
        self._stop_watching()
        now = time.monotonic()
        not_idle = self._settle()
        if not_idle:
            result["backend_not_idle"] = not_idle
        with self._lock:
            prompts = [
                {k: v for k, v in p.items() if not k.startswith("_")}
                for p in self._prompts
            ]
            stuck = [
                p
                for p in self._prompts
                if not p["delivered"]
                and not p.get("gone")
                and now - p["_seen_at"] >= PROMPT_STUCK_S
            ]
        result["prompts"] = prompts
        # A search that asks once per unreadable file raises thousands of these.
        counts: Dict[str, int] = {}
        for p in prompts:
            key = f"{p['tool']}: {p['decision']}"
            counts[key] = counts.get(key, 0) + 1
        result["prompt_counts"] = counts
        if stuck:
            first = stuck[0]
            result["status"] = "INFRA_ERROR"
            result["error"] = (
                f"A permission prompt for '{first['tool']}' waited "
                f"{now - first['_seen_at']:.0f}s and the eval harness could not "
                f"answer it ({first.get('error') or 'never delivered'}), so the "
                "scenario measured the harness, not the agent. Check that the "
                "backend accepts POST /api/chat/confirm-tool."
            )
        if not result.get("turns") and result.get("status") not in ("PASS", "FAIL"):
            self._recover_turns(result)

    def abort(self) -> None:
        """Best-effort cleanup when the scenario raised instead of returning."""
        self._stop_watching()
        not_idle = self._settle()
        if not_idle:
            logger.error(not_idle)

    def _settle(self) -> Optional[str]:
        """Stop the eval turns still running and wait for the backend to free them.

        Returns why the backend did not go idle, or None once it has.
        """
        deadline = time.monotonic() + SETTLE_TIMEOUT_S
        try:
            running = [s for s in self._active() if self._is_eval_session(s)]
            for session_id in running:
                self._seen_sessions.add(session_id)
                resp = requests.post(
                    f"{self.backend_url}/api/chat/cancel",
                    json={"session_id": session_id},
                    headers=_HEADERS,
                    timeout=REQUEST_TIMEOUT_S,
                )
                if resp.status_code not in (200, 404):  # 404: it just finished
                    resp.raise_for_status()
                logger.info(
                    "Stopped %s's turn left running in %s", self.scenario_id, session_id
                )
            while running:
                active = set(self._active())
                running = [s for s in running if s in active]
                if not running:
                    break
                if time.monotonic() > deadline:
                    titles = ", ".join(f"{self._title(s)!r} ({s})" for s in running)
                    return (
                        f"The backend at {self.backend_url} was still running "
                        f"{titles} {SETTLE_TIMEOUT_S:.0f}s after it was told to "
                        "stop, so the next scenario would run against a busy "
                        "backend. Restart the Agent UI backend before re-running."
                    )
                time.sleep(POLL_INTERVAL_S)
        except requests.RequestException as e:
            return (
                f"Could not confirm the backend at {self.backend_url} is idle "
                f"after {self.scenario_id}: {e}. Check it is still running."
            )
        return None

    def _recover_turns(self, result: dict) -> None:
        """Fill ``turns`` from the backend's transcript of this scenario's sessions."""
        try:
            sessions = [
                s["id"]
                for s in reversed(self._scenario_sessions())
                if s["id"] not in self._sessions_before
            ]
            # A session the driver titled loosely still counts if it names us.
            for session_id in sorted(self._seen_sessions):
                if (
                    session_id not in sessions
                    and session_id not in self._sessions_before
                    and self.scenario_id in (self._title(session_id) or "")
                ):
                    sessions.append(session_id)
            turns: List[Dict[str, Any]] = []
            for session_id in sessions:
                messages = self._get(
                    f"/sessions/{session_id}/messages", limit=10000
                ).get("messages", [])
                turns.extend(_turns_from_messages(messages, session_id, len(turns)))
        except requests.RequestException as e:
            result["turns_recovery_error"] = (
                f"Could not read the finished turns back from {self.backend_url}: {e}"
            )
            return
        if not turns:
            return
        result["turns"] = turns
        result.setdefault("session_id", turns[0]["session_id"])
        for turn in reversed(turns):
            if turn["agent_tools"]:
                result["last_tool"] = turn["agent_tools"][-1]
                break


def _turns_from_messages(messages, session_id: str, offset: int) -> List[dict]:
    """Pair each user message with the agent reply after it; unjudged."""
    turns: List[dict] = []
    for message in messages:
        role = message.get("role")
        if role == "user":
            turns.append(
                {
                    "turn": offset + len(turns) + 1,
                    "session_id": session_id,
                    "user_message": message.get("content", ""),
                    "agent_response": None,
                    "agent_tools": [],
                    "judged": False,
                }
            )
        elif role == "assistant" and turns and turns[-1]["agent_response"] is None:
            turns[-1]["agent_response"] = message.get("content", "")
            turns[-1]["agent_tools"] = [
                step["tool"]
                for step in message.get("agent_steps") or []
                if step.get("tool")
            ]
    return turns
