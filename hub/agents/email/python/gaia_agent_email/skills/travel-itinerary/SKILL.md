---
name: travel-itinerary
description: Assemble scattered booking confirmations into one chronological itinerary. Use when the user asks about an upcoming trip, their flight or hotel details, or wants an itinerary built from their email.
version: 0.1.0
metadata:
  gaia:
    tools_required:
      - search_messages
      - get_message
      - summarize_message
      - list_calendar_events
      - create_event_from_email
---

# Travel Itinerary

A trip arrives as a dozen unrelated confirmations. Produce one timeline, with
nothing invented.

- `search_messages` for confirmation-shaped mail in the trip window: flights,
  trains, lodging, car hire, tickets.
- Per confirmation, keep only: what it is, date and time, location, reference.
- Order by departure time, never by when the email arrived.
- Check `list_calendar_events` before offering to add anything — never duplicate
  a booking already on the calendar. Add only what the user confirms.

**Report** one line per segment, in time order:
`Tue 14 Mar · 08:40 · Flight · <origin> → <destination> · ref <code>`
Then, separately, what you could not resolve: a leg with no return, an overlap,
a night with no lodging. Name the gap; do not fill it.

Never invent a detail to complete the timeline. A later confirmation supersedes
an earlier one for the same reference. Times are local to each location — say
which zone when a segment crosses one. A cancelled booking is not a segment.
