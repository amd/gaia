# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What the daemon clears up at start for agents GAIA no longer ships.

Three things an upgrade leaves behind: the agent's install folder, the access
it was granted to connected accounts, and work it had scheduled. The folder and
the access are removed. Scheduled work cannot run any more, so it is reported
once instead; the agent's own data under ``~/.gaia/<id>/`` is never deleted.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from gaia.home import default_gaia_home
from gaia.logger import get_logger
from gaia.retired_agents import EMAIL_DOCS_URL, RETIRED_AGENT_IDS

logger = get_logger(__name__)

#: Written beside the old store once its unsent work has been reported.
NOTICE_MARKER = ".removed-agent-notice"
#: Written under the daemon's folder once retired agents' grants are revoked.
GRANTS_REVOKED_MARKER = "retired-agent-grants-revoked"


def revoke_retired_grants(done_marker: Optional[Path] = None) -> List[str]:
    """Take back every connector grant a retired agent held; return what went.

    Runs once per GAIA home: an agent that later ships under a retired id is a
    different agent, and the access a person then gives it must stay.
    """
    from gaia.connectors.grants import load_grants, revoke_agent_grant
    from gaia.daemon.paths import host_dir

    marker = done_marker or host_dir() / GRANTS_REVOKED_MARKER
    if marker.exists():
        return []
    revoked: List[str] = []
    for connector_id, agents in sorted(load_grants().items()):
        for agent_id in RETIRED_AGENT_IDS:
            namespaced = f"installed:{agent_id}"
            if namespaced not in agents:
                continue
            revoke_agent_grant(connector_id, namespaced)
            logger.warning(
                "daemon: revoked the removed '%s' agent's access to %s",
                namespaced,
                connector_id,
            )
            revoked.append(f"{connector_id}:{namespaced}")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(", ".join(RETIRED_AGENT_IDS), encoding="utf-8")
    return revoked


def _pending_email_jobs(state_db: Path) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(f"{state_db.as_uri()}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='email_scheduled_jobs'"
        ).fetchone()
        if not has_table:
            return []
        rows = conn.execute(
            "SELECT job_id, kind, due_at, payload_json FROM email_scheduled_jobs "
            "WHERE status IN ('pending', 'firing') ORDER BY due_at"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _describe(job: Dict[str, Any]) -> str:
    try:
        payload = json.loads(job.get("payload_json") or "{}")
    except ValueError:
        payload = {}
    subject = payload.get("subject") if isinstance(payload, dict) else None
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(job["due_at"])))
    what = "scheduled email" if job["kind"] == "scheduled_send" else job["kind"]
    named = f' "{subject}"' if subject else ""
    return f"{what}{named} due {when}"


def report_abandoned_email_work(email_home: Optional[Path] = None) -> List[str]:
    """Say, once, which mail the removed email agent had scheduled and never sent.

    Reads the agent's own store and changes nothing in it. Returns one line per
    item reported; empty when there is nothing, or it was reported before.
    """
    home = email_home or default_gaia_home() / "email"
    state_db = home / "state.db"
    marker = home / NOTICE_MARKER
    if not state_db.is_file() or marker.exists():
        return []
    try:
        jobs = _pending_email_jobs(state_db)
    except sqlite3.Error as exc:
        logger.error(
            "daemon: could not read %s to check for mail the removed email "
            "agent had scheduled: %s. Open it with a SQLite viewer and look at "
            "the email_scheduled_jobs table.",
            state_db,
            exc,
        )
        return []
    items = [_describe(job) for job in jobs]
    for item in items:
        logger.warning(
            "daemon: the email agent was removed, so this will not happen: %s. "
            "Its data is still in %s.",
            item,
            home,
        )
    if items:
        _notify(items, home)
    marker.write_text(
        "GAIA no longer ships the email agent. This folder holds its old data "
        f"and is safe to delete. See {EMAIL_DOCS_URL}\n",
        encoding="utf-8",
    )
    return items


def _notify(items: List[str], home: Path) -> None:
    message = (
        "The email agent was removed from GAIA, so these will not go out: "
        + "; ".join(items)
        + f". Ask GAIA in a chat to send them instead. The old data is in {home}."
    )
    try:
        from gaia.schedule import sinks

        sinks.dispatch(
            "notification", {"title": "Scheduled email will not be sent"}, message
        )
    except Exception as exc:  # the log lines above already carry every item
        logger.warning("daemon: could not show the removed-agent notice: %s", exc)


def clean_up_retired_agents() -> None:
    """Run every retired-agent cleanup step; one failing never skips the rest."""
    from gaia.daemon.sidecars.install import remove_retired_agents

    for step in (
        remove_retired_agents,
        revoke_retired_grants,
        report_abandoned_email_work,
    ):
        try:
            step()
        except Exception as exc:  # start-up must not die on leftover state
            logger.error(
                "daemon: retired-agent cleanup step %s failed: %s. GAIA will "
                "try again at its next start.",
                step.__name__,
                exc,
            )
