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

- Scan first (`pre_scan_inbox`, then `triage_inbox`); never open messages one by
  one to build a picture.
- Buckets, in order: needs a reply · needs a decision · for information · noise.
- Open only what the category can't settle. Sender + subject usually decide it.
- Act on the reversible end only — mark read, star, label, archive. Reply, send,
  and delete are proposals the user confirms.

**Report** the reply-needed count first, then one line each: sender, what they
want, how old. Summarise the rest as counts. Nothing needing a reply is a
one-sentence answer.

Age beats volume. A thread the user already answered is handled. "URGENT" in a
subject line is a claim, not a fact.
