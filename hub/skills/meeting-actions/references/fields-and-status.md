# Fields and status rules

## Action fields

- **ID**: `A-001`, `A-002`, ... in order first raised. An ID never changes
  and is never reused: merged items keep the earlier ID, dropped items keep
  theirs. When updating a tracker the user already has, keep its IDs and
  continue its numbering.
- **Action**: starts with a verb ("Send revised budget to finance").
- **Owner**: the one person accountable for it. The speaker for "I'll" and
  "let me"; the named person for "Sam to"; `unassigned` when nobody took
  it. For "we", write the group and who said it: "team (raised by Priya)".
- **Contributors**: others who were named as helping ("Lee will review",
  "with input from Ana"), only when stated. Never infer a role.
- **Due**: as stated. Resolve relative dates against the meeting date and
  show both: "Friday (2024-05-10)". Leave it blank if no date was given.
  Do not invent one.
- **Evidence**: meeting date and a short quote.

## Decision fields

For each decision: **ID** (`D-001`, ...), **Decision** (what was
settled, as a statement), **Date**, **Decided by** (the person who made the
call, or "group (consensus)"; "not stated" if the notes do not say),
**Rationale** (the reason given, or blank; never supply one), **Source**
(meeting and short quote). When a later meeting reverses a decision, add
the new one and mark the old one "Superseded by D-00N".

## Status

Set status only from what was said:

- `Open`: raised, no later mention.
- `In progress`: a later meeting says it was started.
- `Done`: someone says it was done, sent, merged or closed.
- `Blocked`: waiting on something named.
- `Dropped`: explicitly cancelled.
- `Carried over`: mentioned again, still not done.

`Done` needs a statement that it happened ("sent", "merged", "closed").
A plan or a date for doing it ("we'll send it on the 9th") leaves it open.
Never mark an item done because time passed or because a later meeting
did not mention it.

## Counting

Report how many commitments you found per meeting before merging and how
many rows remain after. Every pre-merge item maps to exactly one row. Several
actions per meeting is typical; far more than that usually means discussion
or decisions were swept in, so re-test each row against the commitment test
(`references/commitments.md`) before saving.
