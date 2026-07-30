---
name: inbox-triage
description: Sort an inbox into what needs a reply, what needs a decision, and what is just noise. Use when the user asks to triage, catch up on, clean up, or make sense of their inbox.
version: 0.1.0
metadata:
  gaia:
    tools_required:
      - pre_scan_inbox
      - triage_inbox
      - get_message
      - archive_message
      - label_message
      - add_star
      - mark_read
---

# Inbox Triage

Triage answers one question per message: **does a human have to act on this?**

## Procedure

1. **Scan before reading.** `pre_scan_inbox` for the shape of the backlog,
   then `triage_inbox` for the categorised pass. Never open messages one by one
   to build a picture — that burns the context budget and misses the pattern.
2. **Sort into four buckets**, in this order:
   - **Needs a reply** — a person is waiting on the user specifically.
   - **Needs a decision** — no reply required, but an action or choice is.
   - **For information** — worth knowing, nothing owed.
   - **Noise** — bulk, automated, or already-handled mail.
3. **Open only what the category can't settle.** If the sender and subject
   already decide the bucket, don't fetch the body.
4. **Act on the safe end only.** Marking read, starring, labelling, and
   archiving are reversible — do them. Replying, sending, and deleting are not
   — propose those and let the user confirm.

## Reporting

Lead with the count that matters: how many need a reply. Then list them, one
line each — sender, what they want, and how old the request is. Summarise the
rest as counts, not lists. A triage report that lists 40 newsletters has buried
the two messages that mattered.

If nothing needs a reply, say exactly that in one sentence and stop.

## Judgement calls

- **Age beats volume.** A four-day-old direct question outranks twenty
  minutes-old bulk messages.
- **A thread the user already replied to is usually handled** — check before
  flagging it as waiting.
- **Never infer urgency from the sender's own words.** "URGENT" in a subject
  line is a claim, not a fact.
