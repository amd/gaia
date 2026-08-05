---
name: action-item-extraction
description: Pull the concrete commitments out of a thread — who owes what, by when. Use when the user asks what they need to do, what they promised, what is outstanding, or for action items from a conversation.
version: 0.1.0
metadata:
  gaia:
    tools_required:
      - extract_action_items
      - get_thread
      - summarize_thread
      - list_tasks
      - check_followups
---

# Action Item Extraction

An action item has an **owner**, a **verb**, and ideally a **date**. Without an
owner and a verb it is discussion, not an action item.

- Read the whole thread (`get_thread`, then `extract_action_items`) — a
  commitment made early is often only confirmed later.
- Attribute every item to a named person. "The team will follow up" is not an
  action item; name who, or drop it.
- Separate what the user owes from what the user is owed. They get handled
  completely differently.
- Check `list_tasks` and `check_followups` before proposing a new item, so the
  same commitment is not tracked twice.

**Report** two lists, longest-overdue first:
**You owe** — `<verb the deliverable> · to <person> · by <date or "no date">`
**You're owed** — `<person> · <what> · by <date or "no date">`
Then anything already past its deadline. No preamble.

A question is not an action item unless answering it was promised. "No date" is a
real value — never invent a deadline; the undated commitment is the one that gets
dropped, and saying so is the useful output. A superseded item is closed. A
conditional commitment keeps its condition.
