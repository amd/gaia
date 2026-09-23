# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Per-agent knobs for the shared sidecar ``/query`` relay (#4161).

Everything about relaying a sidecar run is identical between agents except a
handful of values: the contract floor to refuse below, the copy the user sees,
which tool names deserve a friendly label, and which tools change durable state
and so warrant a visible status line. Those live here, one :class:`RelayProfile`
per agent, so :mod:`gaia.ui.email_sidecar.relay` stays agent-agnostic and adding
the next sidecar is a profile, not another branch.

Deliberately NOT in ``gaia.daemon.sidecars.spec``: that spec describes what the
daemon must do to *supervise* a sidecar (binaries, tokens, modes, connector
scopes). This is presentation — what the Agent UI shows while relaying one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Dict, FrozenSet, Mapping, Optional, Tuple


@dataclass(frozen=True)
class RelayProfile:
    """The agent-specific half of one ``/query`` relay."""

    agent_id: str
    #: How the agent is named in user-facing copy, mid-sentence ("the email
    #: agent isn't ready yet"). Lower-case except for proper nouns.
    display_name: str
    #: Terminal error when the stream closes without a ``final``/``error``.
    #: Per-agent so it names the log the reader should actually open.
    stream_ended_message: str
    #: Contract floor, as ``(major, minor)``. A sidecar below this 404s or
    #: mis-speaks the relay's request shape, so it is refused BEFORE the first
    #: POST with :attr:`version_upgrade_message` rather than failing obscurely
    #: mid-stream. The daemon's own handshake only pins MAJOR.
    min_api_version: Tuple[int, int]
    #: Shown when :attr:`min_api_version` is not met — at pre-flight and again
    #: as the 404 backstop in the relay, so both read identically.
    version_upgrade_message: str
    #: ``tool_call`` name -> friendly label. The relay has no in-process tool
    #: registry for a sidecar's tools, so each agent owns its own map; anything
    #: unlisted is humanized from the tool name.
    tool_labels: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    #: Tools that change durable state without passing a confirmation gate.
    #: The relay emits a visible status line for these rather than burying
    #: them in the collapsed activity panel.
    mutating_tools: FrozenSet[str] = frozenset()
    #: What that status line calls the change ("✎ <change_noun>: <tool>"), so
    #: it names what was actually touched instead of a generic "change".
    change_noun: str = "change"
    #: Appended — never substituted — to a connection-shaped terminal error,
    #: because the sidecar emits ``str(exc)`` verbatim and its commonest
    #: failure otherwise reaches the user as a raw urllib3 repr.
    lemonade_hint: str = ""
    #: Whether this agent is ALWAYS served by its sidecar, even when an
    #: importable in-process registration exists. True for email, whose
    #: in-process tool-calling loop was retired in favour of the relay
    #: (#2109). False for the flagship: a wheel/dev install registers a real
    #: factory and runs in-process (that is what ``gaia eval agent`` drives),
    #: while a Hub binary install registers only the raise-on-call stand-in
    #: and must relay. Which one you have is an install-kind question, not an
    #: agent-id question.
    always_relay: bool = False
    #: Whether a not-ready ``/init`` (503) STOPS the turn.
    #:
    #: True for email: its ``/init`` is half of a provisioning handshake the
    #: user completes from the agent card, and running a triage against an
    #: unprovisioned mailbox wastes a long model call to reach the same
    #: answer.
    #:
    #: False for the flagship, whose ``/init`` is only a read-only probe —
    #: and an imperfect one. Observed: against a Lemonade that requires
    #: auth, its probe reports unreachable while ``/query`` answers normally.
    #: Halting on that turns a working agent into a dead one, so the hint is
    #: shown and the turn proceeds; if the backend really is down, the query's
    #: own error says so with the remedy already appended.
    readiness_is_blocking: bool = True
    #: Whether to send ``session_id`` on the ``/query`` body. Gated per agent
    #: because the request model is ``extra="forbid"`` on both sidecars: an
    #: unexpected field is a 422, not a shrug.
    sends_session_id: bool = False


# --- email ------------------------------------------------------------------

#: Mutating email tools that execute WITHOUT confirmation under ``/query``
#: (``CONFIRMATION_REQUIRED_TOOLS`` gates only send/RSVP/forward/
#: quarantine/calendar-create — see
#: ``gaia_agent_email.agent.EmailTriageAgent.CONFIRMATION_REQUIRED_TOOLS``).
_EMAIL_MUTATING_TOOLS = frozenset(
    {
        "archive_message",
        "archive_message_batch",
        "undo_archive_batch",
        "mark_read",
        "mark_unread",
        "mark_read_batch",
        "mark_unread_batch",
        "add_star",
        "remove_star",
        "add_star_batch",
        "remove_star_batch",
        "label_message",
        "label_message_batch",
        "move_to_label",
        "move_to_label_batch",
        "trash_message",
        "restore_message",
        "restore_trashed_message",
        "snooze_message",
        "cancel_scheduled_job",
        "unquarantine_message",
        "set_priority_sender",
        "set_low_priority_sender",
        "set_category_default",
        "clear_session_preferences",
        "build_voice_profile",
        "clear_voice_profile",
    }
)

_EMAIL_TOOL_LABELS: Dict[str, str] = {
    "pre_scan_inbox": "Scanning inbox",
    "triage_inbox": "Triaging inbox",
    "search_messages": "Searching mail",
    "search_trash": "Searching Trash",
    "list_inbox": "Listing inbox",
    "get_message": "Reading message",
    "get_thread": "Reading thread",
    "summarize_thread": "Summarizing thread",
    "summarize_message": "Summarizing message",
    "list_labels": "Listing labels",
    "check_followups": "Checking follow-ups",
    "profile_inbox": "Profiling inbox",
    "draft_reply": "Drafting reply",
    "draft_forward": "Drafting forward",
    "send_draft": "Sending draft",
    "send_now": "Sending message",
    "forward_message": "Forwarding message",
    "schedule_send": "Scheduling send",
    "archive_message": "Archiving message",
    "archive_message_batch": "Archiving messages",
    "undo_archive_batch": "Undoing archive",
    "mark_read": "Marking read",
    "mark_unread": "Marking unread",
    "mark_read_batch": "Marking messages read",
    "mark_unread_batch": "Marking messages unread",
    "add_star": "Starring message",
    "remove_star": "Unstarring message",
    "add_star_batch": "Starring messages",
    "remove_star_batch": "Unstarring messages",
    "label_message": "Labeling message",
    "label_message_batch": "Labeling messages",
    "move_to_label": "Moving message",
    "move_to_label_batch": "Moving messages",
    "trash_message": "Trashing message",
    "restore_message": "Restoring message",
    "restore_trashed_message": "Restoring message from Trash",
    "snooze_message": "Snoozing message",
    "cancel_scheduled_job": "Cancelling scheduled job",
    "list_scheduled_jobs": "Listing scheduled jobs",
    "list_calendar_events": "Checking calendar",
    "accept_invite": "Accepting invite",
    "decline_invite": "Declining invite",
    "create_event_from_email": "Creating calendar event",
    "detect_meeting_request": "Detecting meeting request",
    "detect_calendar_conflicts": "Checking calendar conflicts",
    "quarantine_phishing_message": "Quarantining suspicious message",
    "unquarantine_message": "Restoring quarantined message",
    "set_priority_sender": "Updating priority sender",
    "set_low_priority_sender": "Updating low-priority sender",
    "set_category_default": "Updating category preference",
    "clear_session_preferences": "Clearing session preferences",
    "build_voice_profile": "Building voice profile",
    "clear_voice_profile": "Clearing voice profile",
}

EMAIL_PROFILE = RelayProfile(
    agent_id="email",
    display_name="email",
    # 2.4 is where /query itself landed; a pre-2.4 Hub binary passes the
    # daemon's MAJOR-only handshake and then 404s every call.
    min_api_version=(2, 4),
    stream_ended_message=(
        "Email agent stream ended unexpectedly (the sidecar may have "
        "crashed). Check the sidecar log under ~/.gaia/logs/ and retry."
    ),
    version_upgrade_message=(
        "The installed email agent doesn't support chat queries (needs contract "
        "2.4+). Update it from the Hub and retry."
    ),
    tool_labels=MappingProxyType(_EMAIL_TOOL_LABELS),
    mutating_tools=_EMAIL_MUTATING_TOOLS,
    change_noun="mailbox change",
    lemonade_hint=(
        "\n\nThis usually means the local LLM backend (Lemonade Server) is "
        "not running or unreachable from the email agent. Start Lemonade "
        "Server, then retry."
    ),
    always_relay=True,
    # Left off deliberately: the email relay has never sent one, and turning it
    # on would change which agent instance a live email session resolves to
    # (gaia_agent_email.query_routes threads it into a session-scoped registry).
    # That is its own change, with its own test, not a rider on this one.
    sends_session_id=False,
)


# --- gaia (the flagship) ----------------------------------------------------

#: Flagship tools that write something the user keeps — files, indexes, the
#: skill library, memory — or execute code. Read-only tools (search, fetch,
#: read, list) are not here. Every name is drift-guarded against the real
#: registry by tests/unit/chat/ui/test_sidecar_relay_profiles.py.
_GAIA_MUTATING_TOOLS = frozenset(
    {
        "run_shell_command",
        "write_file",
        "write_markdown_file",
        "write_python_file",
        "edit_file",
        "edit_python_file",
        "replace_function",
        "execute_python_file",
        "run_python",
        "download_file",
        "install_skill",
        "remove_skill",
        "capture_skill",
        "remember_skill_lesson",
        "update_gaia_md",
        "add_watch_directory",
        "create_table",
        "drop_table",
        "insert_data",
        "index_codebase",
        "clear_code_index",
        "index_document",
        "index_directory",
        "generate_image",
        "write_clipboard",
        "bookmark",
    }
)

_GAIA_TOOL_LABELS: Dict[str, str] = {
    # Documents / RAG
    "index_document": "Indexing document",
    "index_directory": "Indexing folder",
    "query_documents": "Searching documents",
    "query_specific_file": "Reading document",
    "search_indexed_chunks": "Searching documents",
    "list_indexed_documents": "Listing indexed documents",
    "summarize_document": "Summarizing document",
    "dump_document": "Exporting document",
    "rag_status": "Checking the document index",
    "evaluate_retrieval": "Checking retrieval quality",
    "analyze_data_file": "Analyzing data file",
    # Files
    "read_file": "Reading file",
    "write_file": "Writing file",
    "write_markdown_file": "Writing document",
    "write_python_file": "Writing script",
    "edit_file": "Editing file",
    "edit_python_file": "Editing script",
    "replace_function": "Editing function",
    "execute_python_file": "Running script",
    "run_python": "Running Python",
    "run_shell_command": "Running a shell command",
    "get_file_info": "Inspecting file",
    "list_recent_files": "Listing recent files",
    "search_file": "Searching files",
    "search_file_content": "Searching file contents",
    "search_directory": "Searching folder",
    "generate_diff": "Comparing files",
    "find_files": "Finding files",
    "list_files": "Listing files",
    "file_info": "Inspecting file",
    "browse_directory": "Browsing folder",
    "tree": "Listing folder tree",
    "add_watch_directory": "Watching folder",
    # Web
    "search_web": "Searching the web",
    "search_documentation": "Searching documentation",
    "fetch_page": "Reading web page",
    "fetch_webpage": "Reading web page",
    "open_url": "Opening link",
    "download_file": "Downloading file",
    "bookmark": "Saving bookmark",
    # Code
    "index_codebase": "Indexing codebase",
    "search_code": "Searching code",
    "search_code_index": "Searching code index",
    "get_index_status": "Checking index status",
    "clear_code_index": "Clearing code index",
    # Data
    "create_table": "Creating table",
    "insert_data": "Adding rows",
    "query_data": "Querying data",
    "list_tables": "Listing tables",
    "drop_table": "Dropping table",
    # Skills
    "list_skills": "Listing skills",
    "search_skill_hub": "Searching the skill hub",
    "install_skill": "Installing skill",
    "remove_skill": "Removing skill",
    "load_skill": "Loading skill",
    "unload_skill": "Unloading skill",
    "skill_status": "Checking skills",
    "capture_skill": "Saving skill",
    "remember_skill_lesson": "Remembering a lesson",
    "load_tools": "Loading tools",
    "request_user_input": "Asking you a question",
    # Media / desktop
    "transcribe_media": "Transcribing recording",
    "refine_transcript": "Refining transcript",
    "take_screenshot": "Taking screenshot",
    "generate_image": "Generating image",
    "analyze_image": "Looking at image",
    "answer_question_about_image": "Reading image",
    "list_sd_models": "Listing image models",
    "get_generation_history": "Listing generated images",
    "text_to_speech": "Speaking",
    "list_windows": "Listing windows",
    "notify_desktop": "Sending a notification",
    "read_clipboard": "Reading clipboard",
    "write_clipboard": "Writing clipboard",
    "get_system_info": "Checking this machine",
    # Mail (the read-only subset the flagship carries)
    "list_inbox": "Listing inbox",
    "search_email": "Searching mail",
    "read_email": "Reading message",
    "list_mail_folders": "Listing mail folders",
    "check_mailbox_access": "Checking mailbox access",
    # Memory / config
    "update_gaia_md": "Updating your GAIA.md",
}

GAIA_PROFILE = RelayProfile(
    agent_id="gaia",
    display_name="GAIA",
    # 2.12 introduced session_id, which the Agent UI needs: without it a
    # multi-turn session forgets the documents it indexed on the turn before.
    # Every published flagship binary is 2.13, so this refuses nothing that
    # exists — it guards a downgrade, not a supported install.
    min_api_version=(2, 12),
    stream_ended_message=(
        "GAIA agent stream ended unexpectedly (the sidecar may have "
        "crashed). Check the sidecar log under ~/.gaia/logs/ and retry."
    ),
    version_upgrade_message=(
        "The installed GAIA agent is too old for the Agent UI (needs contract "
        "2.12+). Update it from the Hub and retry."
    ),
    tool_labels=MappingProxyType(_GAIA_TOOL_LABELS),
    mutating_tools=_GAIA_MUTATING_TOOLS,
    change_noun="local change",
    lemonade_hint=(
        "\n\nThis usually means the local LLM backend (Lemonade Server) is "
        "not running or unreachable from the GAIA agent. Start Lemonade "
        "Server, then retry."
    ),
    readiness_is_blocking=False,
    sends_session_id=True,
)


_PROFILES: Dict[str, RelayProfile] = {
    EMAIL_PROFILE.agent_id: EMAIL_PROFILE,
    GAIA_PROFILE.agent_id: GAIA_PROFILE,
}

#: Agent ids the Agent UI can relay to a daemon sidecar. The single source of
#: truth — ``_chat_helpers._SIDECAR_AGENT_TYPES`` derives from it, so a profile
#: added here is dispatchable without touching the chat path.
SIDECAR_AGENT_IDS = frozenset(_PROFILES)


def profile_for(agent_id: str) -> Optional[RelayProfile]:
    """The relay profile for *agent_id*, or ``None`` if it is not a sidecar."""
    return _PROFILES.get(agent_id)


def api_version_supported(profile: RelayProfile, api_version: Optional[str]) -> bool:
    """True when *api_version* meets *profile*'s contract floor.

    A missing or unparseable version is NOT treated as good enough: the daemon
    reports it from the sidecar's own ``/version``, so its absence means the
    handshake did not complete and the relay has no evidence the routes exist.
    """
    if not api_version:
        return False
    parts = str(api_version).split(".")
    try:
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return False
    return (major, minor) >= profile.min_api_version


__all__ = [
    "RelayProfile",
    "EMAIL_PROFILE",
    "GAIA_PROFILE",
    "SIDECAR_AGENT_IDS",
    "profile_for",
    "api_version_supported",
]
