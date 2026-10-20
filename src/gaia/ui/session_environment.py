# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Where one chat may reach: its folders and its internet switch (#5276).

The settings live in the chat database. They are applied to the chat's agent
before every turn and, when the user changes one, to the cached agent right
away — the agent's path checks and tool policy read them on every call, so a
change takes effect on the next step without a restart.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from gaia.logger import get_logger

from .database import ChatDatabase

logger = get_logger(__name__)


class EnvironmentChangeError(ValueError):
    """A change to the chat's folders or internet that cannot be made."""


def _norm(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.expanduser(path)))


def within(child: str, parent: str) -> bool:
    child, parent = _norm(child), _norm(parent)
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)


def load(db: ChatDatabase, session_id: str) -> Dict[str, Any]:
    return db.get_session_environment(session_id)


def apply_to_agent(agent: Any, env: Dict[str, Any]) -> bool:
    """Give *agent* the chat's folders and internet switch.

    Returns:
        Whether the folder settings are enforced: False for an agent with no
        path validator (a relayed sidecar keeps its own scope).
    """
    agent.internet_off = bool(env["internet_off"])
    validator = getattr(agent, "path_validator", None)
    if validator is None:
        if env["folders"] or env["withheld"]:
            logger.warning(
                "%s has no path validator; this chat's folder settings are not "
                "enforced for it",
                type(agent).__name__,
            )
        return False
    for path in env["folders"]:
        validator.release_path(path)
        validator.add_allowed_path(path)
    for path in env["withheld"]:
        validator.withhold_path(path)
    return True


def apply_environment(agent: Any, session_id: str, db: ChatDatabase) -> None:
    """Called before every turn, beside the memory state."""
    apply_to_agent(agent, load(db, session_id))


def relayed(session: Dict[str, Any]) -> bool:
    """Whether the chat's agent runs as its own process, out of GAIA's reach."""
    from ._chat_helpers import _should_relay_to_sidecar, get_agent_registry

    return _should_relay_to_sidecar(
        session.get("agent_type") or "chat", get_agent_registry()
    )


def _takes_folders(session: Dict[str, Any]) -> bool:
    """Whether the chat's agent can be held to its folders: in-process, or sent them."""
    if not relayed(session):
        return True
    from gaia.ui.email_sidecar.profiles import profile_for

    profile = profile_for(session.get("agent_type") or "chat")
    return profile is not None and profile.min_folders_api_version is not None


def _refuse(what: str) -> EnvironmentChangeError:
    return EnvironmentChangeError(
        f"This chat's agent runs as its own process, so GAIA can't hold it to {what}. "
        "Start a chat with the GAIA agent to use them."
    )


def _require_enforceable(db: ChatDatabase, session_id: str) -> None:
    """Refuse an internet setting for an agent GAIA can't hold to it."""
    session = db.get_session(session_id)
    if session is not None and relayed(session):
        raise _refuse("these settings")


def _require_folders_reach(db: ChatDatabase, session_id: str) -> None:
    """Refuse a folder setting for an agent that is neither in-process nor sent it."""
    session = db.get_session(session_id)
    if session is not None and not _takes_folders(session):
        raise _refuse("these folder settings")


def relayed_scope(db: ChatDatabase, session_id: str) -> Dict[str, Any]:
    """The folder settings a relayed agent is sent each turn to apply itself."""
    from ._chat_helpers import _project_root

    env = load(db, session_id)
    return {
        "folders": list(env["folders"]),
        "withheld": list(env["withheld"]),
        "working_folder": _project_root(env["working_folder"]),
    }


def _checked_folder(path: str) -> Path:
    """*path* as a folder GAIA may be given, or why it can't be."""
    from ._chat_helpers import _unsafe_directory_grant_reason

    target = Path(os.path.expanduser(path)).resolve()
    if not target.is_dir():
        raise EnvironmentChangeError(f"{target} is not a folder on this PC.")
    reason = _unsafe_directory_grant_reason(target)
    if reason:
        raise EnvironmentChangeError(
            f"GAIA can't be given {target}: {reason}. Pick a narrower folder."
        )
    return target


def set_working_folder(db: ChatDatabase, session_id: str, path: str) -> None:
    """Make *path* the folder this chat works in, before its first message.

    The agent is built around its working folder, so a chat that has started
    keeps the one it has; adding a folder is how it reaches another.
    """
    session = db.get_session(session_id)
    if session is None:
        raise EnvironmentChangeError(f"Chat {session_id} does not exist.")
    if session.get("message_count", 0) or _live_agent(session_id) is not None:
        raise EnvironmentChangeError(
            "This chat has already started, so its working folder is set. Add the "
            "folder to this chat instead, or start a new chat in it."
        )
    _require_folders_reach(db, session_id)
    target = _checked_folder(path)
    db.set_session_working_folder(session_id, str(target))
    logger.info("Chat %s works in %s", session_id, target)


def _live_agent(session_id: str) -> Optional[Any]:
    from ._chat_helpers import cached_session_agent

    return cached_session_agent(session_id)


def _computed_scope(
    db: ChatDatabase, session: Dict[str, Any], env: Dict[str, Any]
) -> List[str]:
    """The scope a new agent for this chat would get, before the user's changes."""
    from ._chat_helpers import _compute_allowed_paths, _resolve_rag_paths

    rag, library = _resolve_rag_paths(db, session.get("document_ids", []))
    return _compute_allowed_paths(rag + library, env["working_folder"])


def describe(db: ChatDatabase, session: Dict[str, Any]) -> Dict[str, Any]:
    """What the session panel shows: every folder and file the chat may touch."""
    from ._chat_helpers import _managed_documents_dir, default_working_dir

    session_id = session["id"]
    env = load(db, session_id)
    agent = _live_agent(session_id)
    validator = getattr(agent, "path_validator", None) if agent else None
    if validator is not None:
        scope = [str(p) for p in validator.allowed_paths]
        scratch = str(validator.scratch_dir) if validator.scratch_dir else None
    else:
        scope = _computed_scope(db, session, env) + list(env["folders"])
        scratch = None

    added = {_norm(p) for p in env["folders"]}
    managed = _norm(str(_managed_documents_dir()))
    doc_paths = {
        _norm(d["filepath"])
        for d in db.get_session_documents(session_id)
        if d.get("filepath")
    }
    cwd = _norm(env["working_folder"] or str(default_working_dir()))
    entries: Dict[str, Dict[str, Any]] = {}
    for path in scope:
        key = _norm(path)
        if key in entries or any(within(path, w) for w in env["withheld"]):
            continue
        if key in added:
            source = "added"
        elif key == managed:
            source = "gaia_documents"
        elif scratch and key == _norm(scratch):
            source = "scratch"
        elif key in doc_paths:
            source = "document"
        elif key == cwd:
            source = "working_folder"
        elif validator is not None:
            source = "approved"
        else:
            source = "document"
        entries[key] = {
            "path": str(Path(path)),
            "kind": "file" if Path(path).is_file() else "folder",
            "source": source,
            # An attached document is removed from the chat's documents, which
            # also drops it from search; withholding the file alone would not.
            "removable": source != "document",
        }
    return {
        "session_id": session_id,
        "folders": sorted(
            entries.values(), key=lambda e: (e["kind"] != "folder", e["path"].lower())
        ),
        "removed": list(env["withheld"]),
        "internet": not env["internet_off"],
        "working_folder": env["working_folder"],
        # A relayed agent is sent its folders each turn; its internet is its own.
        "enforced": _takes_folders(session)
        and (relayed(session) or validator is not None or agent is None),
        "internet_enforced": not relayed(session)
        and (validator is not None or agent is None),
    }


def add_folder(db: ChatDatabase, session_id: str, path: str) -> None:
    """Let the chat read and write *path*, now and in later turns."""
    _require_folders_reach(db, session_id)
    target = _checked_folder(path)
    env = load(db, session_id)
    covering = [
        w
        for w in env["withheld"]
        if within(str(target), w) and _norm(w) != _norm(str(target))
    ]
    if covering:
        raise EnvironmentChangeError(
            f"{target} is inside {covering[0]}, which you removed from this chat. "
            "Add that folder back instead."
        )
    withheld = [w for w in env["withheld"] if not within(w, str(target))]
    folders = [f for f in env["folders"] if _norm(f) != _norm(str(target))]
    folders.append(str(target))
    db.set_session_environment(
        session_id,
        folders=folders,
        withheld=withheld,
        internet_off=env["internet_off"],
    )
    agent = _live_agent(session_id)
    validator = getattr(agent, "path_validator", None) if agent else None
    if validator is not None:
        for w in env["withheld"]:
            if w not in withheld:
                validator.release_path(w)
        validator.add_allowed_path(str(target))
    logger.info("Chat %s may now use %s", session_id, target)


def remove_folder(db: ChatDatabase, session_id: str, path: str) -> None:
    """Take *path* away from the chat; enforced from the agent's next step."""
    _require_folders_reach(db, session_id)
    target = str(Path(os.path.expanduser(path)).resolve())
    env = load(db, session_id)
    folders = [f for f in env["folders"] if not within(f, target)]
    withheld = [w for w in env["withheld"] if not within(w, target)]
    withheld.append(target)
    db.set_session_environment(
        session_id,
        folders=folders,
        withheld=withheld,
        internet_off=env["internet_off"],
    )
    agent = _live_agent(session_id)
    validator = getattr(agent, "path_validator", None) if agent else None
    if validator is not None:
        validator.withhold_path(target)
    logger.info("Chat %s may no longer use %s", session_id, target)


def set_internet(db: ChatDatabase, session_id: str, on: bool) -> None:
    """Turn the chat's internet on or off; enforced from the agent's next step."""
    _require_enforceable(db, session_id)
    env = load(db, session_id)
    db.set_session_environment(
        session_id,
        folders=env["folders"],
        withheld=env["withheld"],
        internet_off=not on,
    )
    agent = _live_agent(session_id)
    if agent is not None:
        agent.internet_off = not on
    logger.info("Chat %s internet %s", session_id, "on" if on else "off")
