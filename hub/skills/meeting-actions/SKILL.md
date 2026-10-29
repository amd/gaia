---
name: meeting-actions
description: Pull every action item out of meeting notes, minutes or transcripts, including commitments made in passing ("I'll send it", "let me check"), with owner, due date and status, merged across meetings into one row per item, plus a separate log of decisions. Use when the user asks for action items, follow-ups, to-dos, owners, next steps, decisions, an action tracker, or what is still open from one or more meetings.
license: MIT
version: 1.0.0
metadata:
  gaia:
    security_tier: community
    tools_required:
      - list_files
      - read_file
      - run_python
      - write_file
    pairs_with:
      - transcribe-meeting
    provenance:
      source: starter-pack
---

# Meeting Actions

Most commitments are said in passing ("I'll ask finance", "let me check"),
not on lines labelled ACTION, and the same item comes back in later meetings
with a new status. A tracker built from labelled lines misses most of the
work and lists one item three times; one that also takes in every decision
buries the work in noise.

Deliver three things:

- **Action tracker**: things a person will do. One row per item.
- **Decision log**: things the meeting settled. Never in the tracker.
- **Questions for you**: open items that need an owner or a date.

## Hard rules

- **Commitment test**: a named person (or group) took on a specific future
  task. Someone, took it on, a task you could check off, still to do. Keep
  all three kinds:
  - explicit: "Action:", "TODO", "Sam to send the draft";
  - informal promises: "I'll", "let me check", "I can take that", "leave it
    with me", "we'll";
  - accepted requests: "Can you update the doc?" answered "Sure" or "Will
    do". The person who said yes owns it.

  Not actions: ideas ("we could"), questions nobody accepted, opinions,
  agenda items, and reports of finished work (a status update instead).
- **Decisions stay separate.** "Agreed", "decided", "approved" go in the
  decision log. "We decided to switch vendors" is a decision; "Dan will sign
  the new contract" is the action it creates. Log both.
- **Merge duplicates and chases.** The same deliverable with the same owner
  is one row across meetings and sources, however it is worded. "Chase Sam
  for the budget" is not a new action: it goes on Sam's row.
- **Status only from what was said.** `Done` needs a statement that it
  happened ("sent", "merged"). A plan or a date leaves it open. Never mark it
  done because time passed.
- **Never invent** an owner, a due date, a rationale or a role. Use
  `unassigned` or leave it blank, and ask.

## Procedure

1. Read every meeting in full: `list_files`, then `read_file`, following
   `next_offset` until it is null. Put the meetings in date order.
2. Find commitments and decisions with the test above, then run the cue scan
   as a safety net.
3. Fill each action: ID (`A-001`; never changed or reused, keep an existing
   tracker's IDs), Action (starts with a verb), Owner, Contributors, Due
   ("Friday (2024-05-10)"), Status, First raised, Latest, Evidence (date and
   a short quote). Fill each decision: ID (`D-001`), Decision, Date, Decided
   by, Rationale, Source.
4. Merge across meetings, run the duplicate check, and report the count of
   commitments before merging and of rows after.
5. Questions: open actions with owner `unassigned` ("needs owner") or no due
   date ("needs date"), and any important "someone should..." nobody took.
6. Deliver the tracker, decision log and questions; save with `write_file`
   if asked. Reopen the saved file and check every thing the user asked for,
   that no decision sits in the tracker, and that every ID is unique.

Example:

```
Dan: We could also look at a second vendor, maybe.      -> not an action (an idea)
Priya: OK, agreed: the onboarding doc moves to the wiki. -> decision D-001
Priya: Can you do the move, Mei?
Mei: Sure, I can take that.                              -> A-004, owner Mei, Open, needs date
```

## References (paths relative to this skill's folder)

- `references/commitments.md` - read when a line is borderline: hedged
  yeses, "someone should", "we" commitments, decisions that create work.
- `references/fields-and-status.md` - read when filling owner, due date,
  contributors, decision fields or status.
- `references/cue-scan.md` - read at step 2 for the script that flags
  commitment, decision and status lines in a folder of notes.
- `references/merge-and-views.md` - read at step 4 for the duplicate check,
  when the user asks who owes what or what is overdue, and for the final
  check.
- `references/worked-example.md` - read for a full two-meeting example with
  the expected tables.
