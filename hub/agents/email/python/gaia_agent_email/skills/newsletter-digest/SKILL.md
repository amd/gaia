---
name: newsletter-digest
description: Condense newsletters, promotions, and other bulk mail into one short digest and clear them out. Use when the user asks what they missed in their subscriptions, wants a digest of bulk mail, or asks to clean up promotions.
version: 0.1.0
metadata:
  gaia:
    tools_required:
      - search_messages
      - summarize_message
      - summarize_thread
      - archive_message_batch
      - label_message_batch
---

# Newsletter Digest

Bulk mail is read in aggregate or not at all. Produce one digest the user can
skim in under a minute, then clear the folder.

- Gather by shape, not by guess: `search_messages` over the provider's bulk
  categories and unsubscribe-bearing senders.
- Summarise **per source**, one line covering everything they sent — five
  messages from one sender become one line.
- Pull out what is actually actionable (a dated event, an expiring notice, a
  receipt) and name it separately. That is not digest material.
- Then `archive_message_batch`; label first if the user wants them findable.

**Report** the count and window ("18 newsletters since Monday"), the per-source
lines oldest-first, then the actionable items and what you archived.

More than a dozen lines is a list, not a digest — group harder. Never delete
bulk mail and never unsubscribe on the user's behalf.
