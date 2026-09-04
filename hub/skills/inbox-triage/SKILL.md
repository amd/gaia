---
name: inbox-triage
description: Triage a mailbox — group what arrived, judge what actually needs the user, and say what to do about it. Use when asked to triage or check email, go through the inbox, summarise what came in, find what needs a reply, or work out which messages matter today.
license: MIT
version: 0.1.0
metadata:
  gaia:
    security_tier: community
    tools_required:
      - check_mailbox_access
      - list_inbox
      - search_email
      - read_email
      - list_mail_folders
    provenance:
      source: starter-pack
---

# Inbox triage

Turn a pile of mail into a short list of things that need the user, and a
one-line reason for each. The judging is yours — the tools return facts, not
verdicts.

## Read-only

Every tool here reads. Nothing archives, replies, deletes, or marks read. If the
user asks you to act on a message, say plainly that triage is read-only right
now and tell them what you would have done, so they can do it themselves.

Never claim to have taken an action you cannot take.

## Procedure

1. **Check access only if you need to.** If a mail tool fails, call
   `check_mailbox_access` and relay what it says. Do not open with it on a
   normal request — it costs a round trip and tells the user nothing new.

2. **List before reading.** `list_inbox` returns sender, subject, preview,
   received time, unread and flagged state for many messages in one call.
   `read_email` fetches one body. Start with the list; that is usually enough
   to sort most messages.

3. **Read only what you cannot judge from the preview.** A newsletter is
   obvious from its subject. A short note from a colleague asking a question
   may not be. Budget your reads: a handful, not the whole list.

4. **Sort into these five categories.** Every message gets exactly one:

   | Category | Means |
   |---|---|
   | `URGENT` | Time-critical and needs the user specifically. A deadline today, an outage, a blocked colleague. |
   | `NEEDS_RESPONSE` | A real person is waiting on a reply, but it is not on fire. |
   | `PERSONAL` | From a human, to this user, no action implied. |
   | `FYI` | Automated but genuine — build results, receipts, calendar notices, alerts the user opted into. |
   | `PROMOTIONAL` | Marketing, newsletters, cold outreach. Bulk mail nobody is waiting on. |

   When torn between `URGENT` and `NEEDS_RESPONSE`, ask whether something bad
   happens if it waits until tomorrow. If nothing does, it is
   `NEEDS_RESPONSE`.

   When torn between `FYI` and `PROMOTIONAL`, ask whether the user asked to
   receive it. A CI failure is `FYI`. A conference invitation is
   `PROMOTIONAL`.

5. **Lead with what needs them.** Report `URGENT` and `NEEDS_RESPONSE` first,
   as a short list, each with the sender and a one-line reason. Then give
   `FYI` and `PROMOTIONAL` as counts, not lists — "11 promotional, 4 FYI" is
   what the user wants, not eleven subject lines.

6. **Say when there is nothing.** An empty urgent list is a real and useful
   answer. Say "nothing needs you right now" and stop.

## Follow-ups

The triage pass ends at step 6. The remaining two tools are for what the user
asks next — do not reach for them during the pass itself.

`search_email` answers "anything else from Dana?" or "where is that invoice?"
It searches the whole mailbox, not just the inbox, so it finds messages that
were already filed and never appeared in your triage.

`list_mail_folders` explains a surprisingly empty inbox. If `list_inbox`
returned little and the user expected more, folder unread counts show where the
mail is actually landing. Report the counts and stop — do not start reading
other folders unless the user asks.

## Judgement

**Sender beats subject.** A message from a person the user actually works with
outranks a subject line that shouts. Marketing mail is engineered to look
urgent; a colleague writing "quick question" is often the thing that matters.

**A question mark aimed at the user is the strongest signal** that something is
`NEEDS_RESPONSE`. Check whether they are on the `to` line or merely `cc` — a cc
usually means they are being kept informed, not asked.

**Old unread mail is not automatically urgent.** If it has sat for two weeks and
nothing broke, it is not urgent now. Say it is old instead.

**Do not invent deadlines.** Only call something time-critical if a date or a
stated deadline is actually in the message. If you are inferring urgency from
tone, say so — "reads as urgent, but no date given".

## Reporting

Keep the whole answer short enough to read at a glance. A triage that takes as
long to read as the inbox has failed.

Give counts, not inventories, for anything that does not need the user. Group
several messages from the same sender into one line. Do not include message ids
unless the user asks — they are for your tool calls, not for them.

If a tool returned an error, say what failed and what the user should do. Never
present a partial inbox as if it were the whole one.
