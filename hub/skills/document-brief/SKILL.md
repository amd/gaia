---
name: document-brief
description: Index a document or folder and answer questions about it with quotes from the source. Use when the user points at a PDF, contract, spec, report, or folder of files and asks what it says, what changed, or what they should worry about.
license: MIT
version: 1.0.0
metadata:
  gaia:
    security_tier: community
    tools_required:
      - index_document
      - index_directory
      - list_indexed_documents
      - query_documents
      - summarize_document
      - rag_status
    provenance:
      source: starter-pack
---

# Document Brief

Reading a long document for someone is not summarization — it is answering the
question they actually have, with the sentence that proves it.

## Procedure

1. **Check what is already indexed** with `list_indexed_documents()`. If the
   file the user named is already there, skip straight to step 3 — re-indexing
   is slow and buys nothing.
2. **Index it.** `index_document(file_path)` for one file, or
   `index_directory(directory_path, recursive=True)` for a folder. If indexing
   fails, run `rag_status()` and report what it says — do not answer from the
   filename.
3. **Answer with `query_documents(query)`.** Ask one focused question at a time;
   a compound question retrieves worse than two simple ones.
4. **Quote the source.** Every claim in your answer carries the sentence or
   figure it came from. If retrieval returns nothing relevant, say "the document
   does not appear to cover that" — never fill the gap from general knowledge.
5. **Offer the brief.** For a document the user has not read at all, lead with
   `summarize_document(file_path)` and then invite specific questions.

## Standing brief format

When the user asks for "a brief" rather than a specific question:

- **What it is** — one line.
- **What matters** — the 3–5 points that would change a decision.
- **What to watch** — obligations, deadlines, numbers that move.
- **Open questions** — what the document leaves unresolved.

## Fork this

Replace the brief format with your domain's checklist and it becomes a reviewer:
for a contract, ask for termination, liability, auto-renewal, and payment terms
by name in step 3 so they are never skipped.
