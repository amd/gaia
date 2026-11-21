---
name: research-report
description: Research a topic on the open web and write a cited Markdown report. Use when the user asks for a report, a literature scan, a competitive landscape, or a "what's the current state of X" answer that needs sources.
license: MIT
version: 1.1.0
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
decomposition — a single search rarely answers a real research question — and
in every claim being traceable to a page you actually read.

## Procedure

1. **Decompose.** Restate the topic as 3–5 sub-questions that together answer it,
   in one line, then carry on. Wait for the user only if the topic is ambiguous.
2. **Search each sub-question** with `search_web(query, num_results=5)`. Use a
   different phrasing per sub-question; do not reuse the user's wording verbatim
   for all of them.
3. **Read the best sources.** For the 2–3 most promising results per
   sub-question, call `fetch_page(url)` to get the actual text.
   - Prefer primary sources: the organisation itself, the paper, the court or
     patent-office record, the regulation, the agency. An encyclopedia is a map
     to those sources, not a substitute for them — follow its references.
   - On a long page, read the part you need with `fetch_page(url, find="<word>")`
     instead of paging through it 5,000 characters at a time.
4. **Check what is newest.** Anything that can change — a ruling, a law's
   status, a version, a record — search once more for developments since your
   newest source (add the current year to the query). Report the latest status
   with its date; a proposal is not a law, and an appeal is not a final ruling.
5. **Write the report** with this shape:
   - A short answer to the original question, up front.
   - One section per sub-question.
   - Every factual claim followed by an inline `[source](url)` link to a page you
     fetched in this conversation. Never cite a page you only saw as a search
     result, and never write a URL you did not get from a tool.
   - Where sources disagree, say which says what, and which you trust and why.
   - A "What I could not confirm" section — say so when the web disagreed or
     went quiet. An honest gap beats a confident guess.
   - A **Sources** list at the end: each page you cited, once.
6. **Deliver the whole report in the reply**, citations included. If you also
   save it to a file, the reply still carries the full report, not a summary.

## When search is unavailable

`search_web` uses DuckDuckGo, which rate-limits automated searches. If it is
turned away, wait once as the error says. If it still refuses, fetch the
authoritative pages you know by URL (the organisation's own site, the
regulation, the paper) and say plainly in the report that web search was
unavailable, so the reader knows how it was sourced.

## When the answer must outlive the session

If the user wants to keep querying the material later, save each fetched page
with `write_file(file_path, content)` and `index_document(file_path)` it. After
that `query_documents` answers follow-ups from the indexed corpus instead of
re-fetching the web.

## Notes

- If a query comes back empty, rephrase once, then say so rather than inventing
  results.
- A "latest version" or "newest release" claim must come from the project's own
  releases or changelog page, fetched with `fetch_page`. On a version or release
  search, `search_web` lists the project's own pages first and marks them
  `[primary source]`; if none is, search for the releases page before answering,
  because news articles lag behind releases.

## Fork this

Point it at a domain and it becomes a specialist: change the sub-question
template in step 1 to your field's standard questions (for a competitor scan:
pricing, positioning, recent launches, public complaints), and pin step 2 to the
sites you trust.
