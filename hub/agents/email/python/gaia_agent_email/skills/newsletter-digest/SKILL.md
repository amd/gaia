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

Bulk mail is read in aggregate or not at all. The goal is one digest the user
can skim in under a minute, then an empty folder.

## Procedure

1. **Gather by shape, not by guess.** Use `search_messages` with the provider's
   own bulk categories and unsubscribe-bearing senders. Don't classify a
   message as a newsletter because its subject looks promotional.
2. **Summarise per source, not per message.** One line per sender covering
   everything they sent in the window. Five messages from one source become one
   line.
3. **Keep what is actually actionable.** A dated event, an expiring account
   notice, or a receipt is not digest material — pull it out and name it
   separately.
4. **Then clear.** Archive in batches with `archive_message_batch`, and label
   first if the user wants them findable later. Archiving is reversible;
   deleting is not — never delete bulk mail on your own initiative.

## Reporting

Open with the count and the window ("18 newsletters since Monday"). Then the
per-source lines, longest-standing first. End with anything you pulled out as
actionable, and say plainly what you archived.

## Judgement calls

- **A digest with more than about a dozen lines is a list, not a digest** —
  group harder.
- **Say nothing about a source that sent nothing worth a line.** Count it,
  don't describe it.
- **Never unsubscribe on the user's behalf** — surface the option and let them
  decide.
