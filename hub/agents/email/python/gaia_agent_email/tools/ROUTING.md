# Chat-loop routing decisions (#2762 / #2764)

The four sense-making jobs #2762 defines are tested **by intent, never by
phrasing** — a job passes only when every unrehearsed way of asking it lands
on the same tool and the same surface. This table is the decision each job
was given; the system prompt (`agent.py`) and the tool docstrings below
implement it as intent classes and exclusion criteria, never as a growing
list of trigger phrases. If a routing bug shows up as "phrasing X doesn't
reach tool Y", the fix is to sharpen the exclusion criterion that misfired —
never to add phrasing X to a list.

| Job | What it means | Surface | Tool | Why |
|---|---|---|---|---|
| J1 — "what needs me" | Open-ended importance/urgency/attention question, including generic time-sensitivity that names no specific meeting/invite/deadline | **Card** | `pre_scan_inbox` | The card's `needs_you` worklist already folds in the waiting-on-you scan (#2743) plus action items — it is the fuller answer, not a fallback. |
| J2 — "what am I waiting on" | Sent mail nobody replied to (the OUTBOUND direction) | **Prose** | `check_followups` | Reference implementation (#2762): the tool precomputes `count` and the full row set — the model states it, never derives it. Unchanged by #2764. |
| J3 — "what's going on with X" | A named thread/person/topic | **Prose** | thread/search tools (`get_thread`, `search_messages`) | Out of scope for #2764 — grounding defects here are tracked under #2765. |
| J4 — "anything time-sensitive" | Explicit calendar language: meeting, invite, scheduled, deadline | **Prose** | `detect_calendar_conflicts` / calendar tools | Out of scope for #2764 — tracked under #2766 / #2778 / #2787. A generic "anything time-sensitive" with no calendar language stays J1 (the card), per #2764's acceptance criteria. |

## Direction is the disambiguator between J1 and J2

"What needs me" (J1) and "what am I waiting on" (J2) can be asked in
overlapping words — #2762 flags `J2.e` ("what's outstanding on my end?") as
*deliberately* ambiguous with J1. The two jobs are told apart by **direction
of the mail**, not by keyword:

- Mail the user **received** and hasn't acted on -> J1 -> `pre_scan_inbox`.
- Mail the user **sent** and hasn't been answered -> J2 -> `check_followups`.

Both tools' docstrings state this directly so the model can apply the rule
to any phrasing, rather than needing that phrasing enumerated in advance.

## `list_waiting_on_you` is not a J1 destination

`list_waiting_on_you` (`detect_waiting_on_you_impl`) runs the identical scan
`pre_scan_inbox`'s `needs_you` list already runs, at the same depth (#2743).
It is not a second router target for "what needs me" — it exists for a
caller that wants **only** that one slice re-run (a different `min_age_hours`
or scan depth after already seeing the card), or a non-chat caller (REST/MCP)
that has no card to render. A general "what needs me" / "anything urgent"
question always reaches `pre_scan_inbox`; `list_waiting_on_you` is never the
default answer to it.

## Exclusion criteria, not a phrase list

`pre_scan_inbox` is the **default** for any unscoped inbox-attention question.
A question only diverts to a narrower tool when it carries one of these
signals — checked structurally, not against a list of exact words:

1. Names the user's own **sent** mail / who owes them a reply -> `check_followups`.
2. Names a **specific person or topic** ("catch me up on the thread with
   Dana") -> thread/search tools.
3. Names **calendar-specific** language (meeting, invite, scheduled, RSVP,
   deadline tied to an event) -> calendar tools.
4. Explicitly asks about **flagged/suspicious/risky** mail only ->
   `check_suspicious_mail`.

Anything else — however the user phrases "what deserves my attention" —
reaches `pre_scan_inbox`.
