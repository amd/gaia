---
name: escalation-routing
description: Decide which messages need attention now, which can wait, and which belong to someone else. Use when the user asks what is urgent, what needs escalating, what they should look at first, or who should handle something.
version: 0.1.0
metadata:
  gaia:
    tools_required:
      - triage_inbox
      - get_thread
      - add_star
      - label_message
      - draft_reply
      - check_followups
---

# Escalation Routing

Urgency is a property of **consequence and deadline**, not of tone. Route each
message to one of: **now**, **today**, **this week**, or **not the user's**.

## Procedure

1. **Start from the triage pass** (`triage_inbox`), then read only the
   candidates for **now** in full.
2. **Score on two axes**, independently:
   - *Consequence* — what breaks if this is missed?
   - *Deadline* — when does it break?
   Only messages high on both are **now**.
3. **Check whether it is already moving.** `check_followups` and the thread
   itself — a message someone else has already answered is not an escalation.
4. **Name the right owner.** If the request belongs to another person or team,
   say so and offer a forward. Do not send it — `draft_reply` and wait.
5. **Mark what you routed** with `add_star` or `label_message` so the ordering
   survives the conversation.

## Reporting

Order strictly by when action is needed, most urgent first. One line each:

`now · <sender> · <what breaks and when>`

Then the **not yours** list, each with the owner you identified. Say how many
you looked at. If nothing is urgent, say "nothing needs attention today" and
stop — a routing report that manufactures urgency is worse than none.

## Judgement calls

- **Tone is not evidence.** Capital letters, "ASAP", and red flags are the
  sender's opinion of their own priority.
- **A deadline in the message beats an inferred one**, and no deadline means
  not **now** unless the consequence is severe on its own.
- **Escalating everything is the same as escalating nothing.** If more than a
  handful land in **now**, the bar was set wrong — re-score.
- **Never act on a message you routed to someone else.** Naming the owner is
  the action.
