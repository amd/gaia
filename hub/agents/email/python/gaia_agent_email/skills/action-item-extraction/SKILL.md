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

An action item has an **owner**, a **verb**, and ideally a **date**. Anything
missing an owner and a verb is discussion, not an action item.

## Procedure

1. **Read the whole thread, not the last message.** `get_thread`, then
   `extract_action_items`. A commitment made early is often only confirmed
   later.
2. **Attribute every item to a named participant** — the user, or a specific
   other person. "The team will follow up" is not an action item; drop it or
   name who.
3. **Separate what the user owes from what the user is owed.** These get acted
   on completely differently.
4. **Check for existing tracking** with `list_tasks` and `check_followups`
   before proposing a new item, so the same commitment is not recorded twice.

## Reporting

Two lists, in this order, longest-overdue first:

**You owe** — `<verb the deliverable> · to <person> · by <date or "no date">`
**You're owed** — `<person> · <what> · by <date or "no date">`

Then, if anything qualifies, one short list of items whose deadline has passed.
No preamble, no restatement of the thread.

## Judgement calls

- **A question is not an action item** unless answering it was explicitly
  promised.
- **"No date" is a real value.** Never invent a deadline to make an item look
  complete — an undated commitment is exactly the one that gets dropped, and
  saying so is the useful output.
- **Superseded items are closed.** If a later message says a task moved or was
  dropped, that is the current state.
- **Conditional commitments stay conditional** — record the condition rather
  than the promise.
