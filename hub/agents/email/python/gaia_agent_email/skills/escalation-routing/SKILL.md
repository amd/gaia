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

Urgency is **consequence and deadline**, not tone. Route each message to **now**,
**today**, **this week**, or **not the user's**.

- Start from `triage_inbox`; read in full only the candidates for **now**.
- Score two axes independently: what breaks if this is missed, and when. Only
  high on both is **now**.
- Check it is not already moving (`check_followups`, the thread itself). A
  message someone else answered is not an escalation.
- If it belongs to another person or team, name them and offer a forward —
  `draft_reply`, never send. Mark what you routed with `add_star` / `label_message`
  so the ordering outlives the conversation.

**Report** strictly by when action is needed:
`now · <sender> · <what breaks and when>`
Then the **not yours** list with the owner you identified, and how many you
looked at. If nothing is urgent, say "nothing needs attention today" and stop.

Capitals and "ASAP" are the sender's opinion of their own priority. A stated
deadline beats an inferred one; no deadline means not **now** unless the
consequence is severe alone. Escalating everything is escalating nothing — if
more than a handful land in **now**, re-score. Naming the owner *is* the action.
