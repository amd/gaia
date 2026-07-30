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

Every request resolves to **yes**, **no**, or **not then**. Reach one of the
three; never leave it open.

- Confirm it is a request (`detect_meeting_request`) — a date in an email is not
  an invitation.
- Check before answering: `detect_calendar_conflicts` for the slot,
  `list_calendar_events` for the surrounding day. A free slot wedged between two
  calls is worth flagging.
- Free and relevant → recommend accepting. Conflicting → offer at most three
  genuinely free alternatives. Not the user's concern → recommend declining with
  a one-line reason.
- Accepting, declining, and proposing all notify the organiser: `draft_reply`,
  show it, wait. Never RSVP because the slot looked free.

**Report** request, verdict, reason — in that order. Include the time zone
whenever participants are not obviously in one.

A time range is not a time; "sometime Thursday" needs a proposal. Recurring
requests need the whole series checked. Do not propose a slot outside the working
pattern the user's own calendar shows.
