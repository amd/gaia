---
name: meeting-scheduling
description: Turn meeting requests in email into calendar decisions — accept, decline, or propose another time. Use when a message asks to meet, when the user asks about invitations, or when checking whether a proposed time is free.
version: 0.1.0
metadata:
  gaia:
    tools_required:
      - detect_meeting_request
      - detect_calendar_conflicts
      - list_calendar_events
      - create_event_from_email
      - accept_invite
      - decline_invite
      - draft_reply
---

# Meeting Scheduling

Every meeting request resolves to one of three answers: **yes**, **no**, or
**not then**. Get to one of the three; never leave it open.

## Procedure

1. **Confirm it is a request.** `detect_meeting_request` — an email mentioning a
   date is not necessarily asking for a meeting.
2. **Check the calendar before answering.** `detect_calendar_conflicts` for the
   proposed slot, and `list_calendar_events` for the surrounding day. A slot
   that is technically free but wedged between two other meetings is worth
   flagging.
3. **Pick the answer:**
   - Free and clearly relevant → recommend accepting.
   - Conflicting → offer the nearest genuinely free alternatives, at most three.
   - Not relevant to the user → recommend declining, with a one-line reason.
4. **Accepting or declining an invitation notifies the organiser** — always
   confirm with the user first. Same for a reply that proposes a new time:
   `draft_reply`, show it, wait.

## Reporting

State the request, the verdict, and the reason in that order:

> Thursday 14:00, 30 min, project sync — **conflicts** with an existing call at
> 14:00. Nearest free: Thu 15:30, Fri 10:00.

Include the time zone whenever the participants are not obviously in one.

## Judgement calls

- **A time range is not a time.** "Sometime Thursday" needs a proposal, not an
  acceptance.
- **Recurring requests need the whole series checked**, not just the first
  instance.
- **Working hours are a constraint, not a suggestion** — do not propose a slot
  outside the pattern the user's own calendar shows.
- **Never accept on the user's behalf** because the slot looks free.
