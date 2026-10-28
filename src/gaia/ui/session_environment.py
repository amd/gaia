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
from gaia.security import AllowedPathsEnvError, env_allowed_paths

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


def takes_folders(agent_type: Optional[str]) -> bool:
    """Whether a chat with *agent_type* can be given folders.

    An in-process agent always can. A relayed one can when the running
    sidecar's contract says it takes the chat's file scope on every turn.

    Raises:
        EnvironmentChangeError: The relayed agent could not be started to ask.
    """
    from .email_sidecar import daemon_client
    from .email_sidecar.errors import SidecarError
    from .email_sidecar.profiles import profile_for, scope_supported

    agent_type = agent_type or "chat"
    if not relayed({"agent_type": agent_type}):
        return True
    profile = profile_for(agent_type)
    if profile is None or profile.min_scope_api_version is None:
        return False
    try:
        handle = daemon_client.acquire_handle(agent_type)
    except SidecarError as e:
        logger.error("Could not start the %s agent to check folders: %s", agent_type, e)
        raise EnvironmentChangeError(profile.start_failed_text(e.kind)) from e
    return scope_supported(profile, handle.api_version)


def folders_refusal(agent_type: Optional[str]) -> str:
    """What to tell the user when *agent_type* can't be given folders."""
    from .email_sidecar.profiles import profile_for

    profile = profile_for(agent_type or "chat")
    if profile is not None and profile.min_scope_api_version is not None:
        return (
            f"The installed {profile.display_name} agent is too old to be given "
            "folders. Update GAIA to the latest version, then try again."
        )
    return (
        "This chat's agent runs as its own process and can't be given folders. "
        "Start a chat with the GAIA agent to use them."
    )


def _require_folders(agent_type: Optional[str]) -> None:
    if not takes_folders(agent_type):
        raise EnvironmentChangeError(folders_refusal(agent_type))


def _require_holdable(session: Dict[str, Any]) -> None:
    if relayed(session):
        raise EnvironmentChangeError(
            "This chat's agent runs as its own process, so GAIA can't take a "
            "folder away from it or turn its internet off. To work without a "
            "folder, start a new chat."
        )


def _require_enforceable(db: ChatDatabase, session_id: str) -> None:
    session = db.get_session(session_id)
    if session is not None:
        _require_holdable(session)


def check_new_chat_folders(agent_type: Optional[str], folders: List[str]) -> None:
    """Refuse folders a chat about to be made with *agent_type* could not be given.

    Checked before the chat exists, so a refused folder leaves no chat behind.

    Raises:
        EnvironmentChangeError: The agent can't be given folders, or a
            folder is missing or too broad.
    """
    if not folders:
        return
    _require_folders(agent_type)
    for path in folders:
        check_folder(path)


def _live_agent(session_id: str) -> Optional[Any]:
    from ._chat_helpers import cached_session_agent

    return cached_session_agent(session_id)


def _computed_scope(db: ChatDatabase, session: Dict[str, Any]) -> List[str]:
    """The scope a new agent for this chat would get, before the user's changes."""
    from ._chat_helpers import _compute_allowed_paths, _resolve_rag_paths

    rag, library = _resolve_rag_paths(db, session.get("document_ids", []))
    return _compute_allowed_paths(rag + library)


def _env_scope_entries() -> List[Path]:
    """The ``GAIA_ALLOWED_PATHS`` entries, or none when it is unset or invalid."""
    try:
        return env_allowed_paths() or []
    except AllowedPathsEnvError:
        return []


def describe(db: ChatDatabase, session: Dict[str, Any]) -> Dict[str, Any]:
    """What the session panel shows: every folder and file the chat may touch.

    Raises:
        AllowedPathsEnvError: When ``GAIA_ALLOWED_PATHS`` holds an invalid value.
    """
    from ._chat_helpers import _effective_scope, _managed_documents_dir

    session_id = session["id"]
    env = load(db, session_id)
    agent = _live_agent(session_id)
    validator = getattr(agent, "path_validator", None) if agent else None
    if validator is not None:
        scope = [str(p) for p in validator.allowed_paths]
        scratch = str(validator.scratch_dir) if validator.scratch_dir else None
    else:
        # What a new agent's validator would hold, GAIA_ALLOWED_PATHS applied.
        scope = _effective_scope(_computed_scope(db, session) + list(env["folders"]))
        scratch = None

    added = {_norm(p) for p in env["folders"]}
    managed = _norm(str(_managed_documents_dir()))
    doc_paths = {
        _norm(d["filepath"])
        for d in db.get_session_documents(session_id)
        if d.get("filepath")
    }
    cwd = _norm(os.getcwd())
    from_env = {_norm(str(p)) for p in _env_scope_entries()}
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
        elif key in from_env:
            source = "allowed_paths"
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
        "enforced": not relayed(session) and (validator is not None or agent is None),
    }


def check_folder(path: str) -> Path:
    """*path* resolved, if a chat may be given it.

    Raises:
        EnvironmentChangeError: It is not a folder, or too broad to grant.
    """
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


def add_folder(db: ChatDatabase, session_id: str, path: str) -> None:
    """Let the chat read and write *path*, now and in later turns."""
    session = db.get_session(session_id)
    if session is not None:
        _require_folders(session.get("agent_type"))
    grant_folder(db, session_id, path)


def grant_folder(db: ChatDatabase, session_id: str, path: str) -> None:
    """:func:`add_folder` for a chat whose agent is known to take folders.

    A new chat's agent was asked once, by :func:`check_new_chat_folders`.
    """
    target = check_folder(path)
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
    _require_enforceable(db, session_id)
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
