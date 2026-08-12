---
name: check-in
description: Run a short personal check-in that picks up where the last one left off, following up on what the user said they would do and recording what changed. Use when the user says good morning, asks for a check-in, or asks what they were working on.
license: MIT
version: 1.0.0
metadata:
  gaia:
    security_tier: community
    tools_required:
      - recall
      - search_past_conversations
      - remember
      - update_memory
    provenance:
      source: starter-pack
---

# Check In

A check-in is worth having only if it remembers the last one. This is the
smallest skill in the pack that demonstrates the whole memory loop: recall
commitments, ask about them, write back what you learn.

## Procedure

1. **Recall open commitments.**
   `recall(category="reminder", limit=20)` — the things the user said they would
   do and has not closed out. `reminder` is the category for todos; there is no
   `task` category, and passing one silently matches nothing.
2. **Recall recent context.** `search_past_conversations(days=7, limit=10)` for
   what has actually been going on. Use it to make the greeting specific.
3. **Open with one concrete follow-up**, not a generic greeting:
   > Yesterday you said you'd finish the pricing deck. How did that go?
   If memory is empty, say so plainly and ask what they are working on — a
   fabricated callback is far worse than admitting a blank slate.
4. **Ask at most three questions.** A check-in that turns into an interrogation
   stops getting answered.
5. **Write back what changed.**
   - Done → `update_memory(knowledge_id=..., content="... — completed <date>")`
   - Still open → leave it, but note the new expected date
   - New → `remember(fact="<commitment> by <date>", category="reminder", due_at="<ISO date>")`
6. **Close with one line** on what you will ask about next time.

## Tone

Brief, specific, unsentimental. No performance of enthusiasm, no "you've got
this". Track the work, not the mood — unless the user brings up the mood, in
which case listen rather than advise.

## Honest limits

- **The user starts this, not GAIA.** A truly proactive daily check-in needs the
  scheduler to run skills on a cron, which is not wired yet (`--skill` is
  rejected when adding a schedule). Today this runs when invoked.
- Everything here depends on memory being enabled. With memory off there is no
  check-in to speak of — say so instead of running an empty version.
- Do not record anything the user marks private. If in doubt, ask before storing.

## Fork this

Point it at a team instead of a person: recall commitments per owner in step 1
and produce a standup digest in step 6.
