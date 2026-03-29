# Post-Restart Re-Eval Summary

## Scores
| Scenario | Original | Fix Phase | Post-Restart | Total Delta | Status |
|----------|----------|-----------|--------------|-------------|--------|
| concise_response | 7.15 | 7.00 | 4.17 | -2.98 | FAIL |
| negation_handling | 4.62 | 8.10 | 5.17 | +0.55 | FAIL |

## Fix Validation
- Fix 1 (basename fallback): **NOT VALIDATED** — Agent made 0 tool calls across all turns in the negation_handling scenario. The basename fallback in `query_specific_file` cannot be exercised if the agent never attempts a file query. Root cause: Fix 3 prevented document context from being surfaced, so the agent had no document IDs to query against.
- Fix 2 (verbosity / proportional response): **NOT VALIDATED** — The agent's failure mode was not verbose responses but zero RAG usage. Turn 1 of concise_response showed a concise greeting (evidence Fix 2 is syntactically active), but Turns 2–3 the agent answered from general knowledge entirely, making verbosity moot.
- Fix 3 (session isolation): **REGRESSION INTRODUCED** — After the server restart with Fix 3 fully active, `_resolve_rag_paths` appears to be returning `([], [])` even for sessions with documents correctly linked via `index_document(session_id=...)`. The agent receives no document context and falls back to pure LLM knowledge. In the fix_phase run (pre-restart, Fix 3 partially active), documents were still surfacing, yielding 7.00 and 8.10. Post-restart: 4.17 and 5.17. Hypothesis: Fix 3 changed the path where `document_ids` are populated and after a clean server restart (no warm cache) they are not being passed into the chat request payload correctly.

## Root Cause Analysis
All regressions traced to a single issue: **the agent never called any RAG tools in either scenario**. This is a new behavior post-restart that was not present in the original runs or the fix-phase runs. Session documents were confirmed indexed and linked (6 chunks for employee_handbook.md, 1 chunk for acme_q3_report.md), but the agent treated every query as a general knowledge question.

Likely code path to investigate:
- `src/gaia/ui/_chat_helpers.py` — `_resolve_rag_paths()` change in Fix 3
- `src/gaia/ui/routers/chat.py` — whether `document_ids` list is being populated from session before calling `_resolve_rag_paths`

## Remaining Failures (not yet fixed)
- smart_discovery: 2.80 — root cause: search_file doesn't scan eval/corpus/documents/
- table_extraction: 5.17 — root cause: CSV not properly chunked for aggregation
- search_empty_fallback: 5.32 — root cause: search returns empty, agent doesn't fall back
- **concise_response: 4.17 (NEW REGRESSION)** — Fix 3 broke session document surfacing
- **negation_handling: 5.17 (REGRESSION from 8.10)** — Fix 3 broke session document surfacing; Fix 1 unvalidatable

## Recommended Next Steps
1. **Urgent**: Investigate `_resolve_rag_paths` in `_chat_helpers.py` — verify that `document_ids` from linked sessions are being passed correctly to the resolver after the Fix 3 change
2. Re-run `concise_response` and `negation_handling` after the Fix 3 regression is resolved
3. Fix 1 (basename fallback) needs a new dedicated test where the agent is explicitly prompted to query a specific file by name, verifying the fallback resolves correctly
