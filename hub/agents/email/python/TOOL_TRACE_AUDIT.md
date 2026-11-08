# Email-agent tool trace audit

This audit covers the shared `*_impl` helpers under
`hub/agents/email/python/gaia_agent_email/tools/` and records whether each
helper owns a `log_tool_call` trace. The goal is to identify the public
operation a trace represents and to keep a model-selected tool from becoming
invisible when it delegates to a nested helper.

## Agent-facing tool paths

These helpers back Agent tools and emit a trace under the corresponding
public operation name. The trace is asserted by the static regression test in
`tests/test_tool_trace_audit.py`. Some helpers are also called by REST routes;
that route shares the operation trace rather than claiming a model selected it.

| Module | Helper family | Trace owner |
| --- | --- | --- |
| `calendar_tools.py` | `list_calendar_events_impl`, `detect_calendar_conflicts_impl`, `update_rsvp_impl`, `create_event_from_email_impl` | helper |
| `delete_tools.py` | `trash_message_impl`, `restore_message_impl`, `find_trashed_messages_impl`, `restore_trashed_message_impl`, `permanent_delete_impl` | helper |
| `followup_tools.py` | `check_followups_impl` | helper |
| `organize_tools.py` | `archive_message_impl`, `mark_read_impl`, `mark_unread_impl`, `add_star_impl`, `remove_star_impl`, `label_message_impl`, `move_to_label_impl`, `undo_archive_batch_impl`, `undo_reversible_action_impl` | helper |
| `phishing_tools.py` | `quarantine_phishing_impl`, `unquarantine_impl` | helper |
| `read_tools.py` | `list_inbox_impl`, `get_message_impl`, `get_thread_impl`, `summarize_thread_impl`, `search_messages_impl`, `list_labels_impl`, `triage_inbox_impl`, `pre_scan_inbox_impl` | helper |
| `reply_tools.py` | `draft_reply_impl`, `draft_forward_impl`, `send_draft_impl`, `send_now_impl`, `forward_message_impl` | helper |
| `schedule_tools.py` | `schedule_send_impl`, `snooze_message_impl`, `cancel_scheduled_job_impl`, `list_scheduled_jobs_impl` | helper |
| `summarize_tools.py` | `summarize_message_impl` | helper |
| `voice_tools.py` | `build_voice_profile_impl` | helper |
| `waiting_on_you_tools.py` | `detect_waiting_on_you_impl` | helper |

`detect_meeting_request_impl` is deliberately not a second trace owner: the
public `detect_meeting_request` tool wraps the heuristic/LLM helper in its own
`log_tool_call("detect_meeting_request", ...)` context, so the model-facing
call is logged exactly once under the public name.

Two public tools compose existing work without a dedicated `*_impl` helper:
`get_briefing` wraps the cold multi-mailbox pre-scan, and
`check_suspicious_mail` wraps the same pre-scan with a narrower result. Both
own an outer trace under their public names, so their calls remain visible in
addition to nested pre-scan logging.

## Non-tool helper paths

These helpers are intentionally not logged as Agent tool calls because they
are not themselves registered Agent tools:

| Module | Helper | Caller/scope | Why no tool trace is required |
| --- | --- | --- | --- |
| `attention_tools.py` | `build_attention_view_impl` | Agent UI/REST attention aggregation | It is a server-side aggregation helper, not a model-selected tool. |
| `calendar_tools.py` | `detect_meeting_request_impl` | `detect_meeting_request` wrapper | The public tool owns the trace around heuristic/LLM detection; the helper is intentionally not a second trace owner. |
| `schedule_tools.py` | `execute_scheduled_send_impl`, `execute_snooze_impl` | daemon/email scheduler | They run as unattended background jobs, not Agent tool invocations. |

The nested calls those helpers make are still covered by the trace owners
listed above where applicable. This classification is intentionally explicit:
if a future refactor registers one of the non-tool helpers as an Agent tool,
the audit test will require either a helper-owned trace or a documented public
wrapper trace.
