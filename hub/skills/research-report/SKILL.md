---
name: research-report
description: Research a topic on the open web and write a cited Markdown report. Use when the user asks for a report, a literature scan, a competitive landscape, or a "what's the current state of X" answer that needs sources.
license: MIT
version: 1.0.0
metadata:
  gaia:
    security_tier: community
    permissions:
      - network:read
    tools_required:
      - search_web
      - fetch_page
      - write_file
      - index_document
      - query_documents
    provenance:
      source: starter-pack
---

# Research Report

Turn one broad question into a sourced Markdown report. The value is in the
decomposition — a single search rarely answers a real research question.

## Procedure

1. **Decompose.** Restate the topic as 3–5 sub-questions that together answer it.
   Show them to the user before searching so they can redirect you early.
2. **Search each sub-question** with `search_web(query, num_results=5)`. Use a
   different phrasing per sub-question; do not reuse the user's wording verbatim
   for all of them.
3. **Read the best sources.** For the 2–3 most promising results per
   sub-question, call `fetch_page(url)` to get the actual text. Never cite a page
   you only saw as a search snippet.
4. **Write the report** with this shape:
   - One-paragraph answer to the original question, up front.
   - One section per sub-question.
   - Every non-obvious claim followed by an inline `[source](url)` link.
   - A "What I could not confirm" section — say so when the web disagreed or
     went quiet. An honest gap beats a confident guess.
5. **Deliver** as Markdown in the reply.

## When the answer must outlive the session

If the user wants to keep querying the material later, save each fetched page
with `write_file(file_path, content)` and `index_document(file_path)` it. After
that `query_documents` answers follow-ups from the indexed corpus instead of
re-fetching the web.

## Notes

- `search_web` uses DuckDuckGo. It is rate-limited and occasionally returns
  nothing; if a query comes back empty, rephrase once, then say so rather than
  inventing results.
- Prefer primary sources (documentation, filings, the project's own site) over
  aggregators.

## Fork this

Point it at a domain and it becomes a specialist: change the sub-question
template in step 1 to your field's standard questions (for a competitor scan:
pricing, positioning, recent launches, public complaints), and pin step 2 to the
sites you trust.
