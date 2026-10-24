# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""FollowUpToolsMixin — lets an agent schedule its own check-backs.

"Check on the build in 2 hours" becomes a one-shot job on the daemon clock
(:mod:`gaia.daemon.followups`). When it comes due the daemon runs the task, keeps
the answer and posts a desktop notification, and the agent's next turn on any
front end opens with what came back.
"""

from datetime import datetime
from typing import Any, Dict

from gaia.logger import get_logger

logger = get_logger(__name__)


class FollowUpToolsMixin:
    """
    Mixin providing follow-up tools.

    Tools provided:
    - schedule_follow_up: run a task later, once, and report the result
    - list_follow_ups: what is waiting, and results not yet shown
    - cancel_follow_up: drop one that is no longer needed

    ``follow_up_context()`` is the per-turn line that reports finished ones;
    ``follow_ups_told()`` marks them seen once that turn has answered.
    """

    #: The agent the daemon runs a fired follow-up as.
    FOLLOW_UP_AGENT_ID = "gaia"

    def register_follow_up_tools(self) -> None:
        """Register the follow-up tools into _TOOL_REGISTRY."""
        from gaia.agents.base.tools import tool

        self._follow_ups_enabled = True

        @tool(display_label="Scheduling a follow-up")
        def schedule_follow_up(when: str, task: str) -> Dict[str, Any]:
            """Do something later, once, on your own: check on a job, a build, a reply, or a result that is not ready yet, or follow up on a promise to the user. GAIA runs the task at that time even with no chat open, notifies the user, and tells them the result in their next chat. Use it whenever the user says "remind me", "check back", "follow up" or "let me know when", and when you are waiting on something that will finish later.

            Args:
                when: When to do it, from a minute to 7 days ahead: "in 2 hours", "tomorrow at 9am", "at 16:30", or an ISO time.
                task: What to do then, written so it makes sense on its own later: name the file, URL, command, job or person, and what result to report.
            """
            return self._schedule_follow_up(when, task)

        @tool(display_label="Listing follow-ups")
        def list_follow_ups() -> Dict[str, Any]:
            """List the follow-ups still waiting, and the results of finished ones not yet told to the user."""
            return self._list_follow_ups()

        @tool(display_label="Cancelling a follow-up")
        def cancel_follow_up(follow_up_id: str) -> Dict[str, Any]:
            """Cancel a waiting follow-up that is no longer needed.

            Args:
                follow_up_id: The id schedule_follow_up or list_follow_ups returned.
            """
            return self._cancel_follow_up(follow_up_id)

    def _schedule_follow_up(self, when: str, task: str) -> Dict[str, Any]:
        from gaia.daemon import client, followups
        from gaia.daemon.errors import DaemonError

        try:
            fire_at = followups.parse_when(when)
        except followups.FollowUpError as e:
            return {"status": "error", "error": str(e)}
        try:
            job = client.followups_request(
                "POST",
                json={
                    "agent_id": self.FOLLOW_UP_AGENT_ID,
                    "task": task,
                    "fire_at": fire_at.timestamp(),
                    "origin": {"session": getattr(self, "_memory_session_id", None)},
                    "model": getattr(self, "model_id", None),
                },
            )
        except DaemonError as e:
            return {"status": "error", "error": f"Not scheduled: {e}"}
        local = fire_at.strftime("%a %b %d at %H:%M")
        return {
            "status": "scheduled",
            "id": job["id"],
            "runs_at": local,
            "task": job["task"],
            "message": (
                f"Scheduled for {local}. GAIA runs it then even with no chat "
                "open, notifies the user, and reports the result in their next "
                "chat. Tell the user when it will run."
            ),
        }

    def _list_follow_ups(self) -> Dict[str, Any]:
        from gaia.daemon import client
        from gaia.daemon.errors import DaemonError

        try:
            found = client.followups_request(
                "GET", params={"agent_id": self.FOLLOW_UP_AGENT_ID}
            )
        except DaemonError as e:
            return {"status": "error", "error": f"Could not list follow-ups: {e}"}
        pending = [
            {"id": p["id"], "runs_at": _local(p["fire_at"]), "task": p["task"]}
            for p in found["pending"]
        ]
        results = [_result_view(r) for r in found["results"]]
        # Told once this turn answers, like the per-turn line; never in a fired run.
        if not self._in_follow_up_run():
            shown = getattr(self, "_follow_ups_shown", [])
            ids = [r["job_id"] for r in found["results"]]
            self._follow_ups_shown = list(dict.fromkeys([*shown, *ids]))
        return {"status": "found", "pending": pending, "results": results}

    def _cancel_follow_up(self, follow_up_id: str) -> Dict[str, Any]:
        from gaia.daemon import client
        from gaia.daemon.errors import DaemonError

        try:
            client.followups_request(
                "POST",
                f"/{follow_up_id}/cancel",
                params={"agent_id": self.FOLLOW_UP_AGENT_ID},
            )
        except DaemonError as e:
            return {"status": "error", "error": str(e)}
        return {"status": "cancelled", "id": follow_up_id}

    def _in_follow_up_run(self) -> bool:
        """True in the turn the daemon runs for a fired follow-up."""
        from gaia.daemon import followups

        config = getattr(self, "config", None)
        return followups.is_run_session(getattr(config, "ui_session_id", None))

    def follow_ups_told(self, answered: bool) -> None:
        """Mark the results this turn showed as told, if the turn answered.

        A turn that failed keeps them for the next one. A daemon that cannot be
        reached is logged and the results stay unseen; it never fails the turn.
        """
        shown, self._follow_ups_shown = getattr(self, "_follow_ups_shown", []), []
        if not shown or not answered:
            return
        from gaia.daemon import client
        from gaia.daemon.errors import DaemonError

        try:
            # Attach only: a turn must never start a daemon.
            client.followups_request(
                "POST",
                "/seen",
                json={"agent_id": self.FOLLOW_UP_AGENT_ID, "ids": shown},
                timeout=3.0,
                start=False,
            )
        except DaemonError as e:
            logger.info(
                "follow-up results not marked seen; shown again next turn: %s", e
            )

    def follow_up_context(self) -> str:
        """Finished follow-ups the user has not been told about yet.

        Empty when there are none, when this agent never registered the tools,
        or in a fired follow-up's own run, where no user reads the answer. A
        daemon that cannot be asked is logged and leaves the turn without the
        line. What is shown is marked told by :meth:`follow_ups_told`.
        """
        self._follow_ups_shown = []
        if not getattr(self, "_follow_ups_enabled", False):
            return ""
        from gaia.daemon import client
        from gaia.daemon.errors import DaemonError

        if self._in_follow_up_run():
            return ""

        try:
            # Attach only: a turn must never start a daemon just to ask.
            found = client.followups_request(
                "GET",
                params={"agent_id": self.FOLLOW_UP_AGENT_ID},
                timeout=3.0,
                start=False,
            )
        except DaemonError as e:
            logger.info("follow-up results not checked this turn: %s", e)
            return ""
        results = found.get("results") or []
        if not results:
            return ""
        lines = ["[Follow-ups you ran since the user last heard from you — tell them:]"]
        for r in results:
            view = _result_view(r)
            outcome = view.get("answer") or f"FAILED: {view.get('error')}"
            lines.append(f"- {view['finished']}: {view['task']}\n  Result: {outcome}")
        self._follow_ups_shown = [r["job_id"] for r in results]
        return "\n".join(lines)


def _local(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%a %b %d at %H:%M")


def _result_view(row: Dict[str, Any]) -> Dict[str, Any]:
    view = {"task": row["task"], "finished": _local(row["finished_at"])}
    if row.get("answer"):
        view["answer"] = row["answer"]
    if row.get("error"):
        view["error"] = row["error"]
    if row.get("note"):
        view["note"] = row["note"]
    return view
