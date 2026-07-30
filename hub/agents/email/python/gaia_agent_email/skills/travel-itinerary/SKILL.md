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

A trip arrives as a dozen unrelated confirmations from different senders. The
job is one timeline, in the traveller's local order, with nothing invented.

## Procedure

1. **Find the bookings.** `search_messages` for confirmation-shaped mail in the
   trip window: flights, trains, lodging, car hire, tickets.
2. **Read each confirmation once** and pull only: what it is, the date and time,
   the location, and the reference number.
3. **Order by departure time, not by when the email arrived.**
4. **Cross-check the calendar** with `list_calendar_events` before offering to
   add anything — a booking already on the calendar must not be duplicated.
5. **Offer, then add.** Use `create_event_from_email` only for entries the user
   confirms.

## Reporting

One line per segment, in time order:

`Tue 14 Mar · 08:40 · Flight · <origin> → <destination> · ref <code>`

Then, separately, anything you could not resolve: a segment with no return leg,
two bookings that overlap, a gap with no lodging. Name the gap; do not fill it.

## Judgement calls

- **Never invent a detail to complete the timeline.** A missing gate, terminal,
  or seat is reported as missing.
- **Later confirmations supersede earlier ones** for the same reference — a
  changed flight has two emails, and only the latest is true.
- **Times are the local time at each location** unless the confirmation says
  otherwise. Say which zone when a segment crosses one.
- **Cancellations count.** A cancelled booking must not appear as a live
  segment.
