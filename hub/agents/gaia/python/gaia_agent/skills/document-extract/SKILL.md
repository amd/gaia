---
name: document-extract
description: Enumerate every requested item in long text documents or transcripts, with fields and source evidence. Use for exhaustive inventories such as every exercise, action item, or finding; not ordinary summaries, general knowledge lists, or code symbols.
version: 0.1.0
license: MIT
metadata:
  gaia:
    security_tier: community
    tools_required:
      - extract_document_items
      - save_extracted_items
      - read_file
---

# Exhaustive document extraction

1. Identify each source text file and the requested item type and fields. Use
   the user's current files; remembered summaries can help locate them but do
   not establish current contents or completeness.
2. Call `extract_document_items` once per source. It processes bounded chunks
   sequentially and retains source-backed entries. Do not replace this with a
   summary or manually calculated page offsets. When specifying fields in the
   tool call, pass `fields: ["name", "owner", "deadline"]` (the actual requested names).
3. If an extraction fails, report it as incomplete. Do not silently switch to
   a shorter list, a guessed count, or a script that has not been run.
4. If the user requested a saved inventory, use `save_extracted_items` for the
   exact destination. The exporter preserves all entries and provenance as
   JSON, CSV, or readable text, and the framework reads the file back and
   checks it; don't edit or regenerate it by hand.
5. The framework appends the full retained inventory to your final answer.
   Briefly state limitations or requested analysis; do not repeat or
   re-summarize the list.

The existing memory system records the action, source fingerprint, coverage,
count and final inventory unless the session is private. Use conversation
search for follow-ups about previous work; re-extract if the source or requested
fields change. Never treat a remembered summary as proof that all current
items were extracted, and don't store a whole private transcript as a durable
personal fact. A compact summary may supplement the inventory, not replace it.
